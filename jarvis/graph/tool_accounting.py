"""Per-turn tool-execution accounting (Patch 1.2, Faz 1B).

Two things live here:

``tool_call_fingerprint`` — the canonical identity of one tool call
(sha256 over the tool name + sorted-key JSON of its args). The
confirmation node stamps it into ``seen_tool_fingerprints`` at policy
time; this node promotes it into ``completed_tool_fingerprints`` only
after the tool actually returned a non-error result.

``make_tool_result_accounting_node`` — the graph node that runs right
after the tools node (``tools → tool_result_accounting → ...``). It is
the only place that can know how a call actually ended, which is why
completed-bookkeeping happens here and not in the confirmation node
(at decision time the tool hasn't run yet — an external review caught
exactly this design error in the first draft). The ledger it builds is
also what the recursion-stop handler reads to tell the user honestly
whether anything completed before the turn was cut (live incident F16:
10 duplicate procedure drafts, then an opaque 500).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from jarvis.execution import idempotency
from jarvis.execution.envelope import build_shadow_envelope
from jarvis.execution.artifacts import parse_refs as parse_artifact_refs
from jarvis.execution.postcondition_runner import run_postconditions
from jarvis.execution.redaction import redact_preview
from jarvis.graph.state import JarvisState
from jarvis.tool_registry import get_spec

# A ToolMessage whose content starts with one of these did NOT succeed —
# stubs injected by the confirmation node's block paths plus safe_tools'
# error boundary. Kept in sync with those producers by the tests. "⚠" is the
# validation/warning prefix many native tools return on their did-not-happen
# branch (schedule/todo/drive/finance: "⚠ title gerekli", "⚠ Bulunamadı", …);
# their success branches use other glyphs (🗑 ⏸ ▶ ✏ ✓), so treating "⚠" as a
# failure is correct for both the ledger and the audit callback that share
# this tuple (the two used to diverge — see agent._record_execution_end).
# "[INVALID_ARGS" (Agent Runtime rev.2, Faz 6) has no producer yet -- added
# now so the day one exists, this tuple doesn't need a synchronized second
# change.
_FAILURE_PREFIXES = ("[TOOL_ERROR]", "[ERROR]", "[BLOCKED", "[DENIED", "[DUPLICATE", "[INVALID_ARGS", "⚠")

_CONTENT_HEAD_CHARS = 120


def tool_call_fingerprint(name: str, args: dict[str, Any] | None) -> str:
    """Stable identity for a tool call: same tool + same args ⇒ same hash.

    `action`, if present, is compared case/whitespace-insensitively
    (`.strip().lower()`) before hashing: every action-dispatch tool's own
    body already normalizes it the same way before branching on it
    (confirmed while building jarvis.execution.args_schemas), so "send" and
    " SEND " are the same call as far as the tool itself is concerned --
    the fingerprint used to disagree, silently missing same-turn duplicate
    detection for a retry that only varied by how the model capitalized or
    padded the action string (Agent Runtime rev.2, Faz 6 Kısım 2 review).
    Every other arg is hashed as-is: most (email bodies, search queries,
    file paths) are genuinely case-sensitive, so normalizing them too would
    be wrong, not just unnecessary. This only ever widens what counts as a
    duplicate, never narrows it, so it cannot make an already-safe call look
    new -- fine for both of this function's callers (the turn-scoped
    seen/completed-fingerprint dedup gate, and prepare-time vs. resume-time
    TOCTOU comparison in confirmation_node, which hashes both sides through
    this same function and so stays internally consistent either way).
    """
    normalized = dict(args or {})
    action = normalized.get("action")
    if isinstance(action, str):
        normalized["action"] = action.strip().lower()
    canonical = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{name}:{canonical}".encode("utf-8")).hexdigest()


def content_is_failure(content: Any) -> bool:
    """True if a tool's textual result signals failure/no-op.

    Single source of truth shared by the ledger (tool_message_ok) and the
    audit callback (agent._record_execution_end) so both judge an outcome
    identically — the audit used to str() the whole ToolMessage and never
    matched these prefixes, logging failed calls as ok:true (the B6 grafik
    false-positive)."""
    s = content if isinstance(content, str) else str(content)
    return s.lstrip().startswith(_FAILURE_PREFIXES)


def tool_message_ok(tm: ToolMessage | None) -> bool:
    """Did this call genuinely succeed? (No message at all counts as failure.)"""
    if tm is None:
        return False
    if getattr(tm, "status", None) == "error":
        return False
    return not content_is_failure(tm.content)


_BLOCKED_CODE_RE = re.compile(r"^\s*\[BLOCKED:([a-z0-9_]+)\]")


def parse_blocked_code(content: Any) -> str | None:
    """Machine-readable reason code from a '[BLOCKED:<code>] ...' result.

    2026-07-19 review item 3: tool-level policy refusals (SSRF, shell
    deny-list, workspace escape, MCP browser guard) carry a snake_case code
    in the prefix so scoring/telemetry can key on structure instead of the
    human-facing string. Returns None for legacy bare '[BLOCKED] ...' shapes
    and for confirmation-node stubs ('[BLOCKED: free text]', with a space) —
    those pre-execution blocks already leave structured policy_decision rows.
    """
    s = content if isinstance(content, str) else str(content)
    m = _BLOCKED_CODE_RE.match(s)
    return m.group(1) if m else None


_INVALID_ARGS_RE = re.compile(r"^\s*\[INVALID_ARGS:([a-zA-Z0-9_]+)\]")


def parse_invalid_args_field(content: Any) -> str | None:
    """Agent Runtime rev.2, Faz 6: machine-readable field name from an
    '[INVALID_ARGS:<field>] ...' result -- same shape as parse_blocked_code
    above. Nothing produces this prefix yet: schema validation is defined
    in jarvis.execution.args_schemas (plot_data + the action-dispatch
    tools) but not wired into the execution path this phase -- see that
    module's own docstring for why. This parser exists so the vocabulary is
    ready the moment something does, the same "define the shape before the
    producer" precedent as Faz 1's postcondition types.
    """
    s = content if isinstance(content, str) else str(content)
    m = _INVALID_ARGS_RE.match(s)
    return m.group(1) if m else None


def parse_unknown_outcome(content: Any) -> bool:
    """Post-MVP Faz 2.75 (Paket D): did this call leave an external side effect
    in doubt?

    Written by safe_tools.format_tool_error as `outcome=unknown` when a timed-out
    external write may still have landed on the remote service. Parsed the same
    plain-substring way as the flags below, and kept separate from them because
    it is a different KIND of fact: those describe the local process, this
    describes what the remote may or may not now contain.
    """
    s = content if isinstance(content, str) else str(content)
    return "outcome=unknown" in s


def parse_timeout_flags(content: Any) -> tuple[bool, bool, bool]:
    """Agent Runtime rev.2, Faz 3: (timed_out, execution_may_still_be_running,
    worker_terminated) parsed out of a [TOOL_ERROR] block -- same plain
    substring-check style as the existing retryable/blocked-code parses
    above. See safe_tools.format_tool_error() for where these fields
    actually get written."""
    s = content if isinstance(content, str) else str(content)
    return (
        "category=timeout" in s,
        "execution_may_still_be_running=true" in s,
        "worker_terminated=true" in s,
    )


def _last_executed_round(msgs: list) -> tuple[AIMessage | None, dict[str, ToolMessage]]:
    """The most recent AIMessage-with-tool_calls and its ToolMessage results."""
    last_ai, last_ai_idx = None, -1
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            last_ai, last_ai_idx = m, i
            break
    if last_ai is None:
        return None, {}
    results = {
        m.tool_call_id: m
        for m in msgs[last_ai_idx + 1:]
        if isinstance(m, ToolMessage)
    }
    return last_ai, results


def last_round_results(state: JarvisState) -> list[tuple[bool, bool]]:
    """Per call of the last executed round: (ok, retryable).

    retryable is parsed from safe_tools' [TOOL_ERROR] block (``retryable=true``)
    — any other failure shape (stub, missing message) counts as non-retryable,
    so the post-tool router never loops on a failure it can't reason about.
    """
    last_ai, results = _last_executed_round(state.get("messages") or [])
    if last_ai is None:
        return []
    out: list[tuple[bool, bool]] = []
    for tc in last_ai.tool_calls:
        tm = results.get(tc.get("id", ""))
        ok = tool_message_ok(tm)
        content = "" if tm is None else (
            tm.content if isinstance(tm.content, str) else str(tm.content)
        )
        retryable = "retryable=true" in content
        out.append((ok, retryable))
    return out


def make_tool_result_accounting_node(settings=None, workspace: Path | None = None):
    """Node: promote succeeded calls to completed + append to the ledger.

    Agent Runtime rev.2, Faz 1: also builds one ExecutionEnvelope per call
    into state["execution_envelopes"] when settings.execution_contract_mode
    != "off" -- a pure OBSERVER, appended alongside the existing ledger,
    changing no decision (nothing reads envelopes yet). Faz 1 only
    distinguishes off vs not-off; every non-"off" value (shadow,
    enforce_read_only, ...) behaves identically to "shadow" until Faz 2
    adds real gating on this field. With settings=None or mode="off" (both
    the default), the envelope block below never runs at all -- the
    returned dict is unchanged from pre-Faz-1 behavior, which is what lets
    existing callers (e.g. test_tool_limits.py) keep constructing this node
    with zero args.

    Agent Runtime rev.2, Faz 2: also commits each SUCCEEDED call's
    ExecutionRequest (built earlier by prepare_execution_node) into the
    idempotency journal (jarvis.execution.idempotency) -- this is the only
    node that knows a call actually succeeded, same reasoning given above
    for completed_tool_fingerprints living here rather than in
    confirmation_node. A pure disk side effect, not a new state key --
    unconditional and independent of execution_contract_mode (approval
    binding/idempotency sit outside that ladder, see prepare_execution_node's
    docstring), and a no-op when state["execution_requests"] has no entry
    for a call (old checkpoint / direct-node unit test that never ran
    prepare_execution).

    Agent Runtime rev.2, Faz 3: inside the SAME mode!="off" envelope block,
    two more things now happen per call: (1) parse_timeout_flags() reads the
    honest timeout fields safe_tools.format_tool_error() wrote into the
    [TOOL_ERROR] block and feeds them to build_shadow_envelope(), so a timed-
    out call's envelope status is "timed_out", not a plain "failed"; (2) if
    this tool's ToolSpec declares any postconditions, run_postconditions()
    evaluates them against `workspace` and attaches the results. workspace
    is optional (defaults None, same "settings=None is tolerated" contract
    as before) -- when absent, postcondition checks that need a path to
    resolve report "unverified" rather than crashing (see
    postcondition_runner.py's own path-resolution fallback).

    Approve-side result binding: the always-on ledger also correlates each
    executed call with its latest ExecutionRequest and records only safe
    structured authorization metadata. ``user_approved`` requires all three
    facts: the request required confirmation, the interactive gate was
    enabled, and confirmation_node recorded that exact execution id after
    signed-request verification. Risk level by itself is never sufficient.
    """
    mode = getattr(settings, "execution_contract_mode", "off") if settings is not None else "off"

    async def tool_result_accounting(state: JarvisState) -> dict:
        last_ai, results = _last_executed_round(state.get("messages") or [])
        if last_ai is None:
            return {}

        completed = list(state.get("completed_tool_fingerprints") or [])
        ledger = list(state.get("tool_execution_ledger") or [])
        envelopes = list(state.get("execution_envelopes") or []) if mode != "off" else None
        requests_by_id = {
            r["tool_call_id"]: r for r in (state.get("execution_requests") or [])
        }
        user_approved_ids = set(state.get("user_approved_execution_ids") or [])
        for tc in last_ai.tool_calls:
            name = tc.get("name", "")
            args = tc.get("args") or {}
            fp = tool_call_fingerprint(name, args)
            tm = results.get(tc.get("id", ""))
            ok = tool_message_ok(tm)
            if ok and fp not in completed:
                completed.append(fp)
            request_entry = requests_by_id.get(tc.get("id", ""))
            if ok and request_entry is not None:
                idempotency.commit(
                    request_entry["request"]["execution_id"], name, fp,
                )
            content = "" if tm is None else (
                tm.content if isinstance(tm.content, str) else str(tm.content)
            )
            code = parse_blocked_code(content)
            request = (
                request_entry.get("request")
                if isinstance(request_entry, dict) else None
            )
            request = request if isinstance(request, dict) else None
            genuinely_user_approved = bool(
                request
                and request.get("execution_id") in user_approved_ids
                and request.get("requires_confirmation") is True
                and getattr(settings, "confirmation_gate_enabled", False)
            )
            ledger.append({
                "tool": name,
                "fingerprint": fp,
                "ok": ok,
                "tool_call_id": tc.get("id", ""),
                "risk_level": request.get("risk_level") if request else None,
                "side_effect_type": (
                    str(request.get("side_effect_type") or "unknown")
                    if request else "unknown"
                ),
                "confirmation_required": (
                    bool(request.get("requires_confirmation")) if request else None
                ),
                "authorization": (
                    "user_approved" if genuinely_user_approved
                    else "auto_approved" if request else "unknown"
                ),
                # Faz 1: redacted, not raw -- this ledger rides in graph
                # state through the SqliteSaver checkpointer.
                "content_head": redact_preview(content, max_chars=_CONTENT_HEAD_CHARS),
                **({"reason_code": code} if code else {}),
                # Paket D: "not ok" and "may have happened anyway" are
                # different facts, and only the second one forbids a retry.
                **({"outcome": "unknown"} if parse_unknown_outcome(content) else {}),
            })
            if envelopes is not None:
                timed_out, may_still_run, worker_terminated = parse_timeout_flags(content)
                spec = get_spec(name)
                # Post-MVP Faz 1: whatever the tool declared it produced,
                # carried out of band on ToolMessage.artifact by safe_tools'
                # wrap hooks (jarvis/execution/artifacts.py). Read here rather
                # than re-derived from `content` -- that is the entire point.
                declared = parse_artifact_refs(getattr(tm, "artifact", None))
                postcondition_results = (
                    run_postconditions(
                        spec.postconditions, workspace=workspace, args=args,
                        tool_result_content=content, artifacts=declared,
                    )
                    if spec is not None and spec.postconditions
                    else []
                )
                envelopes.append(build_shadow_envelope(
                    tool_name=name, args=args, ok=ok, content=content,
                    retryable="retryable=true" in content, error_code=code,
                    execution_id=tc.get("id", ""),
                    timed_out=timed_out,
                    execution_may_still_be_running=may_still_run,
                    worker_terminated=worker_terminated,
                    postconditions=postcondition_results,
                    artifacts=[a.path for a in declared],
                ).model_dump())

        out = {
            "completed_tool_fingerprints": completed,
            "tool_execution_ledger": ledger,
        }
        if envelopes is not None:
            out["execution_envelopes"] = envelopes
        return out

    tool_result_accounting.__name__ = "tool_result_accounting"
    return tool_result_accounting
