"""Retry policy and retry-feedback helpers."""

from __future__ import annotations

import copy
import random
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetryPolicy:
    api_retries: int = 2
    format_retries: int = 1
    quality_retries: int = 1
    base_delay_seconds: float = 1.0

    @property
    def max_attempts(self) -> int:
        return 1 + self.api_retries + self.format_retries + self.quality_retries

    def sleep_before_retry(self, attempt: int, retry_after: float | None = None) -> None:
        delay = (
            retry_after
            if retry_after is not None
            else self.base_delay_seconds * (2 ** max(0, attempt - 1))
        )
        delay += random.uniform(0.0, min(0.5, self.base_delay_seconds))
        time.sleep(delay)


def with_retry_feedback(
    case_data: dict[str, Any],
    *,
    agent: str,
    reason: str,
    problems: list[str],
    attempt: int,
    previous_output: str | None = None,
) -> dict[str, Any]:
    """Return a copied case payload with targeted feedback for the next run."""
    updated = copy.deepcopy(case_data)
    review_context = updated.setdefault("review_context", {})
    if not isinstance(review_context, dict):
        review_context = {}
        updated["review_context"] = review_context
    review_context["retry_feedback"] = {
        "agent": agent,
        "reason": reason,
        "problems": problems,
        "previous_attempt": attempt,
        "instruction": (
            "请把上一份结果作为待修正稿，只修正列出的问题；不要重新选择评价主题，"
            "不要新增输入中不存在的事实。最终仅输出一个完整、合法的 JSON object。"
        ),
    }
    if previous_output:
        review_context["retry_feedback"]["previous_output"] = previous_output[:16000]
    return updated
