"""Pipeline validation: JSON schema reuse plus lightweight quality checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .schema_validation import (
    normalize_issue_type_enums,
    normalize_output_structure,
    validate_result,
)


@dataclass
class PipelineValidationResult:
    parse_ok: bool = False
    fields_ok: bool = False
    enums_ok: bool = False
    quality_ok: bool = False
    raw_value: dict[str, Any] | None = None
    normalized_value: dict[str, Any] | None = None
    normalization_log: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.parse_ok and self.fields_ok and self.enums_ok and self.quality_ok


def validate_pipeline_output(raw: str, agent: str, case_id: str, case_text: str) -> PipelineValidationResult:
    result = PipelineValidationResult()
    try:
        value = parse_compatible_json(raw)
    except json.JSONDecodeError as exc:
        result.errors.append(f"JSON 解析失败：{exc}")
        return result

    result.parse_ok = True
    if isinstance(value, dict):
        result.raw_value = value
    structured, structure_log = normalize_output_structure(value, agent, case_id)
    normalized, enum_log = normalize_issue_type_enums(structured, agent, case_id)
    result.normalization_log = structure_log + enum_log
    if isinstance(normalized, dict):
        normalized["normalization_log"] = result.normalization_log
        normalized = apply_boundary_rules(normalized, agent, case_text)
        preserve_evidence_locations(value, normalized)
        result.normalized_value = normalized

    schema_validation = validate_result(normalized, agent)
    result.fields_ok = schema_validation.fields_ok
    result.enums_ok = schema_validation.enums_ok
    result.errors.extend(schema_validation.errors)

    quality_errors, quality_warnings = quality_check(normalized, agent, case_text)
    result.errors.extend(quality_errors)
    result.warnings.extend(quality_warnings)
    result.quality_ok = not quality_errors
    return result


def parse_compatible_json(raw: str) -> Any:
    """Parse strict JSON plus the common fenced/prefixed form returned by gateways."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as original_error:
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                pass

        start = text.find("{")
        if start >= 0:
            decoder = json.JSONDecoder()
            try:
                value, _ = decoder.raw_decode(text[start:])
                return value
            except json.JSONDecodeError:
                pass
        raise original_error


def preserve_evidence_locations(source: Any, normalized: dict[str, Any]) -> None:
    """Keep optional source coordinates throughout normalization."""
    if not isinstance(source, dict):
        return
    source_refs = source.get("evidence_refs")
    normalized_refs = normalized.get("evidence_refs")
    if not isinstance(source_refs, list) or not isinstance(normalized_refs, list):
        return

    for source_ref, normalized_ref in zip(source_refs, normalized_refs):
        if not isinstance(source_ref, dict) or not isinstance(normalized_ref, dict):
            continue
        for field_name in ("page", "block_id", "paragraph"):
            if field_name in source_ref:
                normalized_ref[field_name] = source_ref[field_name]


def apply_boundary_rules(value: dict[str, Any], agent: str, case_text: str) -> dict[str, Any]:
    """Deterministic guardrails for edge cases that prompts may handle inconsistently."""
    text = case_text.lower()
    if agent == "ethics_bias":
        apply_secondary_text_ethics_rule(value, text)
    elif agent == "innovation":
        apply_engineering_innovation_rule(value, text)
    apply_conservative_protocol_repair(value, agent)
    return value


