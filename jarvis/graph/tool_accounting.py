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
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

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


def make_tool_result_accounting_node():
    """Node: promote succeeded calls to completed + append to the ledger."""

    async def tool_result_accounting(state: JarvisState) -> dict:
        last_ai, results = _last_executed_round(state.get("messages") or [])
        if last_ai is None:
            return {}

        completed = list(state.get("completed_tool_fingerprints") or [])
        ledger = list(state.get("tool_execution_ledger") or [])
        for tc in last_ai.tool_calls:
            name = tc.get("name", "")
            fp = tool_call_fingerprint(name, tc.get("args") or {})
            tm = results.get(tc.get("id", ""))
            ok = tool_message_ok(tm)
            if ok and fp not in completed:
                completed.append(fp)
            content = "" if tm is None else (
                tm.content if isinstance(tm.content, str) else str(tm.content)
            )
            ledger.append({
                "tool": name,
                "fingerprint": fp,
                "ok": ok,
                "content_head": content[:_CONTENT_HEAD_CHARS],
            })

        return {
            "completed_tool_fingerprints": completed,
            "tool_execution_ledger": ledger,
        }

    tool_result_accounting.__name__ = "tool_result_accounting"
    return tool_result_accounting
