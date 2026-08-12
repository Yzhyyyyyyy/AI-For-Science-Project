# -*- coding: utf-8 -*-
"""Unified document parsing, evaluation, and audit pipeline."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

CONTROLLER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CONTROLLER_DIR.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / "evaluation_agents_delivery" / ".env", override=False)

for module_dir in (
    PROJECT_ROOT / "data_processing",
    PROJECT_ROOT / "evaluation_agents_delivery",
    PROJECT_ROOT / "audit_agent",
):
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))

from audit_agent import AuditAgent, SYSTEM_PROMPT  # noqa: E402
from data_processor import parse_visual_pages, process_document  # noqa: E402
from pipeline.config import AGENTS, EVALUATION_MAX_INPUT_CHARS, current_prompts  # noqa: E402
from service import evaluate_paper  # noqa: E402
from scoring_policy import attach_scoring_policy  # noqa: E402

logger = logging.getLogger("AIReview.Main")
BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = os.getenv("QWEN_TEXT_MODEL", "qwen-plus-latest")

AGENT_TO_AUDIT = {
    "data_reliability": "methodology",
    "ethics_bias": "ethics",
    "logical_rigor": "logic",
    "innovation": "innovation",
    "academic_impact": "academic_impact",
}
AUDIT_TO_AGENT = {value: key for key, value in AGENT_TO_AUDIT.items()}
CACHE_SCHEMA_VERSION = "pipeline-cache-v4-explicit-evidence-links"
CACHE_ROOT = PROJECT_ROOT / "runtime" / "pipeline_cache"
_CACHE_LOCKS_GUARD = threading.Lock()
_CACHE_WRITE_LOCKS: dict[str, threading.Lock] = {}


def _emit_progress(callback: Any | None, stage: str, message: str, **detail: Any) -> None:
    """Best-effort progress notification; UI updates must never break the pipeline."""
    if callback is None:
        return
    try:
        callback({"stage": stage, "message": message, **detail})
    except Exception:
        logger.warning("Progress callback failed", exc_info=True)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json_cache(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_cache(path: Path, value: dict[str, Any]) -> bool:
    """Atomically persist cache data without allowing cache failures to fail a review.

    A unique short temporary name prevents concurrent reviews of the same paper
    from moving each other's ``.tmp`` file.  Cache is an optimisation only: deep
    Windows install paths, antivirus locks, a full disk, or a concurrent cleanup
    must never discard an otherwise valid evaluation result.
    """
    lock_key = os.path.normcase(os.path.abspath(path))
    with _CACHE_LOCKS_GUARD:
        path_lock = _CACHE_WRITE_LOCKS.setdefault(lock_key, threading.Lock())
    with path_lock:
        temporary: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.parent / f".{uuid.uuid4().hex[:12]}.tmp"
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            for attempt in range(3):
                try:
                    os.replace(temporary, path)
                    return True
                except PermissionError:
                    if attempt >= 2:
                        raise
                    time.sleep(0.02 * (attempt + 1))
            return False
        except OSError:
            logger.warning("Pipeline cache write skipped path=%s", path, exc_info=True)
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.debug("Unable to remove temporary cache file %s", temporary, exc_info=True)


def _pipeline_cache_paths(
    file_path: str,
    processing_options: dict[str, Any],
    *,
    base_url: str | None = None,
    model_name: str | None = None,
) -> dict[str, Path]:
    file_digest = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
    cache_options = {
        key: value
        for key, value in processing_options.items()
        if key not in {"output_dir", "review_context"}
    }
    parse_key = _sha256_text(_stable_json({
        "schema": CACHE_SCHEMA_VERSION,
        "file": file_digest,
        "options": cache_options,
        "provider": (base_url or "server-default").rstrip("/"),
        "model": model_name or MODEL,
        "review_context": processing_options.get("review_context"),
    }))
    prompt_fingerprint = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in current_prompts().items()
    }
    evaluation_key = _sha256_text(_stable_json({
        "parse": parse_key,
        "model": model_name or MODEL,
        "provider": (base_url or "server-default").rstrip("/"),
        "prompts": prompt_fingerprint,
        "input_scope_version": "dimension-sections-v2",
        "max_input_chars": EVALUATION_MAX_INPUT_CHARS,
    }))
    audit_key = _sha256_text(_stable_json({
        "evaluation": evaluation_key,
        "model": model_name or MODEL,
        "provider": (base_url or "server-default").rstrip("/"),
        "system_prompt": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
    }))
    # Keep generated paths comfortably below the Windows legacy MAX_PATH limit,
    # even when the distribution is launched directly from a deep WeChat folder.
    directory = CACHE_ROOT / f"p-{parse_key[:16]}"
    return {
        "parse": directory / "p.json",
        "evaluation": directory / f"e-{evaluation_key[:16]}.json",
        "review": directory / f"r-{audit_key[:16]}.json",
    }


def _paper_data_cacheable(paper_data: dict[str, Any]) -> bool:
    paper_info = paper_data.get("paper_info", {})
    content = paper_data.get("content", {})
    title = str(paper_info.get("title") or "").strip().lower()
    return (
        title not in {"", "unknown", "unknown title"}
        and bool(str(content.get("full_text") or "").strip())
        and bool(content.get("text_anchors"))
    )


def _anchor_for_quote(paper_data: dict[str, Any], quote: str) -> dict[str, Any] | None:
    """Resolve an evidence quote to the most specific source anchor."""
    if not quote:
        return None
    content = paper_data.get("content", {})
    full_text = content.get("full_text", "")
    anchors = content.get("text_anchors", [])
    if not isinstance(full_text, str) or not isinstance(anchors, list):
        return None
    start = full_text.find(quote)
    if start < 0:
        start = full_text.find(quote[:80])
    if start < 0:
        normalized_text: list[str] = []
        original_positions: list[int] = []
        for index, char in enumerate(full_text):
            if char.isalnum():
                normalized_text.append(char.lower())
                original_positions.append(index)
        normalized_quote = "".join(char.lower() for char in quote if char.isalnum())
        # 长引用常有少量标点/OCR差异，使用稳定的前段定位。
        searchable_text = "".join(normalized_text)
        for probe_length in (160, 100, 60, 30, 12):
            probe = normalized_quote[:probe_length]
            if len(probe) < probe_length:
                continue
            normalized_start = searchable_text.find(probe)
            if normalized_start >= 0:
                start = original_positions[normalized_start]
                break
    if start < 0:
        return None
    end = start + len(quote)
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        if anchor.get("char_start", -1) < end and anchor.get("char_end", -1) > start:
            return {
                "anchor_id": anchor.get("anchor_id"),
                "page": anchor.get("page"),
                "paragraph": anchor.get("paragraph_index"),
                "bbox": anchor.get("bbox"),
                "bbox_norm": anchor.get("bbox_norm"),
                "char_start": anchor.get("char_start"),
                "char_end": anchor.get("char_end"),
            }
    return None


def _anchor_on_page(paper_data: dict[str, Any], page: Any) -> dict[str, Any] | None:
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        return None
    anchors = paper_data.get("content", {}).get("text_anchors", [])
    for anchor in anchors:
        if isinstance(anchor, dict) and anchor.get("page") == page_number:
            return {
                "anchor_id": anchor.get("anchor_id"),
                "page": anchor.get("page"),
                "paragraph": anchor.get("paragraph_index"),
                "bbox": anchor.get("bbox"),
                "bbox_norm": anchor.get("bbox_norm"),
                "char_start": anchor.get("char_start"),
                "char_end": anchor.get("char_end"),
            }
    return None


def _evidence_tokens(text: str) -> set[str]:
    import re

    normalized = "".join(char.lower() for char in text if char.isalnum())
    bigrams = {normalized[index:index + 2] for index in range(max(0, len(normalized) - 1))}
    numbers = set(re.findall(r"\d+(?:\.\d+)?", text))
    latin = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower()))
    return bigrams | numbers | latin


def attach_risk_locations(paper_data: dict[str, Any], results: dict[str, dict[str, Any]]) -> None:
    """Bind evidence and each risk item to source coordinates in-place."""
    for result in results.values():
        refs = result.get("evidence_refs", [])
        if not isinstance(refs, list):
            refs = []
        resolved_refs: list[tuple[int, dict[str, Any], set[str]]] = []
        resolved_refs_by_id: dict[str, tuple[int, dict[str, Any]]] = {}
        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            anchor = _anchor_for_quote(paper_data, str(ref.get("quote", "")))
            match_method = "normalized_quote"
            if not anchor:
                anchor = _anchor_on_page(paper_data, ref.get("page"))
                match_method = "page_hint"
            if anchor:
                ref.setdefault("block_id", anchor["anchor_id"])
                ref.setdefault("page", anchor["page"])
                ref.setdefault("paragraph", anchor["paragraph"])
                ref["coordinates"] = {**anchor, "match_method": match_method}
                resolved_refs.append((ref_index, anchor, _evidence_tokens(str(ref.get("quote", "")))))
                for ref_id in (ref.get("ref_id"), ref.get("block_id"), anchor.get("anchor_id")):
                    if isinstance(ref_id, str) and ref_id:
                        resolved_refs_by_id[ref_id] = (ref_index, anchor)

        for issue in result.get("issues", []):
            if not isinstance(issue, dict):
                continue
            explicit_ids = issue.get("evidence_ref_ids", [])
            if isinstance(explicit_ids, str):
                explicit_ids = [explicit_ids]
            if isinstance(explicit_ids, list):
                explicit_match = next(
                    (resolved_refs_by_id[ref_id] for ref_id in explicit_ids
                     if isinstance(ref_id, str) and ref_id in resolved_refs_by_id),
                    None,
                )
                if explicit_match:
                    ref_index, ref_anchor = explicit_match
                    issue["location"] = {
                        **ref_anchor,
                        "match_method": "explicit_evidence_ref",
                        "evidence_ref_index": ref_index,
                    }
                    continue
            existing_location = issue.get("location")
            if isinstance(existing_location, dict):
                anchor = _anchor_for_quote(paper_data, str(existing_location.get("quote", "")))
                if not anchor:
                    anchor = _anchor_on_page(paper_data, existing_location.get("page"))
                if anchor:
                    issue["location"] = {**existing_location, **anchor, "match_method": "declared_location"}
                    continue
            evidence = str(issue.get("evidence", ""))
            anchor = _anchor_for_quote(paper_data, evidence)
            if anchor:
                issue["location"] = {**anchor, "match_method": "normalized_quote"}
                continue
            issue_tokens = _evidence_tokens(evidence)
            candidates = [
                (
                    len(issue_tokens & ref_tokens) / max(1, len(issue_tokens)),
                    ref_index,
                    ref_anchor,
                )
                for ref_index, ref_anchor, ref_tokens in resolved_refs
            ]
            if candidates:
                score, ref_index, ref_anchor = max(candidates, key=lambda value: value[0])
                if score >= 0.03:
                    issue["location"] = {
                        **ref_anchor,
                        "match_method": "related_evidence_ref",
                        "evidence_ref_index": ref_index,
                        "match_score": round(score, 3),
                    }
                    continue
            if "metadata.open_data" in evidence:
                issue["location"] = {
                    "json_path": "paper_data.metadata.open_data",
                    "value": paper_data.get("metadata", {}).get("open_data"),
                    "match_method": "structured_field",
                }


def _successful_results(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return only successful dimensions; failed calls are never scored as zero."""
    return {
        name: item["result"]
        for name, item in response.items()
        if name in AGENT_TO_AUDIT
        and isinstance(item, dict)
        and item.get("status") == "success"
        and isinstance(item.get("result"), dict)
    }


