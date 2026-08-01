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

Post-MVP Faz 1 (honesty kernel): both wrappers now also open a per-call
artifact declaration sink (jarvis/execution/artifacts.py) and attach
whatever the tool declared to the outgoing ToolMessage.artifact. These hooks
are the right home for it for the same reason the docstring gives above --
they are the one interception point that still knows WHICH call is running,
and they already bracket every tool invocation exactly once.
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt import ToolNode

from jarvis.execution import artifacts
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


def is_unknown_outcome(tool_name: str, action: str, still_running: bool) -> bool:
    """Whether a timeout leaves an EXTERNAL side effect in an unknown state.

    Post-MVP Faz 2.75, Paket D. "Timed out" and "did not happen" are different
    claims, and for an external write the difference is a duplicate:

        gmail send -> JARVIS reports a timeout -> the HTTP request completes
        anyway -> the user says "tekrar dene" -> the mail is sent twice.

    Three conditions, all necessary:
      still_running  -- giving up on the await did not stop the socket. False
                        for cooperative_async tools, where cancellation really
                        does stop the coroutine.
      external write -- a local write that half-happened is on this machine and
                        recoverable; a sent mail is not.
      idempotency "none" -- a converging write (file_write, report_*, finance)
                        can be safely re-run, so its outcome being unknown does
                        not matter. Read straight off ToolSpec rather than
                        re-guessed here.

    `action` matters: gmail's ToolSpec is external_write because `send` is, but
    a timed-out `list_unread` wrote nothing and must stay ordinarily retryable.
    Per-action classification (Paket E) already knows the difference.
    """
    spec = get_spec(tool_name)
    if spec is None or not still_running:
        return False
    from jarvis.tool_registry import get_action_spec

    action_spec = get_action_spec(tool_name, action)
    effect = action_spec.side_effect_type if action_spec else spec.side_effect_type
    return effect == "external_write" and spec.idempotency == "none"


def format_tool_error(tool_name: str, exc: BaseException, action: str = "") -> str:
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
    retryable = category in _RETRYABLE_CATEGORIES
    extra = ""
    if category == "timeout":
        spec = get_spec(tool_name)
        timeout_class = spec.timeout_class if spec is not None else "soft_thread_timeout"
        still_running = timeout_class != "cooperative_async"
        worker_terminated = isinstance(exc, subprocess.TimeoutExpired)
        extra = (
            f"\nexecution_may_still_be_running={'true' if still_running else 'false'}"
            f"\nworker_terminated={'true' if worker_terminated else 'false'}"
        )
        if is_unknown_outcome(tool_name, action, still_running):
            # The block already said execution_may_still_be_running=true, and
            # that field has been there since Faz 3 -- but it sat next to
            # `retryable=true`, and the model reads the field it was trained to
            # act on. Saying "this may have worked" and "go ahead and retry" in
            # the same message is how the same mail gets sent twice.
            retryable = False
            extra += (
                "\noutcome=unknown"
                "\nDO NOT retry this call. The request may have completed on the"
                " remote service. Tell the user the result could NOT be verified"
                " -- do not report it as failed -- and offer to check before"
                " doing anything again."
            )
    return (
        "[TOOL_ERROR]\n"
        f"tool={tool_name}\n"
        f"category={category}\n"
        f"message={first_line}\n"
        f"retryable={'true' if retryable else 'false'}"
        + extra
    )


def _error_tool_message(request: Any, exc: BaseException) -> ToolMessage:
    call = getattr(request, "tool_call", None) or {}
    name = call.get("name") or "unknown"
    # Paket D: the action decides whether a timeout leaves an external side
    # effect in doubt -- gmail("send") does, gmail("list_unread") does not.
    action = str((call.get("args") or {}).get("action", "")).strip().lower()
    return ToolMessage(
        content=format_tool_error(name, exc, action),
        name=name,
        tool_call_id=call.get("id") or "",
        status="error",
    )


def _attach_artifacts(result: Any, declared: list) -> Any:
    """Ride this call's declared artifacts back on ToolMessage.artifact.

    Post-MVP Faz 1 -- see jarvis/execution/artifacts.py for why the channel
    is out of band rather than parsed out of the tool's return string. Only
    ever fills an EMPTY artifact field: a tool using LangChain's own
    response_format="content_and_artifact" owns that slot, and silently
    overwriting it would break it. A non-ToolMessage result (ToolNode can
    return a Command) is passed through untouched.
    """
    if not declared:
        return result
    if isinstance(result, ToolMessage) and result.artifact is None:
        result.artifact = [a.model_dump() for a in declared]
    return result


def _wrap_tool_call(request: Any, execute: Any) -> Any:
    with artifacts.collecting() as declared:
        try:
            return _attach_artifacts(execute(request), declared)
        except GraphInterrupt:
            raise  # confirmation-gate interrupts must keep propagating untouched
        except Exception as exc:  # noqa: BLE001 — this boundary is the point
            return _attach_artifacts(_error_tool_message(request, exc), declared)


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
    with artifacts.collecting() as declared:
        try:
            return _attach_artifacts(
                await asyncio.wait_for(execute(request), timeout=_timeout_bound_for(name)),
                declared,
            )
        except GraphInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 — this boundary is the point (includes asyncio.TimeoutError)
            # A tool that wrote its file and THEN timed out really did produce
            # it; keeping the declaration lets the envelope say so instead of
            # losing the artifact along with the error.
            return _attach_artifacts(_error_tool_message(request, exc), declared)


def make_safe_tool_node(tools: list) -> ToolNode:
    """ToolNode whose tool-body exceptions become [TOOL_ERROR] ToolMessages."""
    return ToolNode(tools, wrap_tool_call=_wrap_tool_call, awrap_tool_call=_awrap_tool_call)
