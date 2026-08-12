"""Production configuration shared by the evaluation pipeline."""

from __future__ import annotations

import os
import re
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PACKAGE_ROOT / "prompts"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


MODEL = "qwen-plus-latest"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_TIMEOUT_SECONDS = 120.0
EVALUATION_MAX_WORKERS = max(1, min(5, _env_int("EVALUATION_MAX_WORKERS", 2)))
EVALUATION_REQUEST_INTERVAL_SECONDS = max(
    0.0, _env_float("EVALUATION_REQUEST_INTERVAL_SECONDS", 0.5)
)
EVALUATION_MAX_INPUT_CHARS = max(
    12000, min(120000, _env_int("EVALUATION_MAX_INPUT_CHARS", 50000))
)
EVALUATION_MAX_OUTPUT_TOKENS = max(
    2048, min(12000, _env_int("EVALUATION_MAX_OUTPUT_TOKENS", 6000))
)

AGENTS = {
    "data_reliability": "数据可靠性",
    "ethics_bias": "伦理与偏见",
    "logical_rigor": "逻辑严密性",
    "innovation": "创新性",
    "academic_impact": "学术影响力",
}

PROMPT_FILES = {
    "data_reliability": "data_reliability_prompt_v1.6.txt",
    "ethics_bias": "ethics_bias_prompt_v1.5.txt",
    "logical_rigor": "logical_rigor_prompt_v1.2.txt",
    "innovation": "innovation_prompt_v1.5.txt",
    "academic_impact": "academic_impact_prompt_v1.0.txt",
}


def current_prompts() -> dict[str, Path]:
    """Return the pinned production prompts after checking they exist."""
    prompts = {agent: PROMPTS_DIR / filename for agent, filename in PROMPT_FILES.items()}
    missing = [str(path) for path in prompts.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"缺少评价 Prompt：{', '.join(missing)}")
    return prompts


def prompt_version(path: Path) -> str:
    """Extract a version such as ``v1.6`` from a prompt filename."""
    match = re.search(r"_v(\d+(?:\.\d+)*)\.txt$", path.name)
    return f"v{match.group(1)}" if match else "unknown"
