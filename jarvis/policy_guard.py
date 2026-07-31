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
from jarvis.tool_registry import ToolSpec, get_alpha_status, get_spec

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
    # Patch 1.1: per-CALL side-effect class. Mirrors ToolSpec.side_effect_type
    # except on the mixed read/write external tools (_READ_ACTIONS), where a
    # read action resolves to "external_read" — so a gate keyed on "did this
    # call write externally" (EXTERNAL_WRITES_ENABLED=false) stops denying
    # `gmail read` / `calendar list` along with `send`/`create`. "unknown"
    # when no ToolSpec is registered for the tool.
    side_effect_type: str = "unknown"
    # Agent Runtime rev.2, Faz 0: which hard-veto path produced allowed=False,
    # so callers (confirmation_node) can show the right message instead of
    # always saying "the kill switch is off" — that sentence is actively
    # wrong for a capability that's disabled by alpha policy, kill switch
    # state notwithstanding. Only meaningful when allowed=False; default
    # matches the only veto path that existed before this field did, so
    # every pre-existing call site (kill-switch veto) needs no changes.
    veto_kind: str = "kill_switch"  # "kill_switch" | "capability_disabled"


# ── Post-MVP Faz 2: confidence-based calendar confirmation ───────────────────
#
# The owner's decision was "takvim/todo serbest -- tarih hatasi kapandiktan
# sonra": stop interrupting for routine calendar entries once dates are
# trustworthy. The plan deliberately does NOT cash that in as a blanket
# relaxation, because "JARVIS may create calendar events without asking" and
# "JARVIS may create calendar events it is not sure about without asking" are
# very different promises. So:
#
#     create + high confidence + reversible   -> run
#     create + ambiguous date / time / title  -> ask
#     update / batch_create / delete          -> ask (always)
#
# Only `create` is eligible, and only a single one. batch_create writes N
# events from a JSON blob (N chances to be wrong, one approval), update
# silently rewrites something that already exists, and delete is the one
# genuinely hard-to-reverse action in the tool. A single create is the only
# action a user can undo by looking at their calendar and pressing delete.
_AUTONOMOUS_CALENDAR_ACTIONS = frozenset({"create"})


def calendar_confidence(args: dict[str, Any], utterance: str = "") -> tuple[float, str]:
    """How sure are we that this calendar call means what it says?

    A pure function of (args, utterance) — no clock, no I/O, no model.
    Confidence is the MINIMUM across the date, the time, the title, and what
    the user actually said: an event at a confidently wrong hour is just as
    wrong as one on a confidently wrong day, and taking an average would let
    good fields hide a bad one.

    **`utterance` is not decoration.** Live measurement (real qwen3:8b,
    2026-07-31) found that scoring the arguments alone is not enough. Asked
    *"Pazartesi saat 4'te spor salonu diye takvime bir şey ekle"*, the model
    ignored the tool description's "pass the wording through", resolved the
    weekday itself — to a Saturday — and passed an ISO date. Scored on args
    alone that is a 1.00, and a wrong-day event would have been created with
    no prompt. Reading the user's own words closes it: an ambiguous request
    stays ambiguous however precise the model's arguments look, and a resolved
    date that contradicts a weekday the user named is a provable conflict.

    Deliberately NOT read from the arguments themselves. If a "confidence"
    field were part of the tool schema, the model could set it to 1.0 and
    approve its own actions -- the gate has to derive this itself or it is not
    a gate.
    """
    from jarvis.nlu import event_text, temporal

    args = args or {}
    action = str(args.get("action", "")).strip().lower()
    if action not in _AUTONOMOUS_CALENDAR_ACTIONS:
        return 0.0, f"'{action}' is not an auto-approvable calendar action"

    date_expr = str(args.get("date", ""))

    # A provable contradiction between the user's words and the model's date
    # is not a low score, it is a stop. Same standard the Faz 1 unbacked-claim
    # gate holds itself to: block only on something demonstrably false.
    conflict = temporal.weekday_conflict(utterance, temporal.absolute_date(date_expr))
    if conflict:
        return 0.0, conflict

    date_conf, date_why = temporal.date_expression_confidence(date_expr)
    time_conf, time_why = temporal.time_expression_confidence(str(args.get("time", "")))
    title_conf, title_why = event_text.title_quality(str(args.get("title", "")))
    said_conf, said_why = temporal.utterance_ambiguity(utterance)

    worst, why = min(
        ((date_conf, date_why), (time_conf, time_why),
         (title_conf, title_why), (said_conf, said_why)),
        key=lambda pair: pair[0],
    )
    return worst, why


def _resolve_risk(
    tool_name: str,
    args: dict[str, Any],
    spec: ToolSpec,
    settings: "Settings | None" = None,
    *,
    interactive: bool = False,
    utterance: str = "",
) -> tuple[int, bool, str, str]:
    """Return (effective_risk_level, effective_requires_confirmation,
    effective_side_effect_type, note) for this specific call, applying the
    per-action read-action downgrade and the calendar confidence downgrade.

    risk_level is NEVER lowered by the confidence path -- a confidently-correct
    calendar create is still an external write, still audited as L3, still
    vetoable by the kill switch, and still blocked by --profile test. The only
    thing confidence buys is not interrupting the user to ask.
    """
    action = str(args.get("action", "")).strip().lower()
    read_actions = _READ_ACTIONS.get(tool_name)
    if read_actions and action in read_actions:
        # _READ_ACTIONS only lists tools whose ToolSpec is external_write —
        # their documented pure-read actions are, per call, external READS.
        return 1, False, "external_read", ""

    if (
        tool_name == "google_calendar"
        and interactive
        and getattr(settings, "calendar_autonomy_enabled", False)
        and spec.requires_confirmation
    ):
        confidence, why = calendar_confidence(args, utterance)
        if confidence >= _auto_threshold():
            return (
                spec.risk_level, False, spec.side_effect_type,
                f"calendar create auto-approved (confidence {confidence:.2f})",
            )
        if action in _AUTONOMOUS_CALENDAR_ACTIONS:
            return (
                spec.risk_level, True, spec.side_effect_type,
                f"confirmation required: {why}" if why else
                f"confirmation required (confidence {confidence:.2f})",
            )

    return spec.risk_level, spec.requires_confirmation, spec.side_effect_type, ""


