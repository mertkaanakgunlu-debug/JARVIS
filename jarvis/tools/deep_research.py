"""Deep multi-step web research with citation synthesis (Faz 7).

Pipeline:
  1. Tavily search for URLs + snippets
  2. Fetch full content for each URL (webfetch.py)
  3. Synthesize with Gemini Pro — returns markdown with [N] citations
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jarvis.tools.web import tavily_search_raw
from jarvis.tools.webfetch import fetch_url

if TYPE_CHECKING:
    from jarvis.config import Settings

_SYNTHESIS_SYSTEM = """\
You are a research synthesizer for JARVIS. Given a research topic and content from
multiple web sources, write a comprehensive, well-structured markdown report.

Requirements:
- Use [N] inline citations for specific claims (e.g., "Studies show [2]...")
- Include a ## References section at the end with numbered entries
- Use ## and ### headings to structure the report
- Be factual and specific — prefer cited details over generalities
- Target length: 400–700 words (excluding references)
"""


def run_deep_research(
    topic: str,
    settings: "Settings",
    max_sources: int = 5,
) -> str:
    """Search → fetch → synthesize. Returns a markdown report with citations."""

    # ── Step 1: Search ──────────────────────────────────────────────────────────
    raw = tavily_search_raw(topic, settings.tavily_api_key, max_results=max_sources + 2)
    if not raw:
        return (
            f"[ERROR] No search results for '{topic}'. "
            "Check TAVILY_API_KEY in .env or try a different query."
        )

    # ── Step 2: Fetch full page content ────────────────────────────────────────
    sources: list[dict] = []
    for r in raw:
        url = r.get("url", "")
        if not url:
            continue
        title = r.get("title", url)
        snippet = r.get("content", "")

        content = fetch_url(url, settings, max_chars=3000)
        if content.startswith("[ERROR]"):
            content = snippet or content  # fall back to Tavily snippet
        sources.append(
            {"idx": len(sources) + 1, "title": title, "url": url, "content": content}
        )
        if len(sources) >= max_sources:
            break

    if not sources:
        return f"[ERROR] Could not retrieve any content for: {topic}"

    # ── Step 3: Build context ───────────────────────────────────────────────────
    context_parts = []
    for s in sources:
        header = f"[{s['idx']}] {s['title']}\n{s['url']}"
        context_parts.append(f"{header}\n\n{s['content'][:2500]}")
    context = "\n\n---\n\n".join(context_parts)

    user_msg = f"Research topic: {topic}\n\nSources:\n\n{context}"

    # ── Step 4: Synthesize with Gemini Pro ─────────────────────────────────────
    from jarvis.providers import cloud_extractors_enabled, note_degraded
    if not cloud_extractors_enabled(settings):
        note_degraded("deep_research")
        lines = [f"## Research: {topic}", "",
                 "*(Synthesis skipped — cloud LLM disabled by CLOUD_POLICY=off)*", ""]
        for s in sources:
            lines.append(f"### [{s['idx']}] {s['title']}")
            lines.append(f"*{s['url']}*")
            lines.append(s["content"][:600])
            lines.append("")
        return "\n".join(lines)

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        research_temperature = getattr(settings, "deep_research_temperature", 0.3)
        if settings.use_vertex:
            llm = ChatGoogleGenerativeAI(
                model=settings.vertex_model_primary,
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.google_cloud_region,
                temperature=research_temperature,
            )
        else:
            llm = ChatGoogleGenerativeAI(
                model=settings.cloud_model_pro,
                google_api_key=settings.gemini_api_key,
                temperature=research_temperature,
            )

        response = llm.invoke(
            [SystemMessage(content=_SYNTHESIS_SYSTEM), HumanMessage(content=user_msg)]
        )
        synthesis = response.content

    except Exception as exc:
        # Fallback: structured summary without LLM synthesis
        lines = [f"## Research: {topic}", "", f"*(Synthesis failed: {exc})*", ""]
        for s in sources:
            lines.append(f"### [{s['idx']}] {s['title']}")
            lines.append(f"*{s['url']}*")
            lines.append(s["content"][:600])
            lines.append("")
        synthesis = "\n".join(lines)

    return synthesis
