# -*- coding: utf-8 -*-
"""
AI 学术审查系统审计 Agent 模块 (Audit Agent)
=========================================
质检中枢：负责对成功返回的评价 Agent 初步结果
进行"幻觉校验"和"跨引擎冲突仲裁"，输出清洗后的纯净数据。

职责边界：只清洗和纠偏数据，不计算最终总分或动态权重。
"""

import copy
import json
import time
import logging
import random
import re
from typing import Dict, Any, Optional, List, Tuple, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

NON_RETRYABLE_ERROR_MARKERS = (
    "insufficient_quota",
    "token-limit",
    "invalid_api_key",
    "authentication_error",
    "permission_denied",
)


def _is_non_retryable_api_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if any(marker in text for marker in NON_RETRYABLE_ERROR_MARKERS):
        return True
    return getattr(exc, "status_code", None) in {401, 402, 403}


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        try:
            return max(0.0, float(headers.get("retry-after")))
        except (TypeError, ValueError):
            return None
    return None


def _is_timeout_error(exc: Exception) -> bool:
    """Recognize SDK and gateway timeout variants without importing provider-specific errors."""
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    return "timeout" in name or "timed out" in text or "timeout" in text


def _is_compatibility_parameter_error(exc: Exception) -> bool:
    """Detect optional fields rejected by an OpenAI-compatible gateway."""
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    markers = (
        "response_format",
        "enable_thinking",
        "extra_body",
        "unsupported parameter",
        "unknown parameter",
        "unrecognized request argument",
    )
    return status in {400, 422} and any(marker in text for marker in markers)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class AuditInput:
    """审计 Agent 的标准化输入"""
    original_paper: Dict[str, Any]       # 原始论文结构化 JSON
    preliminary_reports: Dict[str, Any]  # 五个评价维度中的成功结果


@dataclass
class AuditOutput:
    """审计 Agent 的标准化输出"""
    audit_log: Dict[str, Any]
    audited_results: Dict[str, Any]
    raw_response: Optional[str] = None


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
你现在是 AI 学术审查系统的"首席审计官 (Audit Agent)"。
你将接收到两部分输入：
1. 【原始论文数据】：一份结构化的论文 JSON（包含章节文本、图表描述等）。
2. 【初步评价报告】：由评价引擎（方法论、逻辑、伦理、创新、学术影响力）成功生成的初步评价 JSON。

你的唯一职责是进行"事实核查"与"冲突仲裁"，确保最终数据的纯净和逻辑自洽。

请严格执行以下两项任务：
【任务 1：幻觉与假证据校验 (Fact-Checking)】
- 逐一核对本次成功返回的各引擎证据。验证这些证据是否在【原始论文数据】中真实存在。
- 若判定为"幻觉"，剔除该伪证并在日志中记录。

【任务 2：跨引擎冲突仲裁 (Conflict Resolution)】
- 检查本次成功返回的评价维度之间是否存在严重矛盾。
- 一旦发现冲突，基于【原始论文数据】进行二次推演，修正出错一方的评价内容。

【输出要求：只返回修改补丁，禁止重写完整报告】
你必须返回一个严格的 JSON 对象。没有发现问题时，`changes` 必须为空数组。
发现问题时，只列出确实需要修改的最小字段路径；没有列出的字段将由系统从原报告完整保留。

`path` 使用点分路径，例如 `issues.0.evidence`、`score`、`evidence_refs.1.quote`。
`operation` 只能是：
- `replace`：替换已经存在的字段，必须提供 `value`；
- `remove`：删除错误字段或列表项；
- `add`：增加字段，向列表末尾增加项目时使用 `issues.-`。

格式：
{
  "approved": true,
  "audit_log": {
    "fact_check_summary": "描述发现的幻觉和处理方式",
    "conflict_resolution_summary": "描述发现的冲突和仲裁结果"
  },
  "changes": [
    {
      "engine": "methodology",
      "path": "issues.0.evidence",
      "operation": "replace",
      "value": "经原文核验后的证据",
      "reason": "原证据与论文原文不一致"
    }
  ]
}

