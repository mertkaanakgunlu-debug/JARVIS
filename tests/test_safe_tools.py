"""Patch 1.2 Faz 1A — tool-body exceptions become [TOOL_ERROR] ToolMessages.

Regression anchor: live incident E15 (2026-07-16) — "Son 3 mailimi listele"
produced a real itu_mail list_unread call whose RuntimeError (undeclared
imap-tools dep) escaped LangGraph's default handle_tool_errors (it only
converts ToolInvocationError) and surfaced as an opaque HTTP 500.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END, MessagesState

from jarvis.graph.safe_tools import format_tool_error, make_safe_tool_node


def _tool_graph(tools: list):
    """Minimal graph so ToolNode runs with a real LangGraph runtime — a bare
    node.invoke() fails on missing injected-runtime config in langgraph 1.x."""
    builder = StateGraph(MessagesState)
    builder.add_node("tools", make_safe_tool_node(tools))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile()


# ── format_tool_error: categories + sanitization ─────────────────────────────

def test_dependency_missing_category_and_not_retryable():
    exc = RuntimeError("imap-tools not installed. Run: pip install imap-tools")
    text = format_tool_error("itu_mail", exc)
    assert text.startswith("[TOOL_ERROR]\n")
    assert "tool=itu_mail" in text
    assert "category=dependency_missing" in text
    assert "retryable=false" in text


def test_timeout_is_retryable():
    text = format_tool_error("url_read", TimeoutError("request timed out"))
    assert "category=timeout" in text
    assert "retryable=true" in text


def test_rate_limit_is_retryable():
    text = format_tool_error("web_search", Exception("429 RESOURCE_EXHAUSTED: quota"))
    assert "category=rate_limit" in text
    assert "retryable=true" in text


def test_auth_category():
    text = format_tool_error("gmail", Exception("invalid credential: token expired"))
    assert "category=auth" in text
    assert "retryable=false" in text


def test_unexpected_fallback():
    text = format_tool_error("plot_data", ValueError("boom"))
    assert "category=unexpected" in text


def test_message_is_single_line_and_truncated():
    exc = RuntimeError(("secret " * 100) + "\nTraceback (most recent call last):\n  File ...")
    text = format_tool_error("shell_run", exc)
    message_line = next(line for line in text.splitlines() if line.startswith("message="))
    assert "Traceback" not in message_line
    assert len(message_line) <= len("message=") + 203  # 200 chars + "..."


def test_empty_exception_message_falls_back_to_class_name():
    text = format_tool_error("file_read", KeyError())
    assert "message=KeyError" in text


# ── SafeToolNode: raising tool body → ToolMessage, not an exception ──────────

@tool
def exploding_tool(x: str) -> str:
    """Test tool that always raises an operational error."""
    raise RuntimeError("imap-tools not installed. Run: pip install imap-tools")


def _ai_message_calling(name: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"x": "hi"}, "id": "call-1", "type": "tool_call"}],
    )


@pytest.mark.asyncio
async def test_async_tool_exception_becomes_tool_error_message():
    graph = _tool_graph([exploding_tool])
    result = await graph.ainvoke({"messages": [_ai_message_calling("exploding_tool")]})
    tm = result["messages"][-1]
    assert isinstance(tm, ToolMessage)
    assert tm.tool_call_id == "call-1"
    assert tm.status == "error"
    assert tm.content.startswith("[TOOL_ERROR]")
    assert "tool=exploding_tool" in tm.content
    assert "category=dependency_missing" in tm.content


def test_sync_tool_exception_becomes_tool_error_message():
    graph = _tool_graph([exploding_tool])
    result = graph.invoke({"messages": [_ai_message_calling("exploding_tool")]})
    tm = result["messages"][-1]
    assert isinstance(tm, ToolMessage)
    assert tm.content.startswith("[TOOL_ERROR]")


@tool
def healthy_tool(x: str) -> str:
    """Test tool that works."""
    return f"ok:{x}"


@pytest.mark.asyncio
async def test_healthy_tool_passes_through_unchanged():
    graph = _tool_graph([healthy_tool])
    result = await graph.ainvoke({"messages": [_ai_message_calling("healthy_tool")]})
    tm = result["messages"][-1]
    assert tm.content == "ok:hi"
    assert tm.status != "error"
