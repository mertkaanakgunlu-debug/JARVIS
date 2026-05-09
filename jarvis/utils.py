"""Shared utilities for JARVIS."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, Callable, Coroutine, TypeVar

if TYPE_CHECKING:
    from jarvis.config import Settings

T = TypeVar("T")


def build_cloud_model(settings: "Settings", model_id: str | None = None):
    """Return a Groq model if groq_api_key is configured, else a Gemini model.

    All sub-agents and the orchestrator call this so there is one place to
    change the cloud provider.
    """
    if settings.groq_api_key:
        from pydantic_ai.models.openai import OpenAIModel
        from pydantic_ai.providers.openai import OpenAIProvider
        mid = model_id or settings.groq_model
        return OpenAIModel(
            mid,
            provider=OpenAIProvider(
                base_url=settings.groq_api_url,
                api_key=settings.groq_api_key,
            ),
        )
    from pydantic_ai.models.gemini import GeminiModel
    from pydantic_ai.providers.google_gla import GoogleGLAProvider
    gemini_model_id = model_id or settings.effective_cloud_model
    return GeminiModel(
        gemini_model_id,
        provider=GoogleGLAProvider(api_key=settings.gemini_api_key or None),
    )


def is_daily_quota_error(exc: BaseException) -> bool:
    """True when a Gemini error is a daily-quota exhaustion (not a per-minute rate limit).

    Daily quotas reset at midnight UTC, not after retryDelay — retrying is futile.
    Detected via Gemini's quotaId pattern, e.g. ``GenerateRequestsPerDayPerProjectPerModel-FreeTier``.
    """
    msg = str(exc)
    return "PerDay" in msg or "RequestsPerDay" in msg


async def run_with_retry(
    coro_factory: Callable[[], Coroutine[Any, Any, T]],
    max_retries: int = 3,
    label: str = "Gemini",
) -> T:
    """Run an async call, retrying automatically on 429/503 transient errors.

    Skips retry when the error is a daily-quota exhaustion (callers should
    handle those by switching to a different model). Uses the retry delay
    suggested by the API; falls back to exponential backoff (5s, 10s, 20s).
    """
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as exc:
            msg = str(exc)
            msg_lower = msg.lower()
            is_transient = (
                "429" in msg
                or "RESOURCE_EXHAUSTED" in msg
                or "503" in msg
                or "UNAVAILABLE" in msg
                or "overloaded" in msg_lower
            )
            # Daily quota exhausted → don't burn retries; bubble up to caller
            # so it can switch to a fallback model.
            if is_daily_quota_error(exc):
                raise
            if is_transient and attempt < max_retries - 1:
                # Extract the API-suggested wait time, e.g. "retryDelay": "7s"
                match = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', msg)
                wait = float(match.group(1)) + 1.0 if match else 5.0 * (2 ** attempt)
                print(
                    f"\n[JARVIS] {label} transient error — "
                    f"retrying in {wait:.0f}s (attempt {attempt + 1}/{max_retries})..."
                )
                await asyncio.sleep(wait)
                continue
            raise
    raise RuntimeError(f"{label} still rate-limited after {max_retries} attempts.")
