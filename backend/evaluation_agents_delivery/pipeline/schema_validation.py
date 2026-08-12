"""Output schema definitions, normalization, and validation helpers."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .config import AGENTS


RISK_ENUM = {"low", "medium", "high"}
SEVERITY_ENUM = {"low", "medium", "high"}
INNOVATION_ENUM = {"problem", "method", "theory", "data", "application", "engineering"}
ISSUE_ENUMS = {
    "data_reliability": {
        "data_source_unclear", "sample_size_insufficient", "sample_representativeness",
        "inclusion_exclusion_unclear", "missing_value_handling", "outlier_handling",
        "duplicate_handling", "preprocessing_unclear", "variable_definition_unclear",
        "statistical_method_inappropriate", "uncertainty_not_reported", "data_leakage",
        "selective_reporting", "unsupported_extrapolation", "reproducibility_insufficient",
        "data_conclusion_mismatch", "other",
    },
    "ethics_bias": {
        "ethics_approval_unclear", "informed_consent_unclear", "privacy_protection_unclear",
        "data_authorization_unclear", "intellectual_property_risk", "conflict_of_interest",
        "unfair_impact", "evaluation_reputation_bias", "other",
    },
    "logical_rigor": {
        "causality_confusion", "overgeneralization", "unsupported_claim", "circular_reasoning",
        "missing_premise", "internal_inconsistency", "ignored_alternative", "evidence_mismatch",
        "selective_evidence", "unclear_research_question", "untestable_hypothesis",
        "method_question_mismatch", "other",
    },
    "innovation": {
        "novelty_unclear", "incremental_improvement", "simple_module_combination",
        "baseline_insufficient", "ablation_missing", "related_work_insufficient",
        "contribution_unsupported", "performance_gain_insignificant",
        "innovation_claim_exaggerated", "practical_value_unclear",
        "theoretical_value_unclear", "other",
    },
    "academic_impact": {
        "topic_novelty_insufficient", "theoretical_contribution_unclear",
        "argumentation_weak", "empirical_evidence_incomplete",
        "academic_relevance_unclear", "potential_impact_overstated",
        "literature_positioning_insufficient", "contribution_unsupported", "other",
    },
}
BIAS_ENUM = {
    "sample_selection_bias", "gender_bias", "age_bias", "regional_bias", "ethnic_bias",
    "language_bias", "economic_bias", "historical_data_bias", "survivorship_bias",
    "publication_bias", "prestige_bias", "citation_bias", "topic_popularity_bias", "other",
}
ISSUE_ALIASES = {
    "data_reliability": {},
    "ethics_bias": {},
    "logical_rigor": {},
    "innovation": {
        "method_unclear": "novelty_unclear",
        "method_missing": "novelty_unclear",
        "insufficient_method_detail": "novelty_unclear",
        "insufficient_information": "novelty_unclear",
        "evidence_insufficient": "contribution_unsupported",
        "unsupported_innovation": "contribution_unsupported",
        "no_baseline": "baseline_insufficient",
        "missing_related_work": "related_work_insufficient",
    },
    "academic_impact": {},
}

VALUE_ALIASES = {
    "low": "low", "medium": "medium", "high": "high",
    "低": "low", "低风险": "low", "轻微": "low",
    "中": "medium", "中风险": "medium", "中等": "medium", "一般": "medium",
    "高": "high", "高风险": "high", "严重": "high",
}

TOP_LEVEL_ALIASES = {
    "agent": "agent_name",
    "dimension": "dimension_name",
    "rating": "score",
    "confidence_score": "confidence",
    "risk": "risk_level",
    "overview": "summary",
    "conclusion": "summary",
    "pros": "strengths",
    "findings": "issues",
    "problems": "issues",
    "references": "evidence_refs",
    "citations": "evidence_refs",
    "reasoning": "reasoning_md",
    "analysis": "reasoning_md",
    "caveats": "limitations",
}

COMMON_FIELDS = {
    "agent_name": str,
    "dimension_name": str,
    "score": int,
    "confidence": (int, float),
    "risk_level": str,
    "summary": str,
    "strengths": list,
    "issues": list,
    "evidence_refs": list,
    "reasoning_md": str,
    "limitations": list,
}


@dataclass
class Validation:
    parse_ok: bool = False
    fields_ok: bool = False
    enums_ok: bool = False
    errors: list[str] = field(default_factory=list)


def normalize_output_structure(
    value: Any,
    agent: str,
    case_id: str,
) -> tuple[Any, list[dict[str, Any]]]:
    """Normalize common OpenAI-compatible model field drift without inventing evidence."""
    normalized = copy.deepcopy(value)
    log: list[dict[str, Any]] = []
    if not isinstance(normalized, dict):
        return normalized, log

    # OpenAI-compatible gateways sometimes wrap the requested object in a
    # result/output/data envelope even when JSON mode was requested.
    for wrapper in ("result", "output", "evaluation", "data"):
        candidate = normalized.get(wrapper)
        if isinstance(candidate, dict) and any(
            key in candidate for key in ("score", "rating", "issues", "findings")
        ):
            normalized = copy.deepcopy(candidate)
            log.append({
                "agent": agent,
                "case": case_id,
                "field": wrapper,
                "action": "unwrapped_compatible_envelope",
            })
            break

    for alias, canonical in TOP_LEVEL_ALIASES.items():
        if canonical not in normalized and alias in normalized:
            normalized[canonical] = normalized[alias]
            log.append({
                "agent": agent,
                "case": case_id,
                "field": canonical,
                "action": f"restored_from_alias:{alias}",
            })

    # Identity fields are protocol metadata rather than model judgements.
    if normalized.get("agent_name") != agent:
        normalized["agent_name"] = agent
        log.append({"agent": agent, "case": case_id, "field": "agent_name", "action": "restored_protocol_identity"})
    if normalized.get("dimension_name") != AGENTS[agent]:
        normalized["dimension_name"] = AGENTS[agent]
        log.append({"agent": agent, "case": case_id, "field": "dimension_name", "action": "restored_protocol_identity"})

    score = _coerce_number(normalized.get("score"))
    if score is not None:
        coerced_score = max(0, min(100, int(round(score))))
        if normalized.get("score") != coerced_score:
            log.append({"agent": agent, "case": case_id, "field": "score", "action": "coerced_numeric_value"})
        normalized["score"] = coerced_score

    confidence = _coerce_number(normalized.get("confidence"), percent=True)
    if confidence is not None:
        coerced_confidence = max(0.0, min(1.0, float(confidence)))
        if normalized.get("confidence") != coerced_confidence:
            log.append({"agent": agent, "case": case_id, "field": "confidence", "action": "coerced_numeric_value"})
        normalized["confidence"] = coerced_confidence

    if isinstance(normalized.get("score"), int):
        expected_risk = "low" if normalized["score"] >= 80 else "medium" if normalized["score"] >= 60 else "high"
        risk = VALUE_ALIASES.get(str(normalized.get("risk_level", "")).strip().lower())
        if risk != expected_risk:
            log.append({"agent": agent, "case": case_id, "field": "risk_level", "action": "aligned_with_score"})
        normalized["risk_level"] = expected_risk

    for field_name in ("strengths", "issues", "evidence_refs", "limitations"):
        field_value = normalized.get(field_name)
        if field_value is None:
            normalized[field_name] = []
            log.append({"agent": agent, "case": case_id, "field": field_name, "action": "restored_empty_array"})
        elif isinstance(field_value, str):
            normalized[field_name] = [field_value] if field_value.strip() else []
            log.append({"agent": agent, "case": case_id, "field": field_name, "action": "coerced_array"})

    if agent == "ethics_bias" and not isinstance(normalized.get("bias_detected"), list):
        normalized["bias_detected"] = []
        log.append({"agent": agent, "case": case_id, "field": "bias_detected", "action": "restored_empty_array"})
    if agent == "innovation" and not isinstance(normalized.get("innovation_types"), list):
        normalized["innovation_types"] = []
        log.append({"agent": agent, "case": case_id, "field": "innovation_types", "action": "restored_empty_array"})

    raw_refs = normalized.get("evidence_refs")
    normalized_refs: list[Any] = []
    if isinstance(raw_refs, list):
        for index, ref in enumerate(raw_refs):
            converted = ref
            if isinstance(ref, str) and ref.strip():
                converted = {"location": "论文原文", "quote": ref.strip()}
            elif isinstance(ref, dict):
                converted = copy.deepcopy(ref)
                if not isinstance(converted.get("quote"), str) or not converted["quote"].strip():
                    for alias in ("text", "evidence", "excerpt", "content", "citation"):
                        candidate = converted.get(alias)
                        if isinstance(candidate, str) and candidate.strip():
                            converted["quote"] = candidate.strip()
                            break
                if not isinstance(converted.get("location"), str) or not converted["location"].strip():
                    for alias in ("section", "source", "position", "chapter"):
                        candidate = converted.get(alias)
                        if isinstance(candidate, str) and candidate.strip():
                            converted["location"] = candidate.strip()
                            break
                    else:
                        page = converted.get("page")
                        converted["location"] = f"第 {page} 页" if isinstance(page, int) and page > 0 else "论文原文"
            if converted != ref:
                log.append({
                    "agent": agent,
                    "case": case_id,
                    "field": f"evidence_refs[{index}]",
                    "action": "normalized_compatible_shape",
                })
            normalized_refs.append(converted)
        normalized["evidence_refs"] = normalized_refs

    evidence_quotes = [
        str(ref.get("quote") or "").strip() if isinstance(ref, dict) else ""
        for ref in normalized_refs
    ]
    issues = normalized.get("issues")
    if isinstance(issues, list):
        for index, issue in enumerate(issues):
            if not isinstance(issue, dict):
                continue
            for alias, canonical in (
                ("type", "issue_type"), ("category", "issue_type"),
                ("level", "severity"), ("description", "evidence"),
                ("problem", "evidence"), ("recommendation", "suggestion"),
                ("advice", "suggestion"),
            ):
                if canonical not in issue and alias in issue:
                    issue[canonical] = issue[alias]
                    log.append({
                        "agent": agent,
                        "case": case_id,
                        "field": f"issues[{index}].{canonical}",
                        "action": f"restored_from_alias:{alias}",
                    })
            severity = VALUE_ALIASES.get(str(issue.get("severity", "")).strip().lower())
            if severity:
                issue["severity"] = severity
            if not isinstance(issue.get("evidence"), str) or not issue["evidence"].strip():
                replacement = ""
                for alias in ("quote", "evidence_text", "supporting_evidence", "basis"):
                    candidate = issue.get(alias)
                    if isinstance(candidate, str) and candidate.strip():
                        replacement = candidate.strip()
                        break
                if not replacement and index < len(evidence_quotes):
                    replacement = evidence_quotes[index]
                if replacement:
                    issue["evidence"] = replacement
                    log.append({
                        "agent": agent,
                        "case": case_id,
                        "field": f"issues[{index}].evidence",
                        "action": "restored_from_compatible_field",
                    })
            if not isinstance(issue.get("suggestion"), str) or not issue["suggestion"].strip():
                for alias in ("recommendation", "advice", "action", "remediation"):
                    candidate = issue.get(alias)
                    if isinstance(candidate, str) and candidate.strip():
                        issue["suggestion"] = candidate.strip()
                        log.append({
                            "agent": agent,
                            "case": case_id,
                            "field": f"issues[{index}].suggestion",
                            "action": "restored_from_compatible_field",
                        })
                        break

    return normalized, log


def _coerce_number(value: Any, *, percent: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().replace("％", "%")
        is_percent = text.endswith("%")
        if is_percent:
            text = text[:-1].strip()
        try:
            number = float(text)
        except ValueError:
            return None
        if is_percent:
            number /= 100
    else:
        return None
    if percent and number > 1:
        number /= 100
    return number


def normalize_issue_type_enums(value: Any, agent: str, case_id: str) -> tuple[Any, list[dict[str, Any]]]:
    """Normalize model enum drift while keeping an audit log."""
    normalized = copy.deepcopy(value)
    normalization_log: list[dict[str, Any]] = []
    if not isinstance(normalized, dict):
        return normalized, normalization_log

    issues = normalized.get("issues")
    if not isinstance(issues, list):
        return normalized, normalization_log

    legal_issue_types = ISSUE_ENUMS[agent]
    aliases = ISSUE_ALIASES.get(agent, {})
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            continue
        original_issue_type = issue.get("issue_type")
        if original_issue_type in legal_issue_types:
            continue
        normalized_issue_type = aliases.get(original_issue_type, "other")
        if normalized_issue_type not in legal_issue_types:
            normalized_issue_type = "other"
        issue["issue_type"] = normalized_issue_type
        normalization_log.append(
            {
                "agent": agent,
                "case": case_id,
                "issue_index": index,
                "original_issue_type": original_issue_type,
                "normalized_issue_type": normalized_issue_type,
            }
        )

    normalized["normalization_log"] = normalization_log
    return normalized, normalization_log


def validate_result(value: Any, expected_agent: str) -> Validation:
    """Validate common fields plus agent-specific enum constraints."""
    result = Validation(parse_ok=True)
    if not isinstance(value, dict):
        result.errors.append("顶层必须是 JSON object")
        return result

    for name, expected_type in COMMON_FIELDS.items():
        if name not in value:
            result.errors.append(f"缺少字段：{name}")
        elif name in {"score", "confidence"} and isinstance(value[name], bool):
            result.errors.append(f"字段类型错误：{name}")
        elif not isinstance(value[name], expected_type):
            result.errors.append(f"字段类型错误：{name}")

    required_special = "bias_detected" if expected_agent == "ethics_bias" else None
    if expected_agent == "innovation":
        required_special = "innovation_types"
    if required_special and not isinstance(value.get(required_special), list):
        result.errors.append(f"缺少专属数组字段或类型错误：{required_special}")
    if expected_agent == "academic_impact":
        validate_journal_recommendation(value.get("journal_recommendation"), result.errors)

    if value.get("agent_name") != expected_agent:
        result.errors.append(f"agent_name 应为 {expected_agent}")
    if value.get("dimension_name") != AGENTS[expected_agent]:
        result.errors.append(f"dimension_name 应为 {AGENTS[expected_agent]}")
    score = value.get("score")
    if isinstance(score, int) and not isinstance(score, bool) and not 0 <= score <= 100:
        result.errors.append("score 超出 0—100")
    confidence = value.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and not 0 <= confidence <= 1:
        result.errors.append("confidence 超出 0—1")

    if value.get("risk_level") not in RISK_ENUM:
        result.errors.append("risk_level 枚举非法")
    for index, issue in enumerate(value.get("issues", [])):
        if not isinstance(issue, dict):
            result.errors.append(f"issues[{index}] 必须是 object")
            continue
        for key in ("issue_type", "severity", "evidence", "suggestion"):
            if not isinstance(issue.get(key), str) or not issue[key]:
                result.errors.append(f"issues[{index}].{key} 缺失或类型错误")
        if issue.get("severity") not in SEVERITY_ENUM:
            result.errors.append(f"issues[{index}].severity 枚举非法")
        if issue.get("issue_type") not in ISSUE_ENUMS[expected_agent]:
            result.errors.append(f"issues[{index}].issue_type 枚举非法")
    for index, ref in enumerate(value.get("evidence_refs", [])):
        required = ("location", "quote")
        if not isinstance(ref, dict) or not all(isinstance(ref.get(key), str) and ref[key] for key in required):
            result.errors.append(f"evidence_refs[{index}] 结构错误")
            continue
        page = ref.get("page")
        if "page" in ref and (
            page is not None
            and (not isinstance(page, int) or isinstance(page, bool) or page <= 0)
        ):
            result.errors.append(f"evidence_refs[{index}].page 必须为 null 或正整数")
        block_id = ref.get("block_id")
        if "block_id" in ref and (
            block_id is not None
            and (not isinstance(block_id, str) or not block_id.strip())
        ):
            result.errors.append(f"evidence_refs[{index}].block_id 必须为 null 或非空字符串")
        paragraph = ref.get("paragraph")
        if "paragraph" in ref and (
            paragraph is not None
            and (not isinstance(paragraph, int) or isinstance(paragraph, bool) or paragraph < 0)
        ):
            result.errors.append(f"evidence_refs[{index}].paragraph 必须为 null 或非负整数")
    if expected_agent == "ethics_bias":
        for index, bias in enumerate(value.get("bias_detected", [])):
            keys = (
                "bias_type", "severity", "affected_group_or_factor",
                "evidence", "potential_impact", "suggestion",
            )
            if not isinstance(bias, dict) or not all(isinstance(bias.get(key), str) and bias[key] for key in keys):
                result.errors.append(f"bias_detected[{index}] 结构错误")
            elif bias["severity"] not in SEVERITY_ENUM:
                result.errors.append(f"bias_detected[{index}].severity 枚举非法")
            elif bias["bias_type"] not in BIAS_ENUM:
                result.errors.append(f"bias_detected[{index}].bias_type 枚举非法")
    if expected_agent == "innovation":
        invalid = set(value.get("innovation_types", [])) - INNOVATION_ENUM
        if invalid:
            result.errors.append(f"innovation_types 枚举非法：{sorted(invalid)}")

    result.fields_ok = not any("枚举非法" not in error for error in result.errors)
    result.enums_ok = not any("枚举非法" in error for error in result.errors)
    return result


def validate_journal_recommendation(value: Any, errors: list[str]) -> None:
    """Validate the academic-impact agent's journal tier recommendation."""
    if not isinstance(value, dict):
        errors.append("缺少专属字段或类型错误：journal_recommendation")
        return

    tier_fields = ("recommended_tier", "alternative_tier")
    list_fields = ("rationale", "readiness_gaps", "basis")
    for name in tier_fields:
        if name not in value:
            errors.append(f"journal_recommendation 缺少字段：{name}")
        elif value[name] is not None and (not isinstance(value[name], str) or not value[name].strip()):
            errors.append(f"journal_recommendation.{name} 必须为 null 或非空字符串")

    confidence = value.get("confidence")
    if "confidence" not in value:
        errors.append("journal_recommendation 缺少字段：confidence")
    elif (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        errors.append("journal_recommendation.confidence 必须为 0—1 的数字")

    for name in list_fields:
        items = value.get(name)
        if name not in value:
            errors.append(f"journal_recommendation 缺少字段：{name}")
        elif not isinstance(items, list):
            errors.append(f"journal_recommendation.{name} 必须为数组")
        elif any(not isinstance(item, str) or not item.strip() for item in items):
            errors.append(f"journal_recommendation.{name} 只能包含非空字符串")