def _auto_threshold() -> float:
    """Indirection so the band lives in exactly one module (jarvis.nlu.temporal)
    while policy_guard keeps its no-import-at-module-scope shape."""
    from jarvis.nlu.temporal import AUTO_THRESHOLD

    return AUTO_THRESHOLD


def evaluate(
    tool_name: str,
    args: dict[str, Any],
    settings: "Settings",
    *,
    interactive: bool = False,
    utterance: str = "",
) -> PolicyDecision:
    """Decide whether a tool call may proceed, and whether it needs the user's
    explicit OK first. Does not itself check settings.confirmation_gate_enabled
    — callers that respect that flag (the graph's confirmation_node) check it
    separately; policy_guard's job is risk classification + the kill switch,
    which apply regardless of whether the interactive gate is on.

    Still a pure function of its arguments, so two independent evaluations of
    the same call can never disagree — see calendar_confidence() for why that
    property had to be designed for rather than assumed.

    `interactive` (Post-MVP Faz 2) means "a human is present, in this turn, to
    see what happens". It gates the confidence-based calendar downgrade and
    NOTHING else, and it defaults to False so every caller that has not
    thought about it — the workflow engine, any future direct dispatcher —
    keeps exactly the pre-Faz-2 behaviour. Callers that pass True must be able
    to state who is watching.

    `utterance` is the user's own text for this turn (`state["user_query"]`).
    It can only ever LOWER confidence, so omitting it is safe — it just gives
    up the protection against a model that resolved an ambiguous request into
    confident-looking arguments before the gate ever saw it.
    """
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

    risk_level, requires_confirmation, side_effect_type, note = _resolve_risk(
        tool_name, args or {}, spec, settings, interactive=interactive, utterance=utterance,
    )

    # Agent Runtime rev.2, Faz 0: a "disabled" alpha-status capability is
    # vetoed unconditionally -- independent of, and checked before, the kill
    # switch below. Kill switch is a stop the OWNER can lift by re-arming it;
    # "disabled" is a build-time decision (see tool_registry._ALPHA_STATUS)
    # that re-arming the kill switch must not bypass. risk_level/
    # requires_confirmation still reflect the tool's real classification
    # (unchanged) -- only `allowed` and `veto_kind` differ from a normal
    # confirm-required call, same pattern the kill-switch veto below uses.
    if get_alpha_status(tool_name) == "disabled":
        return PolicyDecision(
            tool_name, action, risk_level, requires_confirmation, allowed=False,
            reason="this capability is disabled for the manual-alpha build",
            side_effect_type=side_effect_type, veto_kind="capability_disabled",
        )

    # Keyed on risk_level ALONE, deliberately.
    #
    # This condition used to read `requires_confirmation and risk_level >= 3`.
    # That was a safe no-op for as long as requires_confirmation was implied by
    # risk_level >= 3 (tests/test_registry_sweep.py's "L3 => confirmation"
    # invariant guarantees it for every ToolSpec, and _READ_ACTIONS only ever
    # downgrades to L1). Faz 2's confidence path is the first thing that can
    # produce risk_level=3 WITH requires_confirmation=False -- under the old
    # condition, an auto-approved calendar create would have walked straight
    # past a tripped kill switch. The kill switch is an emergency stop on
    # JARVIS touching the outside world; how confident JARVIS feels is not
    # supposed to be an input to it.
    if risk_level >= _KILL_SWITCH_RISK_THRESHOLD and not kill_switch.is_enabled():
        why = kill_switch.reason() or "no reason given"
        return PolicyDecision(
            tool_name, action, risk_level, requires_confirmation, allowed=False,
            reason=f"kill switch is off ({why})",
            side_effect_type=side_effect_type,
        )

    return PolicyDecision(tool_name, action, risk_level, requires_confirmation, allowed=True,
                          reason=note, side_effect_type=side_effect_type)


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

    # Post-MVP Faz 2: show the RESOLVED instant, not the expression.
    # "create a calendar event (title=Baran ile toplantı, date=yarın)" asks the
    # user to approve a word, and a user cannot tell from the word whether it
    # resolved to the right day -- which is exactly how a one-day-early event
    # gets approved. Best-effort: an unresolvable date falls back to showing
    # the raw fields, which is what this line did before.
    if tool_name == "google_calendar" and args.get("date"):
        try:
            from jarvis.nlu import temporal

            resolution = temporal.resolve(str(args.get("date", "")), str(args.get("time", "")))
            if resolution.ok:
                details.append(f"when={temporal.describe(resolution)}")
            else:
                details.append(f"date={args.get('date')}")
        except Exception:  # noqa: BLE001 — a prompt must never fail to render
            details.append(f"date={args.get('date')}")

    detail = " (" + ", ".join(details) + ")" if details else ""
    return f"{verb}{detail}"
