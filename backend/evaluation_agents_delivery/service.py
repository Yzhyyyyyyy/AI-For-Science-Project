"""Backend-facing service API for configured paper-evaluation agents."""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

if __package__:
    from .pipeline.bailian_client import BailianClient, ModelConfig
    from .pipeline.config import AGENTS, EVALUATION_MAX_WORKERS, current_prompts
    from .pipeline.orchestrator import EvaluationOrchestrator
else:
    from pipeline.bailian_client import BailianClient, ModelConfig
    from pipeline.config import AGENTS, EVALUATION_MAX_WORKERS, current_prompts
    from pipeline.orchestrator import EvaluationOrchestrator


PACKAGE_ROOT = Path(__file__).resolve().parent
logger = logging.getLogger("AIReview.Evaluation")


def _load_local_env() -> None:
    """Load an optional local .env while preserving existing environment values."""
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(PACKAGE_ROOT / ".env", override=False)


def _with_audit_feedback(paper_data: dict[str, Any], audit_feedback: dict[str, Any] | None) -> dict[str, Any]:
    payload = copy.deepcopy(paper_data)
    if audit_feedback is None:
        return payload

    review_context = payload.get("review_context")
    if not isinstance(review_context, dict):
        review_context = {}
        payload["review_context"] = review_context
    review_context["audit_feedback"] = copy.deepcopy(audit_feedback)
    return payload


def evaluate_paper(
    paper_data: dict,
    audit_feedback: dict | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
    progress_callback: Any | None = None,
    agent_names: list[str] | tuple[str, ...] | None = None,
    max_workers: int | None = None,
) -> dict:
    """
    输入论文标准JSON
    输出五个评价Agent结果

    返回值以 Agent 标识为顶层键。每个维度包含运行状态、Prompt
    版本、尝试次数、标准化评价结果、错误和质量检查提示。
    """
    if not isinstance(paper_data, dict):
        raise TypeError("paper_data 必须是 dict")
    if audit_feedback is not None and not isinstance(audit_feedback, dict):
        raise TypeError("audit_feedback 必须是 dict 或 None")

    _load_local_env()
    effective_api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
    if not effective_api_key:
        raise RuntimeError("未配置 API Key（请在设置页面填写，或在 backend/.env 中配置）")
    effective_base_url = base_url or os.getenv("QWEN_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    effective_model = model_name or os.getenv("QWEN_TEXT_MODEL", "qwen-plus-latest")

    prompts = current_prompts()
    if agent_names is not None:
        requested = list(dict.fromkeys(agent_names))
        unknown = [name for name in requested if name not in prompts]
        if unknown:
            raise ValueError(f"未知评价维度：{unknown}")
        if not requested:
            raise ValueError("agent_names 不能为空")
        prompts = {name: prompts[name] for name in requested}

    payload = _with_audit_feedback(paper_data, audit_feedback)
    orchestrator = EvaluationOrchestrator(
        client=BailianClient(
            api_key=effective_api_key,
            base_url=effective_base_url,
            config=ModelConfig(model=effective_model),
        ),
        prompts=prompts,
        output_root=None,
        max_workers=max(1, min(5, max_workers or EVALUATION_MAX_WORKERS)),
        progress_callback=progress_callback,
    )
    agent_results = orchestrator.evaluate_data(payload)

    response: dict[str, Any] = {}
    failed_agents: list[str] = []
    error_types: list[str] = []
    for agent_result in agent_results:
        validation = agent_result.validation
        failed = agent_result.status != "success"
        if failed:
            failed_agents.append(agent_result.agent)
            if agent_result.error_type and agent_result.error_type not in error_types:
                error_types.append(agent_result.error_type)
            if not agent_result.error_type:
                for error in validation.errors:
                    error_type = (
                        error.get("error_type", "validation_error")
                        if isinstance(error, dict)
                        else "validation_error"
                    )
                    if error_type not in error_types:
                        error_types.append(error_type)
        response[agent_result.agent] = {
            "status": agent_result.status,
            "prompt_version": agent_result.prompt_version,
            "attempts": agent_result.attempts,
            "result": None if failed else validation.normalized_value,
            "errors": list(validation.errors),
            "warnings": list(validation.warnings),
            "normalization_log": list(validation.normalization_log),
            "error_type": agent_result.error_type or ("validation_error" if failed else ""),
        }
        if failed:
            logger.warning(
                "Evaluation dimension failed agent=%s attempts=%s error_type=%s errors=%s",
                agent_result.agent,
                agent_result.attempts,
                response[agent_result.agent]["error_type"],
                list(validation.errors)[:4],
            )

    if not failed_agents:
        evaluation_status = "success"
    elif len(failed_agents) == len(prompts):
        evaluation_status = "failed"
    else:
        evaluation_status = "partial_failure"
    response["evaluation_status"] = evaluation_status
    response["error_summary"] = {
        "failed_agents": failed_agents,
        "error_types": error_types,
    }
    return response


__all__ = ["evaluate_paper"]
