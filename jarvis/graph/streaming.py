"""Streaming adapter: converts LangGraph astream output to an async text generator.

voice.py:speak_stream() expects AsyncIterator[str] of text deltas.
This adapter filters the LangGraph message stream to yield only the final
AI response chunks (skipping tool-call messages and intermediate states).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from langchain_core.messages import AIMessageChunk


async def graph_stream_to_text(
    graph,
    state: dict,
    config: dict,
) -> AsyncGenerator[str, None]:
    """Yield text deltas from the agent node's final response.

    Skips tool-call chunks (these have tool_call_chunks but no text content).
    Skips messages from non-agent nodes (tools, critic).

    BUG-12: when the critic requests a revision, the "agent" node runs a second
    time in the same turn (up to one retry — see route_from_critic's
    revise_count < 2 cap) — both the draft and the revision are tagged
    langgraph_node="agent", so the node-name filter alone can't tell them apart.
    Without a separator the reconstructed text is the draft immediately
    followed by the revision, garbled with no boundary. metadata["langgraph_step"]
    (a real, populated key — see langgraph's pregel/_algo.py) increments between
    the two passes, so a change in it marks the boundary.
    """
    last_step: int | None = None
    async for chunk, metadata in graph.astream(
        state,
        config,
        stream_mode="messages",
    ):
        if metadata.get("langgraph_node") != "agent":
            continue
        if not isinstance(chunk, AIMessageChunk):
            continue
        if getattr(chunk, "tool_call_chunks", None):
            continue
        step = metadata.get("langgraph_step")
        if last_step is not None and step != last_step:
            yield "\n\n"
        last_step = step
        content = chunk.content
        if isinstance(content, str) and content:
            yield content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        yield text