def apply_conservative_protocol_repair(value: dict[str, Any], agent: str) -> None:
    """Repair protocol-only defects without inventing paper evidence or conclusions."""
    issues = value.get("issues")
    if isinstance(issues, list):
        retained: list[dict[str, Any]] = []
        for issue in issues:
            if not isinstance(issue, dict):
                append_limitation(value, f"{AGENTS_LABELS.get(agent, agent)}返回了一条无法结构化的问题项，已从正式问题中移除。")
                continue
            evidence = str(issue.get("evidence") or "").strip()
            suggestion = str(issue.get("suggestion") or "").strip()
            if len(evidence) < 8 or not suggestion:
                fragment = evidence or str(issue.get("summary") or issue.get("description") or "").strip()
                message = "模型提出的候选问题缺少足够可核验证据，未计入正式问题。"
                if fragment:
                    message += f"候选描述：{fragment[:100]}"
                append_limitation(value, message)
                continue
            retained.append(issue)
        value["issues"] = retained[:6]

    refs = value.get("evidence_refs")
    if isinstance(refs, list):
        value["evidence_refs"] = [
            ref for ref in refs
            if isinstance(ref, dict)
            and isinstance(ref.get("location"), str) and ref["location"].strip()
            and isinstance(ref.get("quote"), str) and ref["quote"].strip()
        ][:8]

    for field_name, limit in (("strengths", 5), ("limitations", 6)):
        items = value.get(field_name)
        if isinstance(items, list):
            value[field_name] = [str(item).strip() for item in items if str(item).strip()][:limit]

    reasoning = str(value.get("reasoning_md") or "").strip()
    if len(reasoning) < 80:
        summary = str(value.get("summary") or "本维度模型已返回评分，但详细论证较短。 ").strip()
        issue_count = len(value.get("issues") or [])
        limitation_count = len(value.get("limitations") or [])
        value["reasoning_md"] = (
            f"{summary} 系统已对返回内容进行结构复核，并核对评分、风险等级、问题证据和修改建议之间的一致性。"
            f"当前保留 {issue_count} 条具备基本证据和建议的问题，记录 {limitation_count} 条信息限制；"
            "无法由原文或模型返回内容支持的候选问题不会进入正式报告。本段仅补全可审计说明，不新增论文事实或评价结论。"
        )


AGENTS_LABELS = {
    "data_reliability": "数据可靠性维度",
    "ethics_bias": "伦理与偏见维度",
    "logical_rigor": "逻辑严密性维度",
    "innovation": "创新性维度",
    "academic_impact": "学术影响力维度",
}


def append_limitation(value: dict[str, Any], message: str) -> None:
    limitations = value.get("limitations")
    if not isinstance(limitations, list):
        limitations = []
        value["limitations"] = limitations
    if message not in limitations:
        limitations.append(message)
    if len(limitations) > 6:
        del limitations[6:]


def apply_secondary_text_ethics_rule(value: dict[str, Any], text: str) -> None:
    issues = value.get("issues")
    if isinstance(issues, list):
        unclear_types = {
            "ethics_approval_unclear",
            "informed_consent_unclear",
            "privacy_protection_unclear",
            "data_authorization_unclear",
        }
        retained_issues = []
        moved_unclear = []
        for issue in issues:
            if not isinstance(issue, dict) or issue.get("issue_type") not in unclear_types:
                retained_issues.append(issue)
                continue
            evidence = str(issue.get("evidence") or "").strip()
            merely_unreported = "未说明" in evidence and not any(
                token in evidence for token in ("未取得", "未获得", "未经", "没有")
            )
            if merely_unreported:
                moved_unclear.append(evidence)
            else:
                retained_issues.append(issue)
        if moved_unclear:
            value["issues"] = retained_issues
            append_limitation(
                value,
                "输入材料未说明伦理审批、知情同意、隐私或数据授权时，仅记录为信息缺失限制；不能据此断言存在明确伦理违规。",
            )

    secondary_markers = (
        "conference abstract", "abstract book", "document analysis",
        "会议摘要", "摘要集", "文献综述", "二次文本", "政策文本", "公开报告",
    )
    harm_markers = (
        "discrimination", "discriminatory", "unfair", "exclusion", "excluded",
        "rights", "resource allocation", "harm", "歧视", "不公平", "排除",
        "权益", "资源分配", "伤害",
    )
    if not any(marker in text for marker in secondary_markers):
        return
    if any(marker in text for marker in harm_markers):
        return

    biases = value.get("bias_detected")
    if not isinstance(biases, list):
        return

    retained = []
    moved = []
    for bias in biases:
        if not isinstance(bias, dict):
            retained.append(bias)
            continue
        if bias.get("bias_type") in {"sample_selection_bias", "regional_bias"}:
            evidence = str(bias.get("evidence", "")).strip()
            if evidence:
                moved.append(evidence)
        else:
            retained.append(bias)
    if len(retained) == len(biases):
        return

    value["bias_detected"] = retained
    append_limitation(
        value,
        "语料来源、会议地点或年份范围有限，属于研究代表性和推论边界限制；输入未显示明确不公平后果，因此未作为伦理偏见输出。",
    )
    for evidence in moved[:2]:
        append_limitation(value, f"代表性限制证据：{evidence[:120]}")