规则：
- `changes` 最多 30 项，只修改确有幻觉或严重跨维度冲突的字段。
- 禁止输出 `audited_results`，禁止复制未修改的原报告。
- 如果现有证据足以完成核验和修正，`approved` 必须为 true。
- 只有输入证据不足以完成可靠复核时才返回 `approved: false`，并在 audit_log 中说明原因。
"""

# 最终审核只接受完整五维输入；缺失维度必须先由评价阶段定向重审。
ENGINE_KEYS = ("methodology", "logic", "ethics", "innovation", "academic_impact")


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class AuditAgent:
    """
    AI 学术审查系统审计 Agent

    负责对五个评价维度的成功输出进行：
      1. 幻觉 / 假证据校验 (Fact-Checking)
      2. 跨引擎冲突仲裁 (Conflict Resolution)

    使用方式:
        agent = AuditAgent(api_key="...", base_url="...", model="...")
        result = agent.audit(
            original_paper=paper_json,
            preliminary_reports=reports_json,
        )
    """

    # ---------- 默认配置 ----------
    DEFAULT_MODEL = "qwen-plus-latest"
    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DEFAULT_MAX_RETRIES = 2
    DEFAULT_TEMPERATURE = 0.0          # 审计任务需要确定性
    DEFAULT_TIMEOUT = 180              # 秒；补丁输出通常远低于此值，避免长请求被重复计算
    DEFAULT_MAX_OUTPUT_TOKENS = 4096
    DEFAULT_COMPACT_MAX_TOKENS = 2048
    COMPACT_AUDIT_PROMPT = """你是 JSON 审计器。只返回一个 JSON 对象，不要 Markdown，不要解释。
