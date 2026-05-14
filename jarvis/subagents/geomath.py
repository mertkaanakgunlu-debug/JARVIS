"""GeoMathAgent — specialist for geophysical mathematics and wave modeling (Faz 18)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent

from jarvis.config import Settings
from jarvis.utils import run_with_retry, build_cloud_model


@dataclass
class GeoMathDeps:
    settings: Settings


def build_geomath_agent(settings: Settings) -> "Agent[GeoMathDeps, str]":
    prompt_path = Path(__file__).parent.parent / "prompts" / "geomath.md"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    model = build_cloud_model(settings)

    agent: "Agent[GeoMathDeps, str]" = Agent(
        model=model,
        deps_type=GeoMathDeps,
        output_type=str,
        system_prompt=system_prompt,
    )
    return agent


async def run_geomath(problem: str, settings: Settings) -> str:
    """Delegate a geophysics/math problem to the GeoMathAgent.

    Returns LaTeX-formatted body content with full derivations.
    Use for: wave equations, seismic modeling, geophysical inversion,
    AVO analysis, rock physics, Fourier/Radon transforms.
    """
    agent = build_geomath_agent(settings)
    deps = GeoMathDeps(settings=settings)
    result = await run_with_retry(
        lambda: agent.run(problem, deps=deps), label="GeoMathAgent"
    )
    return result.output
