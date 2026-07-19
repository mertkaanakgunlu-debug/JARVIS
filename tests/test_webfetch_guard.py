"""fetch_url's SSRF refusal contract (round 3, live-found in the A/B run).

The refusal must carry the [BLOCKED] prefix, not [ERROR]: it is a policy
refusal, same convention as shell_run's deny-list and the MCP browser guard,
and it is the structural signal the eval oracle's BLOCKED verdicts (C9) key
on -- under the old [ERROR] prefix a real, correctly-working SSRF block was
invisible to the oracle and scored FAIL.

No network involved: a literal loopback/private address is rejected by
jarvis/url_policy.py before any fetch is attempted.
"""
from __future__ import annotations

from jarvis.config import Settings
from jarvis.tools.webfetch import fetch_url


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_ssrf_refusal_is_blocked_prefixed_not_error():
    # 2026-07-19: the prefix now carries the machine-readable reason code —
    # "[BLOCKED:<code>]" — which tool_accounting lifts into trace rows.
    out = fetch_url("http://127.0.0.1:8132/status", _settings())
    assert out.startswith("[BLOCKED:ssrf_private_address]"), out
    assert "127.0.0.1" in out


def test_localhost_hostname_also_blocked():
    out = fetch_url("http://localhost/admin", _settings())
    assert out.startswith("[BLOCKED:ssrf_blocked_hostname]"), out


def test_invalid_scheme_stays_a_plain_error():
    """Not every refusal is a policy block: a malformed URL is an input
    error and must keep the [ERROR] prefix so the two stay distinguishable."""
    out = fetch_url("ftp://example.com/x", _settings())
    assert out.startswith("[ERROR]"), out
