"""Streaming adapter: converts LangGraph astream output to an async text generator.

voice.py:speak_stream() expects AsyncIterator[str] of text deltas.
This adapter filters the LangGraph message stream to yield only the final
AI response chunks (skipping tool-call messages and intermediate states).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from langchain_core.messages import AIMessageChunk

from jarvis.graph.nodes import REPAIR_STREAM_TAG


async def graph_stream_to_text(
    graph,
    state: dict,
    config: dict,
) -> AsyncGenerator[str, None]:
    """Yield text deltas from the answer-producing nodes' responses.

    Skips tool-call chunks (these have tool_call_chunks but no text content).
    Skips messages from non-answer nodes (tools, critic, planner).

    Faz 2B: the final user-facing answer of a tool turn now comes from the
    "compose" node (bare model), not the tool-bound "agent" — live incident
    during the Faz 3 A/B: an approved shell_run executed fine but the resumed
    stream was EMPTY because this filter only knew "agent". Both are streamed:
    agent covers conversation/no-tool turns (agent → critic directly) plus
    any pre-tool prose, compose covers tool-turn finals and revisions.

    BUG-12: when the critic requests a revision, the answering node runs a
    second time in the same turn (up to one retry — see route_from_critic's
    revise_count < 2 cap) — both passes carry the same langgraph_node, so the
    node-name filter alone can't tell them apart. Without a separator the
    reconstructed text is the draft immediately followed by the revision,
    garbled with no boundary. metadata["langgraph_step"] (a real, populated
    key — see langgraph's pregel/_algo.py) increments between the two passes,
    so a change in it marks the boundary.
    """
    last_step: int | None = None
    async for chunk, metadata in graph.astream(
        state,
        config,
        stream_mode="messages",
    ):
        if metadata.get("langgraph_node") not in ("agent", "compose"):
            continue
        # Post-MVP Faz 1: compose_node's bounded repair round is a SECOND
        # answer from the same node in the same langgraph_step, so the
        # step-boundary rule above cannot separate it -- untagged, a stream
        # consumer would get the discarded draft and its replacement spliced
        # together. The repair's result reaches the user through the node's
        # returned state, not through this stream.
        if REPAIR_STREAM_TAG in (metadata.get("tags") or ()):
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
