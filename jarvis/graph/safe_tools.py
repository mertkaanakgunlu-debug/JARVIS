"""Last-resort error boundary around tool execution (Patch 1.2, Faz 1A).

Why this exists: LangGraph's default ``handle_tool_errors`` only converts
``ToolInvocationError`` (argument-validation failures) into a ToolMessage —
any exception raised by the tool *body* is re-raised, which kills the whole
graph run and surfaces to the client as an opaque HTTP 500 (live incident
E15, 2026-07-16: itu_mail's RuntimeError for the undeclared imap-tools dep).

Operational failures inside a tool (missing package, missing credential,
network timeout, rate limit, file not found) must come back to the model as
a structured ToolMessage it can react to, not crash the turn. The
``wrap_tool_call``/``awrap_tool_call`` hooks are the supported interception
point that still knows *which* call failed — ``handle_tool_errors``'s
callable form receives only the exception object, so it could never fill
the ``tool=`` field below (the reason a plain formatter wasn't enough).

The error text is deliberately sanitized: first line of str(exc) only,
truncated — never repr() or a traceback, which can leak credentials, tokens
or filesystem paths into the conversation (and from there into session
history / the HUD).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt import ToolNode

_MAX_MESSAGE_CHARS = 200

# Retry semantics per category: transient infrastructure conditions are
# retryable; everything else (missing dep, bad auth, absent file, logic
# errors) will fail identically on retry and must not be re-attempted.
_RETRYABLE_CATEGORIES = frozenset({"timeout", "network", "rate_limit"})


def _categorize(exc: BaseException) -> str:
    """Map an exception to a small, stable category vocabulary.

    Ordered from most to least specific; substring probes use full words
    ("authentication", not "auth") — the _is_trivially_simple substring
    false-positive incident applies here too.
    """
    msg = str(exc).lower()
    if isinstance(exc, (ImportError, ModuleNotFoundError)) or (
        "not installed" in msg or "no module named" in msg
    ):
        return "dependency_missing"
    if isinstance(exc, TimeoutError) or "timed out" in msg or "timeout" in msg:
        return "timeout"
    if "429" in msg or "rate limit" in msg or "quota" in msg or "resource_exhausted" in msg:
        return "rate_limit"
    if any(
        s in msg
        for s in (
            "credential", "unauthorized", "401", "403", "oauth",
            "authentication", "permission denied", "login failed",
        )
    ):
        return "auth"
    if isinstance(exc, FileNotFoundError) or "not found" in msg or "no such file" in msg:
        return "not_found"
    if isinstance(exc, (ConnectionError, OSError)) or "connection" in msg:
        return "network"
    return "unexpected"


def format_tool_error(tool_name: str, exc: BaseException) -> str:
    """Render an exception as the structured [TOOL_ERROR] block the model sees."""
    category = _categorize(exc)
    lines = str(exc).strip().splitlines()
    first_line = lines[0].strip() if lines else ""
    if not first_line:
        first_line = type(exc).__name__
    if len(first_line) > _MAX_MESSAGE_CHARS:
        first_line = first_line[:_MAX_MESSAGE_CHARS] + "..."
    retryable = "true" if category in _RETRYABLE_CATEGORIES else "false"
    return (
        "[TOOL_ERROR]\n"
        f"tool={tool_name}\n"
        f"category={category}\n"
        f"message={first_line}\n"
        f"retryable={retryable}"
    )


def _error_tool_message(request: Any, exc: BaseException) -> ToolMessage:
    call = getattr(request, "tool_call", None) or {}
    name = call.get("name") or "unknown"
    return ToolMessage(
        content=format_tool_error(name, exc),
        name=name,
        tool_call_id=call.get("id") or "",
        status="error",
    )


def _wrap_tool_call(request: Any, execute: Any) -> Any:
    try:
        return execute(request)
    except GraphInterrupt:
        raise  # confirmation-gate interrupts must keep propagating untouched
    except Exception as exc:  # noqa: BLE001 — this boundary is the point
        return _error_tool_message(request, exc)


async def _awrap_tool_call(request: Any, execute: Any) -> Any:
    try:
        return await execute(request)
    except GraphInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error_tool_message(request, exc)


def make_safe_tool_node(tools: list) -> ToolNode:
    """ToolNode whose tool-body exceptions become [TOOL_ERROR] ToolMessages."""
    return ToolNode(tools, wrap_tool_call=_wrap_tool_call, awrap_tool_call=_awrap_tool_call)