def _safe_page_number(value: Any) -> int:
    """Normalize optional parser page values without aborting the review."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _merge_evaluation_recovery(
    initial: dict[str, Any],
    recovery: dict[str, Any],
) -> dict[str, Any]:
    """Replace only re-reviewed dimensions and recompute the five-dimension status."""
    merged = dict(initial)
    for name in AGENT_TO_AUDIT:
        if name in recovery:
            merged[name] = recovery[name]

    failed_agents = [
        name
        for name in AGENT_TO_AUDIT
        if not isinstance(merged.get(name), dict)
        or merged[name].get("status") != "success"
        or not isinstance(merged[name].get("result"), dict)
    ]
    error_types: list[str] = []
    for name in failed_agents:
        item = merged.get(name)
        if isinstance(item, dict):
            error_type = str(item.get("error_type") or "validation_error")
            if error_type not in error_types:
                error_types.append(error_type)

    merged["evaluation_status"] = (
        "success"
        if not failed_agents
        else "failed" if len(failed_agents) == len(AGENT_TO_AUDIT) else "partial_failure"
    )
    merged["error_summary"] = {
        "failed_agents": failed_agents,
        "error_types": error_types,
    }
    return merged


def run_evaluation_and_audit(
    paper_data: dict[str, Any],
    api_key: str,
    base_url: str | None = None,
    model_name: str | None = None,
    service_response: dict[str, Any] | None = None,
    stage_metrics: dict[str, Any] | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Evaluate once; targeted retries stay inside each evaluation dimension."""
    stage_metrics = stage_metrics if stage_metrics is not None else {}
    evaluation_started = time.monotonic()
    evaluation_cached = service_response is not None
    _emit_progress(
        progress_callback,
        "evaluation",
        "正在执行五个评价维度" if not evaluation_cached else "已恢复已完成的五维评价结果",
        cached=evaluation_cached,
    )
    if service_response is None:
        service_response = evaluate_paper(
            paper_data,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            progress_callback=progress_callback,
        )
    evaluation_attempts = sum(
        int(item.get("attempts", 0))
        for name, item in service_response.items()
        if name in AGENT_TO_AUDIT and isinstance(item, dict)
    )
    recovery_rounds = 0
    evaluation_status = service_response.get("evaluation_status", "failed")
    successful = _successful_results(service_response)

    failed_agents = list(service_response.get("error_summary", {}).get("failed_agents", []))
    error_types = set(service_response.get("error_summary", {}).get("error_types", []))
    non_retryable_errors = {"invalid_api_key", "insufficient_quota", "permission_denied"}
    if (
        evaluation_status == "partial_failure"
        and failed_agents
        and not error_types.intersection(non_retryable_errors)
    ):
        recovery_rounds = 1
        _emit_progress(
            progress_callback,
            "evaluation",
            f"检测到 {len(failed_agents)} 个维度未通过校验，正在仅重审失败维度",
            status="retrying",
            phase="targeted_recovery",
            failedAgents=failed_agents,
        )
        recovery_response = evaluate_paper(
            paper_data,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            progress_callback=progress_callback,
            agent_names=failed_agents,
            max_workers=1,
        )
        evaluation_attempts += sum(
            int(item.get("attempts", 0))
            for name, item in recovery_response.items()
            if name in AGENT_TO_AUDIT and isinstance(item, dict)
        )
        service_response = _merge_evaluation_recovery(service_response, recovery_response)
        evaluation_status = service_response.get("evaluation_status", "failed")
        successful = _successful_results(service_response)
        _emit_progress(
            progress_callback,
            "evaluation",
            (
                "失败维度定向重审完成，五维结果现已完整"
                if evaluation_status == "success"
                else "失败维度定向重审后仍未全部通过，已停止最终审核"
            ),
            status="success" if evaluation_status == "success" else "failed",
            phase="targeted_recovery_completed",
            completed=len(successful),
            total=len(AGENT_TO_AUDIT),
            failedAgents=service_response.get("error_summary", {}).get("failed_agents", []),
        )

    stage_metrics["evaluation"] = {
        "cached": evaluation_cached,
        "seconds": round(time.monotonic() - evaluation_started, 3),
        "attempts": evaluation_attempts,
        "recovery_rounds": recovery_rounds,
    }
    _emit_progress(
        progress_callback,
        "evaluation",
        f"五维评价阶段完成，成功返回 {len(successful)} 个维度",
        status=evaluation_status,
        completed=len(successful),
        total=len(AGENT_TO_AUDIT),
    )

    if evaluation_status != "success" or len(successful) != len(AGENT_TO_AUDIT):
        error_types = service_response.get("error_summary", {}).get("error_types", [])
        if "invalid_api_key" in error_types:
            error = {
                "code": "INVALID_API_KEY",
                "message": "模型服务密钥无效，请在设置页面或 backend/.env 中填写正确的 API Key。",
            }
        elif "insufficient_quota" in error_types:
            error = {
                "code": "MODEL_QUOTA_EXHAUSTED",
                "message": "模型服务额度不足，请检查账户配额后重试。",
            }
        elif evaluation_status == "partial_failure":
            failed_labels = [
                AGENTS.get(name, name)
                for name in service_response.get("error_summary", {}).get("failed_agents", [])
            ]
            error = {
                "code": "EVALUATION_INCOMPLETE",
                "message": f"以下评价维度重审后仍未通过：{'、'.join(failed_labels) or '未知维度'}。",
            }
        else:
            error = {"code": "MODEL_SERVICE_UNAVAILABLE", "message": "模型服务异常，请稍后重试。"}
        stage_metrics["audit"] = {
            "cached": False,
            "seconds": 0.0,
            "success": False,
            "attempts": 0,
            "skipped": True,
        }
        return {
            "evaluation_status": evaluation_status,
            "error_summary": service_response.get("error_summary", {}),
            "error": error,
            "agent_results": service_response,
            "final_results": {},
            "audit_log": {"status": "skipped", "reason": error["message"]},
            "audit_passed": False,
        }

    audit_input = {AGENT_TO_AUDIT[name]: result for name, result in successful.items()}
    audit_log: dict[str, Any] = {}
    audited = audit_input
    audit_passed = False
    audit_started = time.monotonic()
    audit_attempts = 0
    audit_agent: AuditAgent | None = None
    _emit_progress(
        progress_callback,
        "audit",
        "五维评价及失败维度重审均已完成，正在组装最终一致性复核任务",
        phase="preparing",
        progressKey="audit-preparing",
    )
    try:
        audit_agent = AuditAgent(
            api_key=api_key,
            base_url=base_url or BASE_URL,
            model=model_name or MODEL,
            progress_callback=progress_callback,
        )
        audit_output = audit_agent.audit(original_paper=paper_data, preliminary_reports=audit_input)
        audit_attempts = audit_agent.last_attempts
        audit_log = audit_output.audit_log
        audited = audit_output.audited_results
        audit_feedback = audit_log.get("review_feedback", []) if isinstance(audit_log, dict) else []
        max_audit_revisions = max(0, min(1, int(os.getenv("AUDIT_REEVALUATION_MAX_ROUNDS", "1"))))
        if audit_feedback and max_audit_revisions:
            target_agents = list(dict.fromkeys(
                str(item.get("target_agent"))
                for item in audit_feedback
                if isinstance(item, dict) and item.get("target_agent") in AGENT_TO_AUDIT
            ))
            if target_agents:
                _emit_progress(
                    progress_callback,
                    "audit",
                    f"复核发现 {len(audit_feedback)} 项需修订内容，正在定向重评 {len(target_agents)} 个维度",
                    status="retrying",
                    phase="audit_feedback_revision",
                    targetAgents=target_agents,
                )
                revision_payload = copy.deepcopy(paper_data)
                revision_context = revision_payload.setdefault("review_context", {})
                if not isinstance(revision_context, dict):
                    revision_context = {}
                    revision_payload["review_context"] = revision_context
                revision_context["retry_feedback"] = copy.deepcopy(audit_feedback)
                revision_response = evaluate_paper(
                    revision_payload,
                    api_key=api_key,
                    base_url=base_url,
                    model_name=model_name,
                    progress_callback=progress_callback,
                    agent_names=target_agents,
                    max_workers=1,
                )
                service_response = _merge_evaluation_recovery(service_response, revision_response)
                successful = _successful_results(service_response)
                if len(successful) != len(AGENT_TO_AUDIT):
                    raise RuntimeError("复核定向重评未返回完整五维结果")
                audit_input = {AGENT_TO_AUDIT[name]: result for name, result in successful.items()}
                audit_output = audit_agent.audit(
                    original_paper=paper_data,
                    preliminary_reports=audit_input,
                )
                audit_attempts += audit_agent.last_attempts
                audit_log = {
                    **audit_output.audit_log,
                    "audit_feedback_revision_rounds": 1,
                    "revised_agents": target_agents,
                    "initial_review_feedback": audit_feedback,
                }
                audited = audit_output.audited_results
        audit_passed = True
        _emit_progress(
            progress_callback,
            "audit",
            "评价结果复核完成，最终报告将使用复核后的评分与证据",
            status="success",
            phase="completed",
            progressKey="audit-completed",
        )
    except Exception as exc:
        if audit_agent is not None:
            audit_attempts = audit_agent.last_attempts
        logger.exception("Audit failed; preserving successful evaluation outputs")
        audit_passed = False
        audit_log = {
            "status": "failed",
            "error": str(exc),
            "limitations": ["审计服务失败，本次结果为未经二次审计的评价结果。"],
        }
        # v5.1: preserve successful evaluation results even when audit fails
        audited = audit_input if isinstance(audit_input, dict) and audit_input else {}
        _emit_progress(
            progress_callback,
            "audit",
            "复核服务未成功，已保留五维评价原始结果，系统将展示降级报告",
            status="degraded",
            phase="degraded",
            progressKey="audit-degraded",
        )
    audit_performance = (
        audit_agent.last_metrics
        if audit_agent is not None and isinstance(audit_agent.last_metrics, dict)
        else {}
    )
    stage_metrics["audit"] = {
        "cached": False,
        "seconds": round(time.monotonic() - audit_started, 3),
        "success": audit_passed,
        "attempts": audit_attempts,
        **audit_performance,
    }

    final_results = {
        AUDIT_TO_AGENT[key]: value
        for key, value in audited.items()
        if key in AUDIT_TO_AGENT and isinstance(value, dict)
    }
    _emit_progress(progress_callback, "report", "正在将风险条目回链到原文页码、段落与坐标")
    attach_risk_locations(paper_data, final_results)
    linked_issue_count = sum(
        1
        for result in final_results.values()
        for issue in result.get("issues", [])
        if isinstance(issue, dict) and isinstance(issue.get("evidence_ref"), dict)
    )
    _emit_progress(
        progress_callback,
        "report",
        f"原文回链完成，已有 {linked_issue_count} 条风险项获得可点击定位",
        linkedIssues=linked_issue_count,
    )
    return {
        "evaluation_status": evaluation_status,
        "error_summary": service_response.get("error_summary", {}),
        "agent_results": service_response,
        "final_results": final_results,
        "audit_log": audit_log,
        "audit_passed": audit_passed,
    }


