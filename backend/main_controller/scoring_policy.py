"""v5.1 cross-discipline scoring policy with four weight modes."""

from __future__ import annotations

from typing import Any

DIMENSIONS = (
    "data_reliability",
    "logical_rigor",
    "innovation",
    "academic_impact",
    "ethics_bias",
)

# ── v5.1 four-mode weight strategies ──────────────────────────────────

# 1. Social Sciences & Humanities — Mentor-approved v5.0 preserved
HUMANITIES_SOCIAL_SCIENCE_WEIGHTS = {
    "data_reliability": 0.25,
    "logical_rigor": 0.20,
    "innovation": 0.25,
    "academic_impact": 0.20,
    "ethics_bias": 0.10,
}

# 2. STEM & Experimental Sciences
STEM_EXPERIMENTAL_SCIENCE_WEIGHTS = {
    "data_reliability": 0.30,
    "logical_rigor": 0.25,
    "innovation": 0.20,
    "academic_impact": 0.15,
    "ethics_bias": 0.10,
}

# 3. Medical & Life Sciences
MEDICAL_LIFE_SCIENCE_WEIGHTS = {
    "data_reliability": 0.30,
    "logical_rigor": 0.20,
    "innovation": 0.15,
    "academic_impact": 0.15,
    "ethics_bias": 0.20,
}

# Backward-compatible aliases
HUMANITIES_WEIGHTS = HUMANITIES_SOCIAL_SCIENCE_WEIGHTS
SCIENCE_ENGINEERING_WEIGHTS = STEM_EXPERIMENTAL_SCIENCE_WEIGHTS

# ── Domain / subject → policy routing ─────────────────────────────────

PRESET_POLICY_MAP: dict[str, dict[str, Any]] = {
    "social_sciences": {"weights": HUMANITIES_SOCIAL_SCIENCE_WEIGHTS, "policy": "humanities_social_science_v1_1", "policy_label": "社会科学与人文锁定权重"},
    "stem": {"weights": STEM_EXPERIMENTAL_SCIENCE_WEIGHTS, "policy": "stem_experimental_science_v1_1", "policy_label": "理工与实验科学锁定权重"},
    "medicine": {"weights": MEDICAL_LIFE_SCIENCE_WEIGHTS, "policy": "medical_life_science_v1_1", "policy_label": "医学与生命科学锁定权重"},
}

SUBJECT_TOP_TO_DOMAIN: dict[str, str] = {
    "人文学科": "social_sciences",
    "纯理科": "stem",
    "交叉工科": "stem",
}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def resolve_subject_profile(paper_data: dict[str, Any]) -> dict[str, str]:
    metadata = paper_data.get("metadata") if isinstance(paper_data.get("metadata"), dict) else {}
    context = paper_data.get("review_context") if isinstance(paper_data.get("review_context"), dict) else {}
    return {
        "subject_top": _first_text(paper_data.get("subject_top"), metadata.get("subject_top"), context.get("subject_top")),
        "subject_sub": _first_text(paper_data.get("subject_sub"), metadata.get("subject_sub"), context.get("subject_sub")),
        "paper_type": _first_text(paper_data.get("paper_type"), metadata.get("paper_type"), context.get("paper_type")),
    }


# ── Custom weights validation ─────────────────────────────────────────

def validate_custom_weights(custom_weights: dict[str, Any]) -> dict[str, float]:
    if not isinstance(custom_weights, dict):
        raise ValueError("自定义权重必须为 JSON 对象")
    missing = [name for name in DIMENSIONS if name not in custom_weights]
    if missing:
        raise ValueError(f"自定义权重缺少维度：{', '.join(missing)}")
    extra = [k for k in custom_weights if k not in DIMENSIONS]
    if extra:
        raise ValueError(f"自定义权重包含未知维度：{', '.join(extra)}")

    validated: dict[str, float] = {}
    for name in DIMENSIONS:
        value = custom_weights[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"自定义权重 {name} 必须为数字，当前：{type(value).__name__}")
        validated[name] = float(value)

    for name, value in validated.items():
        if value < 0.0 or value > 1.0:
            raise ValueError(f"自定义权重 {name} 必须在 0 到 1 之间，当前：{value}")

    total = sum(validated.values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(f"自定义权重总和必须为 100%，当前：{round(total * 100, 1)}%")
    return validated


# ── Policy selection ──────────────────────────────────────────────────

def scoring_policy_for(
    paper_data: dict[str, Any],
    review_mode: str = "preset",
    custom_weights: dict[str, Any] | None = None,
    domain_hint: str | None = None,
) -> dict[str, Any]:
    # v5.1: custom mode takes priority
    if review_mode == "custom":
        if custom_weights is None:
            raise ValueError("自定义审查模式必须提供 custom_weights")
        weights = validate_custom_weights(custom_weights)
        return {
            "policy": "custom_user_defined_v1_1",
            "policy_label": "用户自定义权重",
            "locked": False,
            "source": "user_custom",
            "weights": weights,
        }

    # preset mode: pick by domain_hint or subject detection
    profile = resolve_subject_profile(paper_data)
    source = "backend_subject_detection"

    if domain_hint and domain_hint in PRESET_POLICY_MAP:
        preset = PRESET_POLICY_MAP[domain_hint]
        source = f"mode_preset_{domain_hint}"
    elif profile.get("subject_top") and profile["subject_top"] in SUBJECT_TOP_TO_DOMAIN:
        mapped = SUBJECT_TOP_TO_DOMAIN[profile["subject_top"]]
        preset = PRESET_POLICY_MAP[mapped]
    else:
        preset = PRESET_POLICY_MAP["social_sciences"]
        source = "fallback_default"

    assert abs(sum(preset["weights"].values()) - 1.0) < 1e-9
    return {
        "policy": preset["policy"],
        "policy_label": preset["policy_label"],
        "locked": True,
        "source": source,
        "weights": dict(preset["weights"]),
        **profile,
    }


def weighted_score(final_results: dict[str, Any], policy: dict[str, Any]) -> float | None:
    weights = policy.get("weights") if isinstance(policy.get("weights"), dict) else {}
    values: list[tuple[float, float]] = []
    for name in DIMENSIONS:
        item = final_results.get(name)
        score = item.get("score") if isinstance(item, dict) else None
        weight = weights.get(name)
        if isinstance(score, (int, float)) and isinstance(weight, (int, float)):
            values.append((float(score), float(weight)))
    if len(values) != len(DIMENSIONS):
        return None
    return round(sum(score * weight for score, weight in values), 1)


def attach_scoring_policy(
    result: dict[str, Any],
    paper_data: dict[str, Any],
    review_mode: str = "preset",
    custom_weights: dict[str, Any] | None = None,
    domain_hint: str | None = None,
) -> None:
    policy = scoring_policy_for(paper_data, review_mode=review_mode, custom_weights=custom_weights, domain_hint=domain_hint)
    result["scoring_policy"] = policy
    result["weighted_score"] = weighted_score(result.get("final_results", {}), policy)