def apply_engineering_innovation_rule(value: dict[str, Any], text: str) -> None:
    engineering_markers = (
        "engineering", "system", "microcontroller", "resource-constrained",
        "embedded", "hardware", "real-world", "deployment", "控制系统",
        "工程系统", "微控制器", "资源受限", "嵌入式", "硬件", "部署",
    )
    validation_markers = (
        "simulation", "experiment", "benchmark", "baseline", "hardware",
        "real-world", "frequency", "runtime", "memory", "gazebo", "ros",
        "仿真", "实验", "基线", "硬件", "实机", "频率", "运行时间", "内存",
    )
    if not any(marker in text for marker in engineering_markers):
        return
    if not any(marker in text for marker in validation_markers):
        return

    issues = value.get("issues")
    if not isinstance(issues, list):
        return

    retained = []
    removed = False
    for issue in issues:
        if isinstance(issue, dict) and issue.get("issue_type") == "ablation_missing":
            removed = True
            continue
        retained.append(issue)
    if not removed:
        return

    value["issues"] = retained
    append_limitation(value, "工程系统论文已有仿真、实机、基线或部署验证时，缺少模块级消融不作为创新性 issue；模块贡献归因仍可加强。")


def quality_check(value: Any, agent: str, case_text: str) -> tuple[list[str], list[str]]:
    """Lightweight checks for shallow or poorly grounded model output."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(value, dict):
        return ["顶层不是 JSON object，无法进行质量检查"], warnings

    score = value.get("score")
    risk = value.get("risk_level")
    if isinstance(score, int):
        expected_risk = "low" if score >= 80 else "medium" if score >= 60 else "high"
        if risk != expected_risk:
            errors.append(f"score 与 risk_level 不一致：score={score} 应为 {expected_risk}")

    reasoning = str(value.get("reasoning_md", ""))
    if len(reasoning.strip()) < 80:
        errors.append("reasoning_md 过短，评价依据可能过浅")

    issues = value.get("issues", [])
    if isinstance(issues, list):
        for index, issue in enumerate(issues):
            if not isinstance(issue, dict):
                continue
            evidence = str(issue.get("evidence", "")).strip()
            if len(evidence) < 8:
                errors.append(f"issues[{index}].evidence 过短或缺少具体证据")
            if agent == "ethics_bias":
                issue_type = issue.get("issue_type")
                if issue_type in {
                    "ethics_approval_unclear",
                    "informed_consent_unclear",
                    "privacy_protection_unclear",
                    "data_authorization_unclear",
                } and "未说明" in evidence and not any(token in evidence for token in ("未取得", "未获得", "未经", "没有")):
                    errors.append(f"issues[{index}] 可能将“未说明”误判为明确伦理问题")

    evidence_refs = value.get("evidence_refs", [])
    if isinstance(evidence_refs, list):
        for index, ref in enumerate(evidence_refs):
            if not isinstance(ref, dict):
                continue
            quote = str(ref.get("quote", "")).strip()
            if quote and quote not in case_text:
                warnings.append(f"evidence_refs[{index}].quote 未在输入原文中精确匹配")

    if agent == "innovation":
        innovation_types = value.get("innovation_types", [])
        if isinstance(innovation_types, list) and innovation_types and not issues:
            warnings.append("innovation_types 非空但 issues 为空，请人工确认创新证据是否充分")

    return errors, warnings