def main_pipeline(file_path: str, **processing_options: Any) -> dict[str, Any]:
    """Parse a supported document and produce a stable structured review."""
    progress_callback = processing_options.pop("progress_callback", None)
    force_refresh = bool(processing_options.pop("force_refresh", False))
    api_key = processing_options.pop("api_key", None) or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 API Key（请在设置页面填写，或在 backend/.env 中配置）")

    frontend_base_url = processing_options.pop("base_url", None)
    frontend_model = processing_options.pop("model_name", None)
    # v5.1: new weight mode parameters
    review_mode = processing_options.pop("review_mode", "preset")
    custom_weights_raw = processing_options.pop("custom_weights", None)
    domain_hint = processing_options.pop("domain_hint", None)

    started = time.monotonic()
    stage_metrics: dict[str, Any] = {}
    cache_paths = _pipeline_cache_paths(
        file_path,
        processing_options,
        base_url=frontend_base_url,
        model_name=frontend_model,
    )
    if force_refresh:
        for cache_path in cache_paths.values():
            cache_path.unlink(missing_ok=True)
        _emit_progress(
            progress_callback,
            "parsing",
            "已按当前审查配置清除解析、评价与复核缓存，本次将完整重新执行",
            cacheCleared=True,
        )

    parse_started = time.monotonic()
    _emit_progress(progress_callback, "parsing", "正在检查文档格式、文件完整性与解析缓存")
    paper_data = _read_json_cache(cache_paths["parse"])
    if paper_data is not None and not _paper_data_cacheable(paper_data):
        paper_data = None
    parse_cached = paper_data is not None
    if paper_data is None:
        _emit_progress(progress_callback, "parsing", "正在提取文字层、识别章节结构并记录页面坐标")
        paper_data = process_document(
            file_path,
            schema="full",
            api_key=api_key,
            base_url=frontend_base_url,
            model_name=frontend_model,
            **processing_options,
        )
        if _paper_data_cacheable(paper_data):
            _write_json_cache(cache_paths["parse"], paper_data)
    else:
        _emit_progress(progress_callback, "parsing", "已找到可用解析缓存，正在校验正文与定位锚点")
    stage_metrics["parsing"] = {
        "cached": parse_cached,
        "seconds": round(time.monotonic() - parse_started, 3),
    }
    content = paper_data.get("content", {})
    anchors = content.get("text_anchors", [])
    anchors = anchors if isinstance(anchors, list) else []
    sections = content.get("sections", [])
    anchor_count = len(anchors)
    section_count = len(sections) if isinstance(sections, list) else 0
    character_count = len(str(content.get("full_text") or ""))
    page_count = max(
        (_safe_page_number(anchor.get("page")) for anchor in anchors if isinstance(anchor, dict)),
        default=0,
    )
    _emit_progress(
        progress_callback,
        "parsing",
        f"论文解析完成：识别 {page_count} 页、{section_count} 个章节、{character_count:,} 个字符，并建立 {anchor_count} 个原文定位锚点",
        cached=parse_cached,
        anchorCount=anchor_count,
        pageCount=page_count,
        sectionCount=section_count,
        characterCount=character_count,
    )

    cached_review = _read_json_cache(cache_paths["review"])
    if cached_review is not None:
        result = cached_review
        attach_risk_locations(paper_data, result.get("final_results", {}))
        stage_metrics["evaluation"] = {"cached": True, "seconds": 0.0}
        stage_metrics["audit"] = {
            "cached": True,
            "seconds": 0.0,
            "success": True,
            "attempts": 0,
        }
    else:
        cached_evaluation = _read_json_cache(cache_paths["evaluation"])
        result = run_evaluation_and_audit(
            paper_data,
            api_key,
            base_url=frontend_base_url,
            model_name=frontend_model,
            service_response=cached_evaluation,
            stage_metrics=stage_metrics,
            progress_callback=progress_callback,
        )
        if result.get("evaluation_status") == "success":
            # Guard: do NOT cache if all successful agents returned a score of 0
            all_scores = [
                item.get("score") for item in result.get("final_results", {}).values()
                if isinstance(item, dict) and isinstance(item.get("score"), (int, float))
            ]
            if all_scores and max(all_scores) > 0:
                _write_json_cache(cache_paths["evaluation"], result["agent_results"])
        if result.get("audit_passed") and result.get("evaluation_status") == "success":
            cacheable_result = {
                key: value
                for key, value in result.items()
                if key not in {"paper_data", "processing_time_seconds", "stage_metrics"}
            }
            _write_json_cache(cache_paths["review"], cacheable_result)
    result["paper_data"] = paper_data
    attach_scoring_policy(result, paper_data, review_mode=review_mode, custom_weights=custom_weights_raw, domain_hint=domain_hint)
    result["processing_time_seconds"] = round(time.monotonic() - started, 3)
    result["stage_metrics"] = stage_metrics
    result["system_limitations"] = [
        "结果由大模型辅助生成，不替代同行评审或编辑部决定。",
        "缺乏同领域最新文献的完整横向对比时，影响力预测存在局限。",
        "纯思辨或政策评论类文章的量化评分不确定性通常更高。",
    ]
    _emit_progress(progress_callback, "report", "正在整理评分、风险条目、修改建议与原文定位信息")
    _emit_progress(progress_callback, "report", "报告数据已准备完成，正在发送到前端", status="success")
    return result


