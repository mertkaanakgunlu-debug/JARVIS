"""CoderAgent — specialist for code generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent

from jarvis.config import Settings
from jarvis.utils import run_with_retry, build_cloud_model


@dataclass
class CoderDeps:
    settings: Settings


def build_coder_agent(settings: Settings) -> Agent[CoderDeps, str]:
    prompt_path = Path(__file__).parent.parent / "prompts" / "coder.md"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    model = build_cloud_model(settings)

    agent: Agent[CoderDeps, str] = Agent(
        model=model,
        deps_type=CoderDeps,
        output_type=str,
        system_prompt=system_prompt,
    )
    return agent


async def run_coder(spec: str, settings: Settings) -> str:
    """Generate code per a specification. Returns LaTeX-formatted body content."""
    agent = build_coder_agent(settings)
    deps = CoderDeps(settings=settings)
    result = await run_with_retry(
        lambda: agent.run(spec, deps=deps), label="CoderAgent"
    )
    return result.output
