"""Concurrent orchestration for all configured evaluation agents."""

from __future__ import annotations

import json
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bailian_client import (
    BailianClient,
    api_error_code,
    is_context_length_error,
    is_non_retryable_api_error,
    retry_after_seconds,
)
from .config import AGENTS, EVALUATION_MAX_INPUT_CHARS, prompt_version
from .retry_policy import RetryPolicy, with_retry_feedback
from .validators import PipelineValidationResult, validate_pipeline_output


AGENT_SECTION_KEYWORDS = {
    "data_reliability": ("方法", "数据", "实验", "结果", "method", "data", "experiment", "result"),
    "ethics_bias": ("伦理", "数据", "方法", "限制", "ethic", "data", "method", "limitation"),
    "logical_rigor": ("摘要", "引言", "方法", "结果", "结论", "abstract", "introduction", "method", "result", "conclusion"),
    "innovation": ("摘要", "引言", "相关工作", "贡献", "结论", "abstract", "introduction", "related", "contribution", "conclusion"),
    "academic_impact": ("摘要", "引言", "结果", "结论", "参考文献", "abstract", "introduction", "result", "conclusion", "reference"),
}


def build_agent_payload(
    paper_data: dict[str, Any],
    agent: str,
    max_chars: int = EVALUATION_MAX_INPUT_CHARS,
) -> dict[str, Any]:
    """为各评价维度选择相关章节，避免五次重复发送整篇论文。"""
    payload = copy.deepcopy(paper_data)
    content = paper_data.get("content", {})
    if not isinstance(content, dict):
        return payload
    sections = content.get("sections", [])
    sections = [item for item in sections if isinstance(item, dict)]
    keywords = AGENT_SECTION_KEYWORDS.get(agent, ())
    selected = [
        section
        for section in sections
        if any(
            keyword in (
                str(section.get("section_category", ""))
                + " "
                + str(section.get("section_title", ""))
            ).lower()
            for keyword in keywords
        )
    ]
    if not selected and sections:
        selected = sections[:3]
        if len(sections) > 3:
            selected += sections[-2:]

    bounded_sections: list[dict[str, Any]] = []
    selected_text: list[str] = []
    selected_anchor_ids: set[str] = set()
    remaining = max_chars
    for section in selected:
        title = str(section.get("section_title") or "未命名章节")
        text = str(section.get("section_text") or "")
        if remaining <= 0:
            break
        excerpt = text[:remaining]
        bounded = copy.deepcopy(section)
        bounded["section_text"] = excerpt
        bounded_sections.append(bounded)
        selected_text.append(f"## {title}\n{excerpt}")
        selected_anchor_ids.update(str(value) for value in section.get("source_anchor_ids", []))
        remaining -= len(excerpt)

    if not selected_text:
        full_text = str(content.get("full_text") or "")
        selected_text = [full_text[:max_chars]]

    anchors = content.get("text_anchors", [])
    scoped_anchors = [
        copy.deepcopy(anchor)
        for anchor in anchors
        if isinstance(anchor, dict)
        and (not selected_anchor_ids or str(anchor.get("anchor_id")) in selected_anchor_ids)
    ][:500]
    references_text = str(content.get("references_text") or "")
    if agent not in {"academic_impact", "innovation", "logical_rigor"}:
        references_text = ""
    else:
        references_text = references_text[:8000]

    payload["content"] = {
        **content,
        "full_text": "\n\n".join(selected_text),
        "sections": bounded_sections,
        "references_text": references_text,
        "text_anchors": scoped_anchors,
    }
    review_context = payload.setdefault("review_context", {})
    if not isinstance(review_context, dict):
        review_context = {}
        payload["review_context"] = review_context
    review_context["input_scope"] = {
        "agent": agent,
        "selected_section_count": len(bounded_sections),
        "selected_text_chars": len(payload["content"]["full_text"]),
        "original_text_chars": len(str(content.get("full_text") or "")),
        "note": "输入已按评价维度筛选；结论只能依据所提供章节与结构化字段。",
    }
    return payload


@dataclass
class AgentRunResult:
    agent: str
    prompt_version: str
    status: str
    attempts: int
    validation: PipelineValidationResult
    raw_output_path: str = ""
    normalized_output_path: str = ""
    error_output_path: str = ""
    error_type: str = ""