def cli() -> int:
    parser = argparse.ArgumentParser(description="AI 学术审查系统后端")
    parser.add_argument("file_path")
    parser.add_argument("-o", "--output")
    visual_group = parser.add_mutually_exclusive_group()
    visual_group.add_argument("--visuals", action="store_true", help="处理前几页图表，默认前 5 页")
    visual_group.add_argument("--full-visuals", action="store_true", help="显式开启全文视觉分析")
    visual_group.add_argument("--no-visuals", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-visual-pages", type=int, default=5)
    parser.add_argument("--visual-pages", help="指定页码，例如 1,3,5-7")
    args = parser.parse_args()
    if not Path(args.file_path).is_file():
        parser.error(f"文件不存在：{args.file_path}")
    try:
        selected_pages = parse_visual_pages(args.visual_pages)
    except ValueError as exc:
        parser.error(str(exc))
    extract_visuals = bool(args.visuals or args.full_visuals or selected_pages)
    max_visual_pages = None if args.full_visuals or selected_pages else args.max_visual_pages
    try:
        result = main_pipeline(
            args.file_path,
            extract_visuals=extract_visuals,
            max_visual_pages=max_visual_pages,
            visual_pages=selected_pages,
        )
    except KeyboardInterrupt:
        logger.warning("收到中断信号，正在取消未开始的任务并退出")
        return 130
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if result.get("evaluation_status") != "failed" else 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    raise SystemExit(cli())
