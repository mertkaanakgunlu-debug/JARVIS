"""Shared redaction layer -- Agent Runtime rev.2, Faz 1 (reviewer item #12).

The ONE place that decides what a tool call's arguments/results are safe to
persist to disk. Before this module, three call sites each made their own
(inconsistent) decision:
  - jarvis/agent.py's redact_tool_args() -- key-based, dict-only; its own
    docstring admitted a non-dict input is truncated but never scanned, so
    a secret embedded in a plain-string argument survived untouched
  - jarvis/audit_log.py's args_preview/result_preview (agent.py's
    on_tool_start/_record_execution_end) -- no redaction applied at all
  - jarvis/graph/tool_accounting.py's tool_execution_ledger.content_head --
    no redaction applied at all, and this one persists through the
    LangGraph SqliteSaver checkpointer, not just an append-only log

All three, plus jarvis.execution.envelope's ExecutionEnvelope, now go
through redact_preview()/digest_args() below -- one vocabulary, so the three
call sites can no longer drift apart from each other.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_REDACT_KEYS = (
    "password", "passwd", "token", "api_key", "apikey", "secret",
    "credential", "authorization", "body", "content", "message",
)

# Fixed, deterministic pattern set -- extends the old key-only check to
# plain-string arguments (agent.py's documented limit) without resorting to
# fuzzy entropy heuristics, which the original author deliberately avoided
# for test determinism (see agent.py's pre-Faz-1 comment above the old
# _TRACE_REDACT_KEYS). Each pattern targets a recognizable secret SHAPE, not
# "any long string" -- a blanket long-string filter would also eat file
# paths, hashes and normal prose.
_REDACT_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),          # OpenAI-style secret key
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}\b"),      # Google API key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),   # GitHub token family
)

_MASK = "<redacted>"


def _is_sensitive_key(key: Any) -> bool:
    return any(s in str(key).lower() for s in _REDACT_KEYS)


def _scan_string(s: str) -> str:
    for pattern in _REDACT_PATTERNS:
        s = pattern.sub(_MASK, s)
    return s


def redact_value(value: Any) -> Any:
    """Recursively redact known-sensitive dict keys and pattern-matched
    substrings. Unlike the old key-only helper, a plain string is scanned
    too -- a secret embedded in free text no longer survives just because
    it isn't sitting behind a suspicious dict key."""
    if isinstance(value, dict):
        return {
            k: (_MASK if _is_sensitive_key(k) else redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return _scan_string(value)
    return value


def redact_preview(value: Any, *, max_chars: int = 200) -> str:
    """Redact, stringify, then hard-truncate to max_chars -- the shared
    preview shape used by audit_log/tool_trace/the execution ledger/
    ExecutionEnvelope. Deliberately a plain slice with no truncation
    suffix, matching the exact truncation behavior of the raw call sites
    this supersedes (agent.py's str(x)[:200] x2, tool_accounting.py's
    content[:120]) -- this function changes what gets redacted, not how
    truncation is formatted."""
    redacted = redact_value(value)
    s = redacted if isinstance(redacted, str) else str(redacted)
    return s[:max_chars]


def digest_args(tool_name: str, args: dict[str, Any] | None) -> str:
    """A stable fingerprint for a call's identity, safe to persist where
    tool_accounting.tool_call_fingerprint (used for dedup/idempotency
    matching) is not -- deliberately a DIFFERENT digest, domain-separated
    by the "envelope:" prefix below, so the two are never accidentally
    interchangeable. Honesty note: the prefix is a fixed public string, not
    a secret -- this is domain separation and "don't write raw args to
    disk," not a cryptographic security boundary (this is a local
    single-user assistant; if that threat model ever changes, this
    function needs to change with it)."""
    canonical = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"envelope:{tool_name}:{canonical}".encode("utf-8")).hexdigest()
