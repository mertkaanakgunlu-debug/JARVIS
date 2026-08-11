"""NVIDIA NIM request profiles for the cloud model tournament.

NIM exposes an OpenAI-compatible endpoint, but its models do not share one
sampling/reasoning contract.  Keep the documented per-model differences here
instead of copying Ollama kwargs into every NVIDIA request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

NVIDIA_TOURNAMENT_MODELS: tuple[str, ...] = (
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "z-ai/glm-5.2",
    "mistralai/mistral-medium-3.5-128b",
)


@dataclass(frozen=True)
class NvidiaRequestProfile:
    """OpenAI-compatible request kwargs documented for one NIM model/role."""

    temperature: float
    top_p: float
    seed: int | None = None
    reasoning_effort: str | None = None
    use_reasoning_budget: bool = False
    extra_body: dict[str, Any] = field(default_factory=dict)


def request_profile(
    model_id: str,
    role: Literal["fast", "reasoning"],
) -> NvidiaRequestProfile:
    """Return the documented profile; reject unreviewed tournament models.

    A promoted runtime model must have an explicit profile.  Silently applying
    another model's reasoning controls can make a valid endpoint fail only when
    a real user turn reaches it.
    """
    if model_id in {
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-3-ultra-550b-a55b",
    }:
        template = {
            "enable_thinking": role == "reasoning",
            "force_nonempty_content": True,
        }
        if role == "reasoning" and model_id.endswith("ultra-550b-a55b"):
            template["medium_effort"] = True
        return NvidiaRequestProfile(
            temperature=1.0,
            top_p=0.95,
            use_reasoning_budget=(
                role == "reasoning" and model_id.endswith("super-120b-a12b")
            ),
            extra_body={"chat_template_kwargs": template},
        )

    if model_id == "z-ai/glm-5.2":
        return NvidiaRequestProfile(temperature=1.0, top_p=1.0, seed=42)

    if model_id == "mistralai/mistral-medium-3.5-128b":
        if role == "reasoning":
            return NvidiaRequestProfile(
                temperature=0.7,
                top_p=1.0,
                reasoning_effort="high",
            )
        return NvidiaRequestProfile(
            temperature=0.0,
            top_p=1.0,
            reasoning_effort="none",
        )

    raise ValueError(f"No reviewed NVIDIA request profile for {model_id!r}")
