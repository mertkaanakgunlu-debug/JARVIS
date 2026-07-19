"""2026-07-19 review item 3 — machine-readable block codes.

Tool-level policy refusals now embed a snake_case reason code in the
"[BLOCKED:<code>]" prefix; tool_accounting parses it into the execution
ledger/trace rows, and the eval oracle keys on the structured field first,
keeping the string-prefix sniff only as a fallback for legacy rows. The
user-facing text around the prefix stays presentation.
"""
from __future__ import annotations

from jarvis.graph.tool_accounting import content_is_failure, parse_blocked_code
from scripts import eval_oracle as O


def test_parse_extracts_snake_code():
    out = parse_blocked_code("[BLOCKED:ssrf_private_address] Refusing to fetch")
    assert out == "ssrf_private_address"


def test_parse_tolerates_leading_whitespace():
    assert parse_blocked_code("  [BLOCKED:shell_denylist] nope") == "shell_denylist"


def test_parse_returns_none_for_legacy_bare_prefix():
    assert parse_blocked_code("[BLOCKED] Refusing to fetch") is None


def test_parse_returns_none_for_confirmation_stub_free_text():
    # confirmation_node stubs use "[BLOCKED: free text]" (with a space) and
    # already leave structured policy_decision rows — not this convention.
    assert parse_blocked_code("[BLOCKED: kill switch is off (owner said stop)]") is None


def test_parse_returns_none_for_non_blocked_content():
    assert parse_blocked_code("[ERROR] boom") is None
    assert parse_blocked_code("normal successful output") is None
    assert parse_blocked_code(None) is None


def test_coded_prefix_still_counts_as_failure():
    # _FAILURE_PREFIXES matches on "[BLOCKED" without the closing bracket —
    # the ledger/audit "ok" judgement must not regress with the code inserted.
    assert content_is_failure("[BLOCKED:ssrf_private_address] Refusing") is True
    assert content_is_failure("[BLOCKED] legacy shape") is True


def test_oracle_prefers_structured_reason_code_row():
    """A row whose content_head lost the prefix (truncation, future rewording)
    still proves the block via reason_code — the string is no longer
    load-bearing for scoring."""
    exp = O.Expected("C9", expected_tool="url_read", outcome=O.BLOCKED)
    row = {"tool": "url_read", "ok": False, "reason_code": "ssrf_private_address",
           "content_head": "Refusing to fetch this URL"}
    assert O.score(exp, O.Observed("C9", response="engellendi", trace=[row])).passed


def test_oracle_prefix_fallback_still_works_for_legacy_rows():
    exp = O.Expected("C9", expected_tool="url_read", outcome=O.BLOCKED)
    row = {"tool": "url_read", "ok": False, "content_head": "[BLOCKED] Refusing"}
    assert O.score(exp, O.Observed("C9", response="engellendi", trace=[row])).passed