请根据以下已生成的五维评价摘要判断是否存在明显冲突。
只返回 JSON，字段必须为：
audit_status, consistency_passed, summary, blocking_issues, corrections, confidence
如果无法判断，返回 audit_status="insufficient" 和 consistency_passed=false。"""

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: int = DEFAULT_TIMEOUT,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        system_prompt: Optional[str] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        """
        初始化审计 Agent。

        Parameters
        ----------
        api_key : str
            LLM API 密钥。
        base_url : str, optional
            API 端点地址，默认 OpenAI。
        model : str, optional
            模型名称，默认 qwen-plus-latest。
        max_retries : int
            最大重试次数，默认 2；长时间运行后超时不会重新提交完整任务。
        temperature : float
            采样温度，默认 0.0（确定性输出）。
        timeout : int
            单次请求超时（秒），默认 180。
        max_output_tokens : int
            补丁式复核的最大输出长度，默认 1800 tokens。
        system_prompt : str, optional
            自定义 System Prompt；不传则使用内置提示词。
        """
        self.api_key = api_key
        self.base_url = self._normalize_base_url(base_url or self.DEFAULT_BASE_URL)
        self.model = model or self.DEFAULT_MODEL
        self.max_retries = max_retries
        self.temperature = temperature
        self.timeout = timeout
        self.max_output_tokens = max(512, min(int(max_output_tokens), 8192))
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.progress_callback = progress_callback

        # 惰性导入 openai，避免未安装时导入失败
        self._client = None
        self.last_attempts = 0
        self.last_metrics: Dict[str, Any] = {}

    def _notify_progress(self, message: str, status: str = "running", **detail: Any) -> None:
        """Best-effort audit progress; reporting must never interrupt the audit itself."""
        if self.progress_callback is None:
            return
        try:
            self.progress_callback({
                "stage": "audit",
                "agent": "audit",
                "message": message,
                "status": status,
                **detail,
            })
        except Exception:
            logger.warning("Audit progress callback failed", exc_info=True)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_base_url(raw: str) -> str:
        """Strip trailing /chat/completions so the OpenAI SDK can append its own path."""
        import re
        url = raw.rstrip("/")
        url = re.sub(r"/chat/completions$", "", url)
        url = re.sub(r"/completions$", "", url)
        return url

    def audit(
        self,
        original_paper: Dict[str, Any],
        preliminary_reports: Dict[str, Any],
    ) -> AuditOutput:
        """
        执行审计。

        Parameters
        ----------
        original_paper : dict
            原始论文的结构化 JSON（包含章节文本、图表描述等）。
        preliminary_reports : dict
            完整五维初步评价结果，必须同时包含 methodology / logic / ethics /
            innovation / academic_impact。

        Returns
        -------
        AuditOutput
            包含 audit_log 与 audited_results 的审计结果。
        """
        # 1. 输入校验
        self._notify_progress(
            "正在校验五维评价字段、评分范围与复核输入完整性",
            phase="input_validation",
            progressKey="audit-input-validation",
        )
        self._validate_input(preliminary_reports)
        self._notify_progress(
            "复核输入校验完成，五维评价数据可用于一致性审查",
            status="success",
            phase="input_validation_complete",
            progressKey="audit-input-validation-complete",
        )

        # 2. 构建用户消息
        self._notify_progress(
            "正在压缩论文上下文，并保留被评价结果引用的原文证据",
            phase="payload_build",
            progressKey="audit-payload-build",
        )
        user_message = self._build_user_message(original_paper, preliminary_reports)
        self._notify_progress(
            "复核材料已完成压缩与结构化，准备提交模型服务",
            status="success",
            phase="payload_ready",
            progressKey="audit-payload-ready",
        )

        # 3. 调用 LLM（含重试）
        raw_response = self._call_llm_with_retry(user_message)

        # 4. 解析响应（v5.1: 解析失败时尝试 compact retry）
        parsed = None
        try:
            parsed = self._parse_response(raw_response)
        except ValueError as parse_error:
            logger.warning("AuditAgent parse failed, attempting compact retry: %s", parse_error)
            self._notify_progress(
                "复核响应解析失败，正在使用精简模式重新提交",
                status="retrying",
                phase="compact_retry",
                progressKey="audit-compact-retry",
            )
            try:
                compact_message = self._build_compact_message(original_paper, preliminary_reports)
                raw_compact = self._call_llm_compact(compact_message)
                parsed = self._parse_response(raw_compact)
            except Exception as compact_error:
                logger.warning("AuditAgent compact retry also failed: %s", compact_error)
                parsed = self._build_fallback_audit(str(compact_error))
        self._notify_progress(
            "复核响应结构校验通过，正在合并经复核的五维评价结果",
            status="success",
            phase="response_validated",
            progressKey="audit-response-validated",
        )

        # 5. 后处理：确保 audited_results 不丢失原始字段
        is_fallback = parsed.get("audit_status") == "fallback"
        if "changes" in parsed and not is_fallback:
            if parsed.get("approved") is not True:
                raise ValueError("复核模型未批准本次结果，禁止发布未经可靠复核的报告")
            audited_results = self._apply_changes(
                preliminary_reports,
                parsed.get("changes"),
            )
        elif is_fallback:
            # v5.1: fallback — keep originals unchanged
            audited_results = dict(preliminary_reports)
        else:
            # 兼容旧模型偶发返回的完整 audited_results；新提示词不再要求这种高开销格式。
            audited_results = self._merge_with_originals(
                preliminary_reports, parsed.get("audited_results", {})
            )
        self._notify_progress(
            "复核修订已合并，正在生成最终审查报告",
            status="success",
            phase="merge_complete",
            progressKey="audit-merge-complete",
        )

        audit_log = parsed.get("audit_log", {})
        if not isinstance(audit_log, dict):
            audit_log = {"summary": str(audit_log)}
        audit_log = {
            **audit_log,
            "output_mode": "patch" if "changes" in parsed else "legacy_full",
            "change_count": len(parsed.get("changes") or []) if "changes" in parsed else None,
        }
        self.last_metrics.update({
            "output_mode": audit_log["output_mode"],
            "change_count": audit_log["change_count"],
        })
        return AuditOutput(
            audit_log=audit_log,
            audited_results=audited_results,
            raw_response=raw_response,
        )

    # ------------------------------------------------------------------
    # LLM 调用
    # ------------------------------------------------------------------

    def _get_client(self):
        """惰性初始化 OpenAI 客户端。"""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "请先安装 openai 库: pip install openai"
                )
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=0,
            )
        return self._client

    def _call_llm_with_retry(self, user_message: str) -> str:
        """
        调用大模型 API，失败时自动重试。

        重试策略：指数退避 (1s → 2s → 4s …)，最多 max_retries 次。
        """
        client = self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            self.last_attempts = attempt
            attempt_started = time.monotonic()
            try:
                self._notify_progress(
                    f"一致性复核第 {attempt}/{self.max_retries} 次请求已发送，正在核对评分、证据真实性与跨引擎冲突",
                    attempt=attempt,
                    maxAttempts=self.max_retries,
                    phase="request",
                    progressKey=f"audit-request-{attempt}",
                )
                logger.info(
                    "AuditAgent 调用 LLM (第 %d/%d 次) model=%s max_tokens=%d thinking=false",
                    attempt, self.max_retries, self.model, self.max_output_tokens,
                )
                request = dict(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_output_tokens,
                    response_format={"type": "json_object"},
                )
                if self.model.lower().startswith("qwen"):
                    request["extra_body"] = {"enable_thinking": False}
                try:
                    response = client.chat.completions.create(**request)
                except Exception as compatibility_exc:
                    if not _is_compatibility_parameter_error(compatibility_exc):
                        raise
                    logger.info(
                        "复核网关不支持可选 JSON 参数，改用通用 OpenAI 兼容字段重试 model=%s",
                        self.model,
                    )
                    request.pop("response_format", None)
                    request.pop("extra_body", None)
                    response = client.chat.completions.create(**request)

                choices = getattr(response, "choices", None) or []
                if not choices:
                    raise ValueError("LLM 返回中没有 choices")
                message = choices[0].message
                content = getattr(message, "content", None)
                # v5.1: deepseek may return reasoning_content but empty content
                reasoning = getattr(message, "reasoning_content", None)
                if isinstance(content, list):
                    content = "".join(
                        str(part.get("text", "")) if isinstance(part, dict) else str(part)
                        for part in content
                    )
                if (content is None or (isinstance(content, str) and content.strip() == "")):
                    if reasoning and isinstance(reasoning, str) and reasoning.strip():
                        logger.warning(
                            "AuditAgent response: content is empty but reasoning_content has %d chars",
                            len(reasoning),
                        )
                    # Don't raise — let _parse_response handle empty string
                    content = str(content or "").strip()
                content = str(content)
                elapsed = time.monotonic() - attempt_started
                usage = getattr(response, "usage", None)
                self.last_metrics.update({
                    "request_seconds": round(elapsed, 3),
                    "output_chars": len(content),
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                })
                logger.info(
                    "AuditAgent LLM 调用成功 elapsed=%.2fs output_chars=%d prompt_tokens=%s completion_tokens=%s",
                    elapsed,
                    len(content),
                    getattr(usage, "prompt_tokens", None),
                    getattr(usage, "completion_tokens", None),
                )
                self._notify_progress(
                    "一致性复核响应已返回，正在解析校验记录并合并修订结果",
                    status="success",
                    attempt=attempt,
                    maxAttempts=self.max_retries,
                    phase="response",
                    progressKey=f"audit-response-{attempt}",
                )
                return content

            except Exception as exc:
                last_error = exc
                attempt_elapsed = time.monotonic() - attempt_started
                logger.warning(
                    "AuditAgent 调用失败 (第 %d 次, elapsed=%.2fs): %s",
                    attempt, attempt_elapsed, exc,
                )
                if _is_non_retryable_api_error(exc):
                    logger.error("不可恢复的模型服务错误，停止重试")
                    self._notify_progress(
                        "一致性复核请求遇到不可重试的模型服务错误，将保留原始评价结果",
                        status="failed",
                        attempt=attempt,
                        maxAttempts=self.max_retries,
                        errorType=exc.__class__.__name__,
                        phase="failed",
                        progressKey=f"audit-failed-{attempt}",
                    )
                    break
                if _is_timeout_error(exc) and attempt_elapsed >= 30:
                    logger.warning(
                        "复核请求已运行 %.1f 秒后超时，将按剩余重试次数恢复",
                        attempt_elapsed,
                    )
                    self._notify_progress(
                        f"复核请求等待 {int(attempt_elapsed)} 秒后超时，将使用相同模型进行容错重试",
                        status="retrying" if attempt < self.max_retries else "failed",
                        attempt=attempt,
                        maxAttempts=self.max_retries,
                        errorType=exc.__class__.__name__,
                        phase="long_timeout",
                        progressKey=f"audit-long-timeout-{attempt}",
                    )
                if attempt < self.max_retries:
                    retry_after = _retry_after_seconds(exc)
                    sleep_seconds = retry_after if retry_after is not None else 2 ** (attempt - 1)
                    sleep_seconds += random.uniform(0.0, 0.5)
                    logger.info("将在 %.1f 秒后重试...", sleep_seconds)
                    self._notify_progress(
                        f"第 {attempt} 次复核请求失败，将在 {sleep_seconds:.1f} 秒后进行最后重试",
                        status="retrying",
                        attempt=attempt,
                        maxAttempts=self.max_retries,
                        errorType=exc.__class__.__name__,
                        phase="backoff",
                        progressKey=f"audit-backoff-{attempt}",
                    )
                    time.sleep(sleep_seconds)
                else:
                    logger.error("AuditAgent 已达最大重试次数，抛出异常")
                    self._notify_progress(
                        f"一致性复核连续 {self.max_retries} 次未连接成功，将保留五维原始评价并继续生成报告",
                        status="failed",
                        attempt=attempt,
                        maxAttempts=self.max_retries,
                        errorType=exc.__class__.__name__,
                        phase="fallback",
                        progressKey="audit-fallback",
                    )

        raise RuntimeError(
            "LLM 调用失败。"
            f"最后一次错误: {last_error}"
        )

    # v5.1: compact retry for failed full audit
    def _call_llm_compact(self, user_message: str) -> str:
        """最小化 JSON 复核——仅判断是否存在明显冲突"""
        client = self._get_client()
        self._notify_progress(
            "一致性复核紧凑重试已发送",
            phase="compact_request",
            progressKey="audit-compact-request",
        )
        request_kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": self.COMPACT_AUDIT_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            max_tokens=self.DEFAULT_COMPACT_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
        if not self.model.lower().startswith("qwen"):
            request_kwargs.pop("response_format", None)
        response = client.chat.completions.create(**request_kwargs)
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ValueError("compact retry: 无 choices")
        content = getattr(choices[0].message, "content", "") or ""
        if isinstance(content, list):
            content = "".join(str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in content)
        return str(content)

    def _build_compact_message(self, original_paper, preliminary_reports):
        """极简复核消息——仅送各维度 score + summary"""
        scores = {}
        for eng, report in preliminary_reports.items():
            if isinstance(report, dict):
                scores[eng] = {
                    "score": report.get("score"),
                    "summary": str(report.get("summary") or report.get("core_conclusion") or "")[:200],
                }
        return json.dumps({"scores": scores}, ensure_ascii=False)

    @staticmethod
    def _build_fallback_audit(error_msg: str) -> dict:
        """当所有 LLM 复核尝试均失败时，生成降级审计结果"""
        return {
            "audit_status": "fallback",
            "consistency_passed": False,
            "summary": "最终一致性复核未完成：AuditAgent 未返回可解析结果。系统保留多引擎初审结果，建议重新审查以获得终审核准版本。",
            "blocking_issues": [{"type": "audit_agent_parse_failure", "message": error_msg[:200], "severity": "medium"}],
            "corrections": [],
            "confidence": 0.0,
            "audit_log": {
                "fact_check_summary": "复核未完成：LLM 响应解析失败。",
                "conflict_resolution_summary": "未执行冲突仲裁。",
            },
        }

    # ------------------------------------------------------------------
    # 消息构建
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        original_paper: Dict[str, Any],
        preliminary_reports: Dict[str, Any],
    ) -> str:
        """将双 JSON 组装为 LLM 可读的用户消息。"""
        compact_paper = self._compact_paper_context(original_paper, preliminary_reports)
        compact_reports = self._compact_reports_for_audit(preliminary_reports)
        paper_str = json.dumps(compact_paper, ensure_ascii=False, separators=(",", ":"))
        reports_str = json.dumps(compact_reports, ensure_ascii=False, separators=(",", ":"))
        logger.info(
            "AuditAgent payload prepared paper_chars=%d report_chars=%d total_chars=%d",
            len(paper_str), len(reports_str), len(paper_str) + len(reports_str),
        )
        self.last_metrics.update({
            "paper_chars": len(paper_str),
            "report_chars": len(reports_str),
            "input_chars": len(paper_str) + len(reports_str),
        })

        message = f"""
