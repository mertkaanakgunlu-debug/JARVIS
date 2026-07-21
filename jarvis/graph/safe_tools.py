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

Agent Runtime rev.2, Faz 3: _awrap_tool_call now also bounds every call by
ToolSpec.timeout_seconds (declared since Phase 2, never actually applied
anywhere until now) via asyncio.wait_for, and format_tool_error() reports
two honest facts about ANY timeout it formats -- execution_may_still_be_
running and worker_terminated -- derived from the tool's timeout_class and
the concrete exception type. See tool_registry.py's _TIMEOUT_CLASSES
docstring for what each class means and why. The sync _wrap_tool_call below
is deliberately left WITHOUT timeout enforcement: there is no running event
loop inside a plain `def` to bound anything with, and nothing in this
codebase's real entry points ever drives the compiled graph through its sync
.invoke() path (only ainvoke()/astream() -- see jarvis/agent.py) -- the only
caller of that path today is test_safe_tools.py's own sync-dispatch test.
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt import ToolNode

from jarvis.tool_registry import get_spec

_MAX_MESSAGE_CHARS = 200

# Retry semantics per category: transient infrastructure conditions are
# retryable; everything else (missing dep, bad auth, absent file, logic
# errors) will fail identically on retry and must not be re-attempted.
_RETRYABLE_CATEGORIES = frozenset({"timeout", "network", "rate_limit"})

# Faz 3: headroom given to a hard_process_timeout tool's OUTER bound beyond
# its own ToolSpec.timeout_seconds -- the PRIMARY kill mechanism is the
# subprocess.run(timeout=...) call inside the tool itself (shell.py/
# python_exec.py/latex.py), which should raise subprocess.TimeoutExpired
# well before this outer wait_for would ever fire. This is a defensive
# backstop, not the real enforcement, so it deliberately trails rather than
# races the inner one.
_HARD_PROCESS_BUFFER_SEC = 10.0

# Default timeout (seconds) for a call with no registered ToolSpec at all
# (an MCP tool that somehow wasn't registered, or a genuinely unknown name) --
# matches ToolSpec's own dataclass default so an unclassified tool gets the
# same bound an unclassified static one would.
_DEFAULT_TIMEOUT_SECONDS = 60.0


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
    """Render an exception as the structured [TOOL_ERROR] block the model sees.

    Faz 3: for category=="timeout" (from ANY source -- this outer module's
    own asyncio.wait_for, a subprocess.TimeoutExpired propagated up from
    shell.py/python_exec.py/latex.py, or in principle any future per-library
    client timeout that raises something matching _categorize()'s "timed
    out"/TimeoutError check), two more honest facts are appended:
      execution_may_still_be_running -- false ONLY for cooperative_async
        tools, where asyncio's own cancellation genuinely stops the coroutine
        at its next await point; true for every other class, where giving up
        on the await does not stop the underlying thread/subprocess/socket.
      worker_terminated -- true ONLY for subprocess.TimeoutExpired, the one
        exception type that GUARANTEES Python actually killed the child
        process (documented subprocess module behavior); false otherwise,
        including for a bare TimeoutError/asyncio.TimeoutError, which has no
        separate "worker" to have terminated at all.
    """
    category = _categorize(exc)
    lines = str(exc).strip().splitlines()
    first_line = lines[0].strip() if lines else ""
    if not first_line:
        first_line = type(exc).__name__
    if len(first_line) > _MAX_MESSAGE_CHARS:
        first_line = first_line[:_MAX_MESSAGE_CHARS] + "..."
    retryable = "true" if category in _RETRYABLE_CATEGORIES else "false"
    block = (
        "[TOOL_ERROR]\n"
        f"tool={tool_name}\n"
        f"category={category}\n"
        f"message={first_line}\n"
        f"retryable={retryable}"
    )
    if category == "timeout":
        spec = get_spec(tool_name)
        timeout_class = spec.timeout_class if spec is not None else "soft_thread_timeout"
        still_running = timeout_class != "cooperative_async"
        worker_terminated = isinstance(exc, subprocess.TimeoutExpired)
        block += (
            f"\nexecution_may_still_be_running={'true' if still_running else 'false'}"
            f"\nworker_terminated={'true' if worker_terminated else 'false'}"
        )
    return block


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


def _timeout_bound_for(name: str) -> float:
    """The outer asyncio.wait_for bound for this tool -- ToolSpec.
    timeout_seconds, with extra headroom for hard_process_timeout tools (see
    _HARD_PROCESS_BUFFER_SEC's own comment for why the outer bound
    deliberately trails the inner subprocess-level one rather than racing it)."""
    spec = get_spec(name)
    if spec is None:
        return _DEFAULT_TIMEOUT_SECONDS
    if spec.timeout_class == "hard_process_timeout":
        return spec.timeout_seconds + _HARD_PROCESS_BUFFER_SEC
    return float(spec.timeout_seconds)


async def _awrap_tool_call(request: Any, execute: Any) -> Any:
    call = getattr(request, "tool_call", None) or {}
    name = call.get("name") or "unknown"
    try:
        return await asyncio.wait_for(execute(request), timeout=_timeout_bound_for(name))
    except GraphInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001 — this boundary is the point (includes asyncio.TimeoutError)
        return _error_tool_message(request, exc)


def make_safe_tool_node(tools: list) -> ToolNode:
    """ToolNode whose tool-body exceptions become [TOOL_ERROR] ToolMessages."""
    return ToolNode(tools, wrap_tool_call=_wrap_tool_call, awrap_tool_call=_awrap_tool_call)
