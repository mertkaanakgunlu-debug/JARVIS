"""jarvis/api.py -- Faz 1 of the GPT-5.6 review remediation plan (secure-by-
default API): host binding must fail fast rather than silently exposing an
unauthenticated API, and CORS must never be an unconditional wildcard.

See docs/SAFETY.md / HANDOFF.md for context. No network I/O here -- these
test the pure decision functions (resolve_api_bind_host, resolve_cors_origins)
directly, not a live uvicorn bind or a real ASGI request.
"""
from __future__ import annotations

import pytest

from jarvis.api import resolve_api_bind_host, resolve_cors_origins
from jarvis.config import Settings


def _settings(**overrides) -> Settings:
    # isolated_cwd (not requested here -- these tests never construct
    # anything that touches data/) keeps Settings() itself from reading the
    # real project .env when a test wants a clean baseline.
    return Settings(_env_file=None, **overrides)


# ── resolve_api_bind_host ────────────────────────────────────────────────────

def test_no_key_no_host_defaults_to_loopback():
    assert resolve_api_bind_host(_settings()) == "127.0.0.1"


def test_key_set_no_host_defaults_to_all_interfaces():
    assert resolve_api_bind_host(_settings(jarvis_api_key="secret")) == "0.0.0.0"


def test_no_key_explicit_loopback_host_is_ok():
    assert resolve_api_bind_host(_settings(api_host="127.0.0.1")) == "127.0.0.1"
    assert resolve_api_bind_host(_settings(api_host="localhost")) == "localhost"


def test_no_key_explicit_non_loopback_host_fails_fast():
    with pytest.raises(RuntimeError, match="JARVIS_API_KEY is empty"):
        resolve_api_bind_host(_settings(api_host="0.0.0.0"))
    with pytest.raises(RuntimeError):
        resolve_api_bind_host(_settings(api_host="192.168.1.50"))


def test_key_set_explicit_non_loopback_host_is_ok():
    assert resolve_api_bind_host(_settings(api_host="0.0.0.0", jarvis_api_key="secret")) == "0.0.0.0"


# ── resolve_cors_origins ─────────────────────────────────────────────────────

def test_cors_never_wildcards():
    origins = resolve_cors_origins(_settings())
    assert "*" not in origins


def test_cors_always_allows_electron_file_origin():
    origins = resolve_cors_origins(_settings())
    assert "file://" in origins
    assert "null" in origins


def test_cors_includes_configured_extra_origins():
    origins = resolve_cors_origins(_settings(api_cors_origins=["http://192.168.1.50:3000"]))
    assert "http://192.168.1.50:3000" in origins
    # Base allowances still present alongside the configured extras.
    assert "file://" in origins
