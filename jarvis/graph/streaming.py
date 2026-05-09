"""Streaming adapter: converts LangGraph astream output to an async text generator.

voice.py:speak_stream() expects AsyncIterator[str] of text deltas.
This adapter filters the LangGraph message stream to yield only the final
AI response chunks (skipping tool-call messages and intermediate states).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import AIMessageChunk


async def graph_stream_to_text(
    graph,
    state: dict,
    config: dict,
) -> AsyncGenerator[str, None]:
    """Yield text deltas from the agent node's final response.

    Skips tool-call chunks (these have tool_call_chunks but no text content).
    Skips messages from non-agent nodes (tools, critic).
    """
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
        content = chunk.content
        if isinstance(content, str) and content:
            yield content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        yield text
