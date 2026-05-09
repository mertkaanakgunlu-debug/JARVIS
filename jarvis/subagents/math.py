"""MathAgent — specialist for mathematical problem solving."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent

from jarvis.config import Settings
from jarvis.utils import run_with_retry, build_cloud_model


@dataclass
class MathDeps:
    settings: Settings


def build_math_agent(settings: Settings) -> Agent[MathDeps, str]:
    prompt_path = Path(__file__).parent.parent / "prompts" / "math.md"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    model = build_cloud_model(settings)

    agent: Agent[MathDeps, str] = Agent(
        model=model,
        deps_type=MathDeps,
        output_type=str,
        system_prompt=system_prompt,
    )
    return agent


async def run_math(problem: str, settings: Settings) -> str:
    """Solve a math problem. Returns LaTeX-formatted body content."""
    agent = build_math_agent(settings)
    deps = MathDeps(settings=settings)
    result = await run_with_retry(
        lambda: agent.run(problem, deps=deps), label="MathAgent"
    )
    return result.output
