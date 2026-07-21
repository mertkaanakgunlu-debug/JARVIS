"""Agent Runtime rev.2, Faz 1 (reviewer item #12) -- the shared redaction
layer. Supersedes jarvis.agent's old key-only, dict-only redact_tool_args;
tests/test_tool_trace.py separately pins that the public wrapper still
behaves the same for its existing callers.
"""
from __future__ import annotations

from jarvis.execution.redaction import digest_args, redact_preview, redact_value


# ── key-based (dict) redaction -- pre-existing behavior, preserved ─────────

def test_dict_sensitive_key_is_masked():
    out = redact_value({"path": "x.txt", "content": "gizli metin", "api_key": "AKIA123"})
    assert out["content"] == "<redacted>"
    assert out["api_key"] == "<redacted>"
    assert out["path"] == "x.txt"


def test_nested_dict_is_redacted_recursively():
    out = redact_value({"outer": {"password": "hunter2", "note": "fine"}})
    assert out["outer"]["password"] == "<redacted>"
    assert out["outer"]["note"] == "fine"


def test_list_of_dicts_is_redacted_elementwise():
    out = redact_value([{"token": "abc"}, {"name": "ok"}])
    assert out[0]["token"] == "<redacted>"
    assert out[1]["name"] == "ok"


# ── pattern-based (plain-string) redaction -- the gap this module closes ───

def test_plain_string_with_no_secret_shape_passes_through():
    assert redact_value("plain-string-arg") == "plain-string-arg"


def test_plain_string_bearer_token_is_masked():
    out = redact_value("Authorization: Bearer sk-abcdef1234567890ABCDEF")
    assert "sk-abcdef1234567890ABCDEF" not in out


def test_plain_string_key_equals_value_shape_is_masked():
    out = redact_value("api_key=AKIAABCDEFGH1234567890")
    assert "AKIAABCDEFGH1234567890" not in out


def test_plain_string_google_api_key_is_masked():
    out = redact_value("here is my key: AIzaSyD-abcdefghijklmnopqrstuvwxyz1234")
    assert "AIzaSyD-abcdefghijklmnopqrstuvwxyz1234" not in out


def test_plain_string_github_token_is_masked():
    out = redact_value("token ghp_1234567890abcdefghijklmnopqrstuvwx")
    assert "ghp_1234567890abcdefghijklmnopqrstuvwx" not in out


def test_dict_value_string_is_also_pattern_scanned():
    """The old helper trusted non-sensitive KEYS completely -- a secret
    hiding in a plain-text value under an innocuous key (e.g. "notes")
    survived. redact_value now scans string VALUES too, regardless of key."""
    out = redact_value({"notes": "use Bearer sk-abcdef1234567890ABCDEF to auth"})
    assert "sk-abcdef1234567890ABCDEF" not in out["notes"]


# ── transparency: what redaction must NOT touch ────────────────────────────
# Faz 1 made redaction ALWAYS-ON for the execution ledger's content_head and
# the audit log's previews -- unlike the shadow ledger, that half does not sit
# behind execution_contract_mode, so it is live even at the default "off".
# Two consumers depend on that text staying intact, and neither had a test
# pinning it (found 2026-07-21 while triaging a suspected shadow-mode
# regression -- the reasoning was grep-only until these landed):
#   - scripts/eval_oracle.py's _blocked() reads content_head and sniffs a
#     "[BLOCKED"/"[DENIED" prefix as its fallback block signal; a mangled
#     prefix would silently downgrade a SAFETY scenario to "no block seen".
#   - a tool's own [ERROR]/path payload is what makes the ledger and audit
#     trail diagnosable after the fact.
# The masking tests above prove secrets DO get caught; these prove the layer
# is otherwise a pass-through, so enabling it can't move a verdict.

def test_blocked_prefix_survives_redaction():
    head = "[BLOCKED:url_denylist] Refusing to fetch this URL"
    assert redact_preview(head).lstrip().startswith("[BLOCKED")


def test_denied_prefix_survives_redaction():
    assert redact_preview("[DENIED] user rejected the action").lstrip().startswith("[DENIED")


def test_tool_error_text_survives_redaction():
    """B6's real failure text -- it must stay readable in the ledger, since
    that string is how a human reconstructs why a call failed."""
    err = "[ERROR] Column '1, 2, 3, 4' not found. Available: ['x', 'y']"
    assert redact_preview(err) == err


def test_windows_path_result_survives_redaction():
    """plot_data's success payload is a bare absolute path -- no secret
    shape, so it must round-trip byte-for-byte."""
    path = r"C:\Temp\jarvis-ab\home-champ\data\plots\line_plot.png"
    assert redact_preview(path) == path


# ── redact_preview: redact + hard-truncate, no raw fallback ────────────────

def test_redact_preview_truncates_like_the_raw_slices_it_replaces():
    long_s = "x" * 300
    assert redact_preview(long_s) == "x" * 200  # default max_chars=200, plain slice


def test_redact_preview_respects_custom_max_chars():
    long_s = "y" * 300
    assert redact_preview(long_s, max_chars=120) == "y" * 120


def test_redact_preview_stringifies_non_str():
    assert redact_preview(12345) == "12345"


# ── digest_args ──────────────────────────────────────────────────────────

def test_digest_args_is_stable_for_same_input():
    a = digest_args("file_write", {"path": "a.txt"})
    b = digest_args("file_write", {"path": "a.txt"})
    assert a == b


def test_digest_args_differs_by_tool_or_args():
    base = digest_args("file_write", {"path": "a.txt"})
    assert digest_args("file_write", {"path": "b.txt"}) != base
    assert digest_args("file_read", {"path": "a.txt"}) != base


def test_digest_args_never_contains_raw_argument_value():
    digest = digest_args("file_write", {"path": "C:/very/specific/secret-project-name.txt"})
    assert "secret-project-name" not in digest


def test_digest_args_is_key_order_independent():
    a = digest_args("plot_data", {"x": [1, 2], "y": [3, 4]})
    b = digest_args("plot_data", {"y": [3, 4], "x": [1, 2]})
    assert a == b
