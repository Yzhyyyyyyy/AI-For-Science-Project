"""Aliyun Bailian API client wrapper for evaluation pipeline runs."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .config import (
    BASE_URL,
    EVALUATION_REQUEST_INTERVAL_SECONDS,
    EVALUATION_MAX_OUTPUT_TOKENS,
    MODEL,
    MODEL_TIMEOUT_SECONDS,
)


logger = logging.getLogger("AIReview.ModelClient")


NON_RETRYABLE_CODES = {
    "insufficient_quota",
    "invalid_api_key",
    "authentication_error",
    "permission_denied",
}


def api_error_code(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            return str(error.get("code") or error.get("type") or "").lower()
    text = str(exc).lower()
    for code in NON_RETRYABLE_CODES:
        if code in text:
            return code
    status = getattr(exc, "status_code", None)
    if status == 402:
        return "insufficient_quota"
    if status in {401, 403}:
        return f"http_{status}"
    if status == 408:
        return "request_timeout"
    if status == 429:
        return "rate_limit"
    if isinstance(status, int) and status >= 500:
        return f"http_{status}"
    return type(exc).__name__.lower()


def is_non_retryable_api_error(exc: Exception) -> bool:
    code = api_error_code(exc)
    return code in NON_RETRYABLE_CODES or code in {"http_401", "http_403"}


def is_context_length_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "too many tokens",
        "prompt is too long",
        "input is too long",
    )
    return any(marker in text for marker in markers)


def _is_compatibility_parameter_error(exc: Exception) -> bool:
    """Detect OpenAI-compatible gateways that reject optional request fields."""
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    parameter_markers = (
        "response_format",
        "enable_thinking",
        "extra_body",
        "unsupported parameter",
        "unknown parameter",
        "unrecognized request argument",
    )
    return status in {400, 422} and any(marker in text for marker in parameter_markers)


def retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        try:
            return max(0.0, float(headers.get("retry-after")))
        except (TypeError, ValueError):
            return None
    return None


def normalize_base_url(raw: str) -> str:
    """Strip trailing /chat/completions (and variants) so the OpenAI SDK can append its own path.

    Many users paste a full endpoint URL like https://api.example.com/v1/chat/completions
    into the config form.  The SDK appends /chat/completions again, which produces a 404.
    This function normalises any known OpenAI-compatible suffix back to the API root.
    """
    import re
    url = raw.rstrip("/")
    # Remove trailing /chat/completions  (the SDK always appends this exact path)
    url = re.sub(r"/chat/completions$", "", url)
    # Remove trailing /completions  (some proxies use the older path)
    url = re.sub(r"/completions$", "", url)
    # Remove trailing /v1  just in case, but only if it was the final segment
    # (don't touch https://api.example.com/v1 -- keep it as-is; the SDK needs /v1)
    return url


@dataclass(frozen=True)
class ModelConfig:
    model: str = MODEL
    temperature: float = 0.0
    max_tokens: int = EVALUATION_MAX_OUTPUT_TOKENS
    enable_thinking: bool = False


class BailianClient:
    """Small wrapper around Bailian's OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        config: ModelConfig | None = None,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key,
            base_url=normalize_base_url(base_url),
            timeout=MODEL_TIMEOUT_SECONDS,
            max_retries=0,
        )
        self.config = config or ModelConfig()
        self._rate_lock = threading.Lock()
        self._last_request_started = 0.0

    def complete_json(self, prompt: str, case_text: str) -> str:
        with self._rate_lock:
            remaining = (
                EVALUATION_REQUEST_INTERVAL_SECONDS
                - (time.monotonic() - self._last_request_started)
            )
            if remaining > 0:
                time.sleep(remaining)
            self._last_request_started = time.monotonic()
        request = dict(
            model=self.config.model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": case_text},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            response_format={"type": "json_object"},
        )
        if self.config.model.lower().startswith("qwen"):
            request["extra_body"] = {"enable_thinking": self.config.enable_thinking}
        try:
            response = self._client.chat.completions.create(**request)
        except Exception as exc:
            if not _is_compatibility_parameter_error(exc):
                raise
            logger.info(
                "Gateway rejected optional JSON controls; retrying once with portable fields model=%s",
                self.config.model,
            )
            request.pop("response_format", None)
            request.pop("extra_body", None)
            response = self._client.chat.completions.create(**request)

        choices = getattr(response, "choices", None) or []
        if not choices:
            raise RuntimeError("API 返回中没有 choices")
        content = getattr(choices[0].message, "content", None)
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        if not content:
            raise RuntimeError("API 返回空内容")
        return str(content)