class EvaluationOrchestrator:
    def __init__(
        self,
        *,
        client: BailianClient,
        prompts: dict[str, Path],
        output_root: Path | None = None,
        retry_policy: RetryPolicy | None = None,
        max_workers: int = 4,
        progress_callback: Any | None = None,
    ) -> None:
        self.client = client
        self.prompts = prompts
        self.output_root = output_root
        self.retry_policy = retry_policy or RetryPolicy()
        self.max_workers = max_workers
        self.progress_callback = progress_callback

    def _notify_progress(
        self,
        agent: str,
        message: str,
        *,
        status: str = "running",
        **detail: Any,
    ) -> None:
        """Send a best-effort agent event without affecting evaluation work."""
        if self.progress_callback is None:
            return
        try:
            self.progress_callback({
                "stage": "evaluation",
                "agent": agent,
                "status": status,
                "message": message,
                **detail,
            })
        except Exception:
            pass

    def evaluate_case(self, case_path: Path) -> tuple[str, dict[str, Any], list[AgentRunResult]]:
        case_data = json.loads(case_path.read_text(encoding="utf-8"))
        if not isinstance(case_data, dict):
            raise ValueError(f"业务数据顶层必须是 JSON object：{case_path}")
        case_id = case_path.stem
        return case_id, case_data, self.evaluate_data(case_data, case_id=case_id)

    def evaluate_data(self, case_data: dict[str, Any], *, case_id: str = "service_input") -> list[AgentRunResult]:
        """Evaluate an in-memory paper payload without requiring a case file."""
        if not isinstance(case_data, dict):
            raise TypeError("paper_data 必须是 dict")

        case_output_dir: Path | None = None
        if self.output_root is not None:
            case_output_dir = self.output_root / case_id
            case_output_dir.mkdir(parents=True, exist_ok=True)

        results: list[AgentRunResult] = []
        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        futures = {}
        try:
            futures = {
                executor.submit(self._run_agent, agent, prompt_path, case_id, case_data, case_output_dir): agent
                for agent, prompt_path in self.prompts.items()
            }
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                self._notify_progress(
                    result.agent,
                    (
                        f"{AGENTS.get(result.agent, result.agent)}评价完成，评分与证据结构校验通过"
                        if result.status == "success"
                        else f"{AGENTS.get(result.agent, result.agent)}评价未成功，系统将保留其他有效维度"
                    ),
                    status=result.status,
                    phase="completed",
                    attempts=result.attempts,
                )
        except BaseException:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

        results.sort(key=lambda item: list(AGENTS).index(item.agent) if item.agent in AGENTS else item.agent)
        return results

    def _run_agent(
        self,
        agent: str,
        prompt_path: Path,
        case_id: str,
        original_case_data: dict[str, Any],
        case_output_dir: Path | None,
    ) -> AgentRunResult:
        prompt = prompt_path.read_text(encoding="utf-8")
        version = prompt_version(prompt_path)
        agent_output_dir: Path | None = None
        if case_output_dir is not None:
            agent_output_dir = case_output_dir / agent
            agent_output_dir.mkdir(parents=True, exist_ok=True)

        api_failures = 0
        format_failures = 0
        quality_failures = 0
        agent_case_data = build_agent_payload(original_case_data, agent)
        case_data = agent_case_data
        agent_label = AGENTS.get(agent, agent)
        self._notify_progress(
            agent,
            f"{agent_label}引擎已启动，正在筛选相关章节与证据段落",
            phase="started",
        )
        last_validation = PipelineValidationResult()
        raw_output_path = ""
        normalized_output_path = ""
        error_output_path = ""
        error_type = ""

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            self._notify_progress(
                agent,
                (
                    f"{agent_label}正在提交结构化评价任务，生成评分、问题与证据引用"
                    if attempt == 1
                    else f"{agent_label}正在根据校验反馈执行第 {attempt} 次修正评价"
                ),
                phase="model_request",
                attempt=attempt,
            )
            case_text = json.dumps(case_data, ensure_ascii=False, indent=2)
            raw_path = agent_output_dir / f"{agent}_{version}_attempt{attempt:02d}.raw.txt" if agent_output_dir else None
            normalized_path = (
                agent_output_dir / f"{agent}_{version}_attempt{attempt:02d}.normalized.json"
                if agent_output_dir else None
            )
            error_path = agent_output_dir / f"{agent}_{version}_attempt{attempt:02d}.error.json" if agent_output_dir else None
            try:
                raw = self.client.complete_json(prompt, case_text)
                self._notify_progress(
                    agent,
                    f"{agent_label}模型响应已返回，正在校验输出结构、评分范围与证据完整性",
                    phase="validation",
                    attempt=attempt,
                )
                if raw_path is not None:
                    raw_path.write_text(raw, encoding="utf-8")
                    raw_output_path = str(raw_path)
            except Exception as exc:
                api_failures += 1
                error_type = api_error_code(exc)
                last_validation = PipelineValidationResult(errors=[f"API 调用失败[{error_type}]：{exc}"])
                if error_path is not None:
                    error_path.write_text(
                        json.dumps(
                            {"agent": agent, "attempt": attempt, "errors": last_validation.errors},
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    error_output_path = str(error_path)
                if is_non_retryable_api_error(exc):
                    break
                if api_failures <= self.retry_policy.api_retries:
                    if is_context_length_error(exc):
                        reduced_chars = max(
                            12000,
                            int(EVALUATION_MAX_INPUT_CHARS * (0.6 ** api_failures)),
                        )
                        agent_case_data = build_agent_payload(
                            original_case_data,
                            agent,
                            max_chars=reduced_chars,
                        )
                        case_data = agent_case_data
                        self._notify_progress(
                            agent,
                            f"{agent_label}输入超出当前模型上下文，已压缩到约 {reduced_chars} 字符后重试",
                            status="retrying",
                            phase="context_retry",
                            attempt=attempt,
                            errorType=error_type,
                        )
                        self.retry_policy.sleep_before_retry(attempt)
                        continue
                    self._notify_progress(
                        agent,
                        f"{agent_label}模型请求暂未完成，正在按退避策略重试",
                        status="retrying",
                        phase="api_retry",
                        attempt=attempt,
                        errorType=error_type,
                    )
                    self.retry_policy.sleep_before_retry(
                        attempt,
                        retry_after=retry_after_seconds(exc),
                    )
                    continue
                break

            last_validation = validate_pipeline_output(raw, agent, case_id, case_text)
            if last_validation.normalized_value is not None and normalized_path is not None:
                normalized_path.write_text(
                    json.dumps(last_validation.normalized_value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                normalized_output_path = str(normalized_path)
            if last_validation.errors and error_path is not None:
                error_path.write_text(
                    json.dumps(
                        {
                            "agent": agent,
                            "attempt": attempt,
                            "errors": last_validation.errors,
                            "warnings": last_validation.warnings,
                            "normalization_log": last_validation.normalization_log,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                error_output_path = str(error_path)

            if last_validation.ok:
                return AgentRunResult(
                    agent=agent,
                    prompt_version=version,
                    status="success",
                    attempts=attempt,
                    validation=last_validation,
                    raw_output_path=raw_output_path,
                    normalized_output_path=normalized_output_path,
                    error_output_path=error_output_path,
                )

            if not last_validation.parse_ok or not last_validation.fields_ok or not last_validation.enums_ok:
                format_failures += 1
                if format_failures <= self.retry_policy.format_retries:
                    self._notify_progress(
                        agent,
                        f"{agent_label}输出格式校验未通过，正在携带校验反馈重新生成",
                        status="retrying",
                        phase="format_retry",
                        attempt=attempt,
                    )
                    case_data = with_retry_feedback(
                        agent_case_data,
                        agent=agent,
                        reason="format_validation_failed",
                        problems=last_validation.errors,
                        attempt=attempt,
                        previous_output=raw,
                    )
                    continue
            else:
                quality_failures += 1
                if quality_failures <= self.retry_policy.quality_retries:
                    self._notify_progress(
                        agent,
                        f"{agent_label}证据完整性尚未达标，正在补充引用与论证",
                        status="retrying",
                        phase="quality_retry",
                        attempt=attempt,
                    )
                    case_data = with_retry_feedback(
                        agent_case_data,
                        agent=agent,
                        reason="quality_validation_failed",
                        problems=last_validation.errors,
                        attempt=attempt,
                        previous_output=raw,
                    )
                    continue
            break

        return AgentRunResult(
            agent=agent,
            prompt_version=version,
            status="failed",
            attempts=attempt,
            validation=last_validation,
            raw_output_path=raw_output_path,
            normalized_output_path=normalized_output_path,
            error_output_path=error_output_path,
            error_type=error_type,
        )
