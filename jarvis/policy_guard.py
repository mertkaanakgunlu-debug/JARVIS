"""Transport-agnostic safety kernel (Faz 4).

Single choke point for "is this tool call allowed, and does it need the
user's explicit OK first" — used by the LangGraph confirmation node today
(jarvis/graph/nodes.py's confirmation_node), and meant to be the same thing
any future direct tool dispatcher (MCP, Faz 5) calls into, so there is
exactly one place that answers this question rather than one per transport.
Depends on nothing graph-shaped (no LangGraph/LangChain imports) so it stays
reusable outside the graph.

Per-action, not per-tool (BUG-6): the four gated external_api tools
(google_calendar, gmail, google_drive, itu_mail) mix read actions
(list/search/read/...) with write actions (create/send/delete/...) under one
ToolSpec — gating the whole tool interrupts "list my emails" exactly like
"delete this email". _READ_ACTIONS downgrades the documented read actions
back to L1/no-confirm for those four tools; every other tool's actions all
share its ToolSpec risk_level uniformly (they don't have this read/write
split to begin with).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from jarvis import kill_switch
from jarvis.tool_registry import ToolSpec, get_spec

if TYPE_CHECKING:
    from jarvis.config import Settings

# Kill switch only vetoes genuine external-effect actions (L3) — a tripped
# switch still allows local reversible writes (file_write, todo, ...); it is
# an emergency stop for "JARVIS acting on the outside world", not a full halt.
_KILL_SWITCH_RISK_THRESHOLD = 3

# (tool_name -> action names that are pure reads) — only the four tools whose
# ToolSpec risk_level covers a mix of read and write actions need an entry.
_READ_ACTIONS: dict[str, frozenset[str]] = {
    "google_calendar": frozenset({"list", "search"}),
    "gmail": frozenset({"list_unread", "search", "read"}),
    "google_drive": frozenset({"search", "list", "read", "download"}),
    "itu_mail": frozenset({"list_unread", "search", "read"}),
}


@dataclass(frozen=True)
class PolicyDecision:
    tool: str
    action: str
    risk_level: int
    requires_confirmation: bool
    allowed: bool          # False => hard veto (kill switch) — never even offer confirmation
    reason: str = ""


def _resolve_risk(tool_name: str, args: dict[str, Any], spec: ToolSpec) -> tuple[int, bool]:
    """Return (effective_risk_level, effective_requires_confirmation) for this
    specific call, applying the per-action read-action downgrade."""
    action = str(args.get("action", "")).strip().lower()
    read_actions = _READ_ACTIONS.get(tool_name)
    if read_actions and action in read_actions:
        return 1, False
    return spec.risk_level, spec.requires_confirmation


def evaluate(tool_name: str, args: dict[str, Any], settings: "Settings") -> PolicyDecision:
    """Decide whether a tool call may proceed, and whether it needs the user's
    explicit OK first. Does not itself check settings.confirmation_gate_enabled
    — callers that respect that flag (the graph's confirmation_node) check it
    separately; policy_guard's job is risk classification + the kill switch,
    which apply regardless of whether the interactive gate is on."""
    spec = get_spec(tool_name)
    action = str((args or {}).get("action", "")).strip().lower()

    if spec is None:
        # Every registered @tool has a spec; an unregistered name reaching
        # here would be a bug elsewhere. Fail safe rather than silently
        # trusting an unclassified action.
        return PolicyDecision(
            tool_name, action, risk_level=3, requires_confirmation=True, allowed=True,
            reason="no ToolSpec registered for this tool -- defaulting to confirm",
        )

    risk_level, requires_confirmation = _resolve_risk(tool_name, args or {}, spec)

    if requires_confirmation and risk_level >= _KILL_SWITCH_RISK_THRESHOLD and not kill_switch.is_enabled():
        why = kill_switch.reason() or "no reason given"
        return PolicyDecision(
            tool_name, action, risk_level, requires_confirmation, allowed=False,
            reason=f"kill switch is off ({why})",
        )

    return PolicyDecision(tool_name, action, risk_level, requires_confirmation, allowed=True)


# ── Human-readable descriptions (CLI text, TTS, graph interrupt payload) ──────

_ACTION_VERBS: dict[str, str] = {
    "send": "send an email",
    "reply": "reply to an email",
    "trash": "delete an email",
    "mark_read": "mark an email as read",
    "create": "create a calendar event",
    "batch_create": "create calendar events",
    "delete": "delete an item",
    "update": "update a calendar event",
    "upload": "upload a file to Drive",
    "share": "share a Drive file",
}

_DETAIL_KEYS = (
    "title", "to", "subject", "query", "name", "file_id", "event_id", "command", "script_path",
    # Faz 5: MCP tools (Playwright) don't use the action="..." dispatch
    # convention the native external_api tools do, so the verb fallback
    # above is just the bare tool name -- these keys are what make
    # e.g. "browser_click" read as "browser_click (element=Submit button)"
    # in a confirmation prompt instead of naming nothing about the call.
    "element", "url", "text",
)


def describe_call(tool_name: str, args: dict[str, Any]) -> str:
    """One-line, plain-language description of a tool call — no markdown, safe
    to read aloud (TTS) or print (CLI / interrupt payload / audit log)."""
    args = args or {}
    if tool_name == "shell_run":
        return f"run the shell command: {args.get('command', '')}"
    if tool_name == "python_run":
        return f"execute the Python script: {args.get('script_path', '')}"

    action = str(args.get("action", "")).strip().lower()
    verb = _ACTION_VERBS.get(action, f"{action} via {tool_name}" if action else tool_name)

    details = [f"{k}={args[k]}" for k in _DETAIL_KEYS if args.get(k)]
    detail = " (" + ", ".join(details) + ")" if details else ""
    return f"{verb}{detail}"
