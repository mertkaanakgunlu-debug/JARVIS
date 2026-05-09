"""ResearchAgent — specialist for web-augmented research."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent, RunContext

from jarvis.config import Settings
from jarvis.tools.web import tavily_search
from jarvis.utils import run_with_retry, build_cloud_model


@dataclass
class ResearchDeps:
    settings: Settings


def build_research_agent(settings: Settings) -> Agent[ResearchDeps, str]:
    prompt_path = Path(__file__).parent.parent / "prompts" / "research.md"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    model = build_cloud_model(settings)

    agent: Agent[ResearchDeps, str] = Agent(
        model=model,
        deps_type=ResearchDeps,
        output_type=str,
        system_prompt=system_prompt,
    )

    @agent.tool
    async def web_search(ctx: RunContext[ResearchDeps], query: str) -> str:
        """Search the web for current information.

        Args:
            query: A focused search query string.
        """
        return tavily_search(
            query=query,
            api_key=ctx.deps.settings.tavily_api_key,
            max_results=5,
        )

    return agent


async def run_research(query: str, settings: Settings) -> str:
    """Research a topic using web search. Returns LaTeX-formatted body content."""
    agent = build_research_agent(settings)
    deps = ResearchDeps(settings=settings)
    result = await run_with_retry(
        lambda: agent.run(query, deps=deps), label="ResearchAgent"
    )
    return result.output
