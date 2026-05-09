"""WriterAgent — specialist for academic prose generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent

from jarvis.config import Settings
from jarvis.utils import run_with_retry, build_cloud_model


@dataclass
class WriterDeps:
    settings: Settings


def build_writer_agent(settings: Settings) -> Agent[WriterDeps, str]:
    prompt_path = Path(__file__).parent.parent / "prompts" / "writer.md"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    model = build_cloud_model(settings)

    agent: Agent[WriterDeps, str] = Agent(
        model=model,
        deps_type=WriterDeps,
        output_type=str,
        system_prompt=system_prompt,
    )
    return agent


async def run_writer(topic: str, style: str, settings: Settings) -> str:
    """Write academic content. Returns LaTeX-formatted body content."""
    agent = build_writer_agent(settings)
    deps = WriterDeps(settings=settings)
    prompt = f"Topic: {topic}\nStyle/instructions: {style}"
    result = await run_with_retry(
        lambda: agent.run(prompt, deps=deps), label="WriterAgent"
    )
    return result.output
