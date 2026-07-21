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
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from jarvis.execution.envelope import build_shadow_envelope
from jarvis.execution.redaction import redact_preview
from jarvis.graph.state import JarvisState

# A ToolMessage whose content starts with one of these did NOT succeed —
# stubs injected by the confirmation node's block paths plus safe_tools'
# error boundary. Kept in sync with those producers by the tests. "⚠" is the
# validation/warning prefix many native tools return on their did-not-happen
# branch (schedule/todo/drive/finance: "⚠ title gerekli", "⚠ Bulunamadı", …);
# their success branches use other glyphs (🗑 ⏸ ▶ ✏ ✓), so treating "⚠" as a
# failure is correct for both the ledger and the audit callback that share
# this tuple (the two used to diverge — see agent._record_execution_end).
_FAILURE_PREFIXES = ("[TOOL_ERROR]", "[ERROR]", "[BLOCKED", "[DENIED", "[DUPLICATE", "⚠")

_CONTENT_HEAD_CHARS = 120


def tool_call_fingerprint(name: str, args: dict[str, Any] | None) -> str:
    """Stable identity for a tool call: same tool + same args ⇒ same hash."""
    canonical = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
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


def make_tool_result_accounting_node(settings=None):
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
    """
    mode = getattr(settings, "execution_contract_mode", "off") if settings is not None else "off"

    async def tool_result_accounting(state: JarvisState) -> dict:
        last_ai, results = _last_executed_round(state.get("messages") or [])
        if last_ai is None:
            return {}

        completed = list(state.get("completed_tool_fingerprints") or [])
        ledger = list(state.get("tool_execution_ledger") or [])
        envelopes = list(state.get("execution_envelopes") or []) if mode != "off" else None
        for tc in last_ai.tool_calls:
            name = tc.get("name", "")
            args = tc.get("args") or {}
            fp = tool_call_fingerprint(name, args)
            tm = results.get(tc.get("id", ""))
            ok = tool_message_ok(tm)
            if ok and fp not in completed:
                completed.append(fp)
            content = "" if tm is None else (
                tm.content if isinstance(tm.content, str) else str(tm.content)
            )
            code = parse_blocked_code(content)
            ledger.append({
                "tool": name,
                "fingerprint": fp,
                "ok": ok,
                # Faz 1: redacted, not raw -- this ledger rides in graph
                # state through the SqliteSaver checkpointer.
                "content_head": redact_preview(content, max_chars=_CONTENT_HEAD_CHARS),
                **({"reason_code": code} if code else {}),
            })
            if envelopes is not None:
                envelopes.append(build_shadow_envelope(
                    tool_name=name, args=args, ok=ok, content=content,
                    retryable="retryable=true" in content, error_code=code,
                    execution_id=tc.get("id", ""),
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