请对以下论文的初步评价进行审计。

=== 原始论文数据 ===
{paper_str}

=== 初步评价报告 ===
{reports_str}

请严格按照 System Prompt 中的要求，返回严格的 JSON 对象。
"""
        return message

    @staticmethod
    def _compact_reports_for_audit(preliminary_reports: Dict[str, Any]) -> Dict[str, Any]:
        """Keep facts needed for auditing while omitting prose that will never be rewritten."""
        audit_fields = (
            "agent_name",
            "dimension_name",
            "score",
            "confidence",
            "risk_level",
            "summary",
            "core_conclusion",
            "issues",
            "evidence_refs",
            "journal_recommendation",
        )
        compact: Dict[str, Any] = {}
        for engine, report in preliminary_reports.items():
            if not isinstance(report, dict):
                continue
            projected = {
                key: report[key]
                for key in audit_fields
                if key in report
            }
            reasoning = report.get("reasoning_md")
            if isinstance(reasoning, str) and reasoning.strip():
                projected["reasoning_md"] = reasoning[:1200]
            compact[engine] = AuditAgent._shrink_json_value(projected)
        return compact

    @staticmethod
    def _shrink_json_value(value: Any, depth: int = 0) -> Any:
        """Keep audit JSON valid while bounding verbose reasoning and repeated lists."""
        if depth > 8:
            return "[层级过深，已省略]"
        if isinstance(value, str):
            return value if len(value) <= 3000 else value[:3000] + "…[已截断]"
        if isinstance(value, list):
            return [AuditAgent._shrink_json_value(item, depth + 1) for item in value[:30]]
        if isinstance(value, dict):
            return {
                str(key): AuditAgent._shrink_json_value(item, depth + 1)
                for key, item in value.items()
                if key not in {"raw_response", "debug", "traceback"}
            }
        return value

    @staticmethod
    def _compact_paper_context(
        original_paper: Dict[str, Any],
        preliminary_reports: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build evidence-focused context instead of sending every PDF coordinate and full text."""
        referenced_anchor_ids: set[str] = set()

        def collect_anchor_ids(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"anchor_id", "block_id"} and isinstance(item, str):
                        referenced_anchor_ids.add(item)
                    elif key == "source_anchor_ids" and isinstance(item, list):
                        referenced_anchor_ids.update(str(anchor) for anchor in item if anchor)
                    collect_anchor_ids(item)
            elif isinstance(value, list):
                for item in value:
                    collect_anchor_ids(item)

        collect_anchor_ids(preliminary_reports)
        content = original_paper.get("content") if isinstance(original_paper.get("content"), dict) else {}
        paper_info = original_paper.get("paper_info") if isinstance(original_paper.get("paper_info"), dict) else {}
        anchors = content.get("text_anchors") if isinstance(content.get("text_anchors"), list) else []
        referenced_anchors = []
        referenced_anchor_chars = 0
        for anchor in anchors:
            if not isinstance(anchor, dict):
                continue
            anchor_id = str(anchor.get("anchor_id") or "")
            if anchor_id and anchor_id in referenced_anchor_ids and referenced_anchor_chars < 12000:
                anchor_text = str(anchor.get("text") or anchor.get("text_preview") or "")
                anchor_text = anchor_text[:min(1200, 12000 - referenced_anchor_chars)]
                referenced_anchors.append({
                    "anchor_id": anchor_id,
                    "page": anchor.get("page"),
                    "paragraph_index": anchor.get("paragraph_index"),
                    "text": anchor_text,
                })
                referenced_anchor_chars += len(anchor_text)

        compact_sections = []
        section_chars = 0
        sections = content.get("sections") if isinstance(content.get("sections"), list) else []
        for section in sections:
            if not isinstance(section, dict) or section_chars >= 18000:
                continue
            section_text = str(section.get("section_text") or section.get("content") or "")
            remaining = 18000 - section_chars
            section_text = section_text[:min(3200, remaining)]
            if not section_text:
                continue
            compact_sections.append({
                "section_title": section.get("section_title") or section.get("title"),
                "section_category": section.get("section_category"),
                "section_text": section_text,
            })
            section_chars += len(section_text)

        return {
            "paper_info": AuditAgent._shrink_json_value(paper_info),
            "abstract": str(content.get("abstract") or "")[:3000],
            "keywords": AuditAgent._shrink_json_value(content.get("keywords") or []),
            "referenced_text_anchors": referenced_anchors[:60],
            "sections": compact_sections,
            "context_note": "为降低模型网关超时，本输入省略了整篇 full_text 与未被评价引用的坐标锚点。",
        }

    # ------------------------------------------------------------------
    # 解析与后处理
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(raw: str) -> Dict[str, Any]:
        """
        解析 LLM 返回的 JSON 字符串。
        v5.1: 兼容空输出、markdown 包裹、混合文本。
        """
        text = (raw or "").strip()
        if not text:
            raise ValueError("AuditAgent returned empty response")

        # 去除可能的 markdown 代码块包裹
        for fence in ("```json", "```"):
            if text.startswith(fence):
                first_newline = text.find("\n")
                if first_newline != -1:
                    text = text[first_newline + 1:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                break

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # v5.1: 尝试提取第一个 JSON 对象 { ... }
        brace_start = text.find("{")
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        extracted = text[brace_start:i + 1]
                        try:
                            return json.loads(extracted)
                        except json.JSONDecodeError:
                            break

        logger.error("JSON 解析失败: 原始内容前500字符: %s", raw[:500])
        raise ValueError(f"LLM 返回的内容无法解析为 JSON")

    @staticmethod
    def _validate_input(preliminary_reports: Dict[str, Any]) -> None:
        """Only a complete five-dimension result may enter final audit."""
        if not preliminary_reports:
            raise ValueError("preliminary_reports 不能为空")
        unknown = [key for key in preliminary_reports if key not in ENGINE_KEYS]
        if unknown:
            raise ValueError(f"preliminary_reports 包含未知引擎: {unknown}")
        missing = [key for key in ENGINE_KEYS if key not in preliminary_reports]
        if missing:
            raise ValueError(f"最终审核缺少评价维度: {missing}")
        invalid = [key for key, value in preliminary_reports.items() if not isinstance(value, dict)]
        if invalid:
            raise TypeError(f"引擎输出必须为 object: {invalid}")

    @staticmethod
    def _patch_path_parts(path: str) -> List[str | int]:
        if not isinstance(path, str) or not path.strip() or len(path) > 240:
            raise ValueError("补丁 path 必须是非空的短字符串")
        parts: List[str | int] = []
        for raw_part in path.split("."):
            if raw_part == "-":
                parts.append(raw_part)
            elif raw_part.isdigit():
                parts.append(int(raw_part))
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw_part):
                parts.append(raw_part)
            else:
                raise ValueError(f"补丁 path 包含非法片段: {raw_part!r}")
        if len(parts) > 10:
            raise ValueError("补丁 path 层级过深")
        return parts

    @staticmethod
    def _apply_changes(
        originals: Dict[str, Any],
        changes: Any,
    ) -> Dict[str, Any]:
        """Apply a bounded, validated patch list to a deep copy of preliminary reports."""
        if not isinstance(changes, list):
            raise TypeError("复核结果 changes 必须为数组")
        if len(changes) > 30:
            raise ValueError("复核补丁超过 30 项，拒绝异常的大规模重写")

        protected_roots = {
            "agent_name",
            "dimension_name",
            "status",
            "attempts",
            "prompt_version",
            "errors",
            "warnings",
            "normalization_log",
        }
        result = copy.deepcopy(originals)

        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                raise TypeError(f"changes[{index}] 必须为 object")
            engine = change.get("engine")
            if engine not in result or engine not in ENGINE_KEYS:
                raise ValueError(f"changes[{index}] 指定了未知引擎: {engine!r}")
            operation = change.get("operation", "replace")
            if operation not in {"replace", "remove", "add"}:
                raise ValueError(f"changes[{index}] operation 非法: {operation!r}")
            parts = AuditAgent._patch_path_parts(change.get("path"))
            if not parts or parts[0] in protected_roots:
                raise ValueError(f"changes[{index}] 试图修改受保护字段")
            if "value" in change:
                value_size = len(json.dumps(change["value"], ensure_ascii=False, default=str))
                if value_size > 8000:
                    raise ValueError(f"changes[{index}] value 过大，疑似完整报告重写")
            elif operation != "remove":
                raise ValueError(f"changes[{index}] 缺少 value")

            parent: Any = result[engine]
            for part in parts[:-1]:
                if isinstance(parent, dict) and isinstance(part, str) and part in parent:
                    parent = parent[part]
                elif isinstance(parent, list) and isinstance(part, int) and 0 <= part < len(parent):
                    parent = parent[part]
                else:
                    raise ValueError(f"changes[{index}] path 不存在: {change.get('path')!r}")

            leaf = parts[-1]
            if isinstance(parent, dict) and isinstance(leaf, str) and leaf != "-":
                if operation in {"replace", "remove"} and leaf not in parent:
                    raise ValueError(f"changes[{index}] path 不存在: {change.get('path')!r}")
                if operation == "remove":
                    parent.pop(leaf)
                else:
                    parent[leaf] = copy.deepcopy(change["value"])
            elif isinstance(parent, list):
                if operation == "add" and leaf == "-":
                    parent.append(copy.deepcopy(change["value"]))
                elif isinstance(leaf, int) and 0 <= leaf < len(parent):
                    if operation == "remove":
                        parent.pop(leaf)
                    elif operation == "replace":
                        parent[leaf] = copy.deepcopy(change["value"])
                    else:
                        parent.insert(leaf, copy.deepcopy(change["value"]))
                else:
                    raise ValueError(f"changes[{index}] 列表下标非法: {change.get('path')!r}")
            else:
                raise ValueError(f"changes[{index}] path 无法应用: {change.get('path')!r}")

        return result

    def _merge_with_originals(
        self,
        originals: Dict[str, Any],
        audited: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        安全合并：以原始数据为基底，用审计结果做"补丁式"覆盖。

        核心原则：
        - 原始字段 100% 保留
        - 仅当 audit 明确修改了某个字段时，才覆盖该字段
        - 如果审计结果缺少某个引擎的整体输出，回退到原始数据
        """
        merged = {}

        for engine_key in originals:
            original_engine = originals.get(engine_key, {})
            audited_engine = audited.get(engine_key)

            if audited_engine is None or not isinstance(audited_engine, dict):
                # 审计结果中缺少该引擎 → 完整保留原始数据
                logger.warning(
                    "audited_results 缺少 '%s' 引擎，回退到原始数据", engine_key
                )
                merged[engine_key] = original_engine
            else:
                # 以原始数据为底，审计结果做补丁
                merged[engine_key] = self._deep_patch(
                    original_engine, audited_engine
                )

        return merged

    @staticmethod
    def _deep_patch(
        original: Any,
        patch: Any,
    ) -> Any:
        """
        递归深度补丁合并。

        规则：
        - 如果 original 和 patch 都是 dict：递归合并每个 key
        - 否则：以 patch 为准（意味着 LLM 明确修改了该字段）
        - original 中有但 patch 中没有的字段：保留 original 的值
        """
        if isinstance(original, dict) and isinstance(patch, dict):
            result = dict(original)  # 从原始数据出发
            for key, patch_value in patch.items():
                if key in result:
                    result[key] = AuditAgent._deep_patch(
                        result[key], patch_value
                    )
                else:
                    # patch 中有、original 中没有的字段 → 直接采用
                    result[key] = patch_value
            return result
        else:
            # 标量 / 列表 / 其他类型：LLM 的修改直接覆盖
            return patch


# ---------------------------------------------------------------------------
# 便捷工厂函数
# ---------------------------------------------------------------------------

def create_audit_agent(
    api_key: str,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs,
) -> AuditAgent:
    """
    工厂函数：快速创建一个 AuditAgent 实例。

    Parameters
    ----------
    api_key : str
        API 密钥。
    base_url : str, optional
        API 地址。
    model : str, optional
        模型名。
    **kwargs
        传递给 AuditAgent.__init__ 的其他参数。

    Returns
    -------
    AuditAgent
    """
    return AuditAgent(
        api_key=api_key,
        base_url=base_url,
        model=model,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 独立运行入口（调试 / 测试用）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ---- 示例数据 ----
    sample_paper = {
        "title": "A Novel Approach to Machine Reasoning",
        "abstract": "This paper proposes a new framework for reasoning...",
        "sections": [
            {
                "heading": "Introduction",
                "content": "Machine reasoning has been a long-standing challenge..."
            },
            {
                "heading": "Methodology",
                "content": "We introduce a three-stage pipeline consisting of..."
            },
            {
                "heading": "Experiments",
                "content": "We evaluate on three benchmark datasets: Dataset-A, Dataset-B, Dataset-C..."
            },
        ],
        "tables": [
            {"id": "Table 1", "caption": "Performance comparison on Dataset-A"},
            {"id": "Table 2", "caption": "Ablation study results"},
        ],
        "figures": [
            {"id": "Figure 1", "caption": "Architecture overview"},
            {"id": "Figure 2", "caption": "Training curves"},
        ],
    }

    # 模拟五个评价维度的初步评价（其中包含故意设置的“幻觉”证据）
    sample_reports = {
        "methodology": {
            "score": 8.0,
            "evidence": "The paper uses a three-stage pipeline as described in Section 3.2.",
            "core_conclusion": "Methodology is sound and rigorous.",
            "actionable_advice": "Consider adding more baseline comparisons.",
            "sub_dimensions": {
                "experimental_design": 8.0,
                "reproducibility": 7.5,
            },
        },
        "logic": {
            "score": 6.0,
            "evidence": "The proof in Appendix B contains a contradiction with Table 5.",
            "core_conclusion": "Logical flow has gaps.",
            "actionable_advice": "Revise the proof in Appendix B.",
            "sub_dimensions": {
                "argument_structure": 6.0,
                "deductive_validity": 5.5,
            },
        },
        "ethics": {
            "score": 9.0,
            "evidence": "IRB approval documented in Section 5.1.",
            "core_conclusion": "Ethical compliance is adequate.",
            "actionable_advice": "No major concerns.",
            "sub_dimensions": {
                "data_privacy": 9.0,
                "fairness": 8.5,
            },
        },
        "innovation": {
            "score": 7.0,
            "evidence": "Figure 3 demonstrates a novel architecture not seen in prior work.",
            "core_conclusion": "Moderate novelty.",
            "actionable_advice": "Highlight differentiation from prior art more clearly.",
            "sub_dimensions": {
                "originality": 7.5,
                "impact": 6.5,
            },
        },
    }

    # 从环境变量读取 API 配置
    api_key = os.environ.get("DASHSCOPE_API_KEY", "your-api-key-here")
    base_url = (
        os.environ.get("QWEN_BASE_URL")
        or os.environ.get("DASHSCOPE_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    model = os.environ.get("AUDIT_MODEL") or os.environ.get("QWEN_TEXT_MODEL", "qwen-plus-latest")

    agent = AuditAgent(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )

    print("=" * 60)
    print("AI 学术审查系统 Audit Agent - 调试模式")
    print("=" * 60)

    try:
        result = agent.audit(
            original_paper=sample_paper,
            preliminary_reports=sample_reports,
        )

        print("\n📋 审计日志 (audit_log):")
        print(json.dumps(result.audit_log, ensure_ascii=False, indent=2))

        print("\n✅ 审计后结果 (audited_results):")
        print(json.dumps(result.audited_results, ensure_ascii=False, indent=2))

    except Exception as e:
        print(f"\n❌ 审计失败: {e}", file=sys.stderr)
        sys.exit(1)
