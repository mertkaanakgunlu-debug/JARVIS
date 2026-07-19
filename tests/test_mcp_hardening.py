"""jarvis/mcp_integration.py + jarvis/url_policy.py -- Faz 4 of the GPT-5.6
review remediation plan (MCP/browser hardening).

Two independent fixes:
1. browser_navigate had no SSRF guard at all -- the browser could be pointed
   at a private/localhost/link-local/metadata address via any URL the model
   was told to visit. Now shares jarvis/tools/webfetch.py's url_read guard
   via the new jarvis/url_policy.py module, applied as a
   langchain-mcp-adapters ToolCallInterceptor.
2. @playwright/mcp@latest was unpinned -- npx re-resolves it on every
   launch, so a server-side release reaches this project with zero review.
"""
from __future__ import annotations

import pytest

from jarvis.config import Settings
from jarvis.mcp_integration import (
    _PLAYWRIGHT_MCP_VERSION,
    _playwright_server_config,
    _ssrf_guard_interceptor,
)
from jarvis.url_policy import is_blocked_url


# ── jarvis/url_policy.py ──────────────────────────────────────────────────────

def test_localhost_hostname_is_blocked():
    blocked, why, code = is_blocked_url("http://localhost:8000/admin")
    assert blocked
    assert "localhost" in why
    assert code == "ssrf_blocked_hostname"


def test_link_local_metadata_ip_is_blocked():
    blocked, _, code = is_blocked_url("http://169.254.169.254/latest/meta-data/")
    assert blocked
    assert code == "ssrf_private_address"


def test_private_lan_ip_is_blocked():
    blocked, _, code = is_blocked_url("http://192.168.1.1/")
    assert blocked
    assert code == "ssrf_private_address"


def test_loopback_ip_is_blocked():
    blocked, _, code = is_blocked_url("http://127.0.0.1:9000/")
    assert blocked
    assert code == "ssrf_private_address"


def test_public_looking_ip_is_not_blocked():
    blocked, why, code = is_blocked_url("http://93.184.216.34/")
    assert not blocked, why
    assert code == ""


def test_unparseable_url_is_blocked():
    blocked, _, code = is_blocked_url("not a url at all::::")
    assert blocked
    assert code in ("ssrf_unparseable_url", "ssrf_no_host")


def test_url_with_no_host_is_blocked():
    blocked, _, code = is_blocked_url("file:///etc/passwd")
    assert blocked
    assert code == "ssrf_no_host"


# ── mcp_integration.py: pinned version ───────────────────────────────────────

def test_playwright_version_is_pinned_not_latest():
    settings = Settings(_env_file=None, mcp_playwright_enabled=True)
    config = _playwright_server_config(settings)
    args_str = " ".join(config["args"])
    assert "@latest" not in args_str
    assert f"@playwright/mcp@{_PLAYWRIGHT_MCP_VERSION}" in args_str


# ── mcp_integration.py: SSRF interceptor ─────────────────────────────────────

class _FakeRequest:
    def __init__(self, name, args):
        self.name = name
        self.args = args


@pytest.mark.asyncio
async def test_interceptor_blocks_navigate_to_metadata_endpoint():
    handler_called = []

    async def handler(request):
        handler_called.append(request)
        return "should not be reached"

    request = _FakeRequest("browser_navigate", {"url": "http://169.254.169.254/latest/meta-data/"})
    result = await _ssrf_guard_interceptor(request, handler)

    assert not handler_called, "the real MCP call must never happen for a blocked URL"
    assert result.isError is True
    assert "169.254.169.254" in result.content[0].text or "BLOCKED" in result.content[0].text


@pytest.mark.asyncio
async def test_interceptor_allows_navigate_to_public_url():
    handler_called = []

    async def handler(request):
        handler_called.append(request)
        return "ok"

    request = _FakeRequest("browser_navigate", {"url": "http://93.184.216.34/"})
    result = await _ssrf_guard_interceptor(request, handler)

    assert handler_called
    assert result == "ok"


@pytest.mark.asyncio
async def test_interceptor_ignores_non_navigate_tools():
    """Only _URL_ARG_TOOLS are checked -- a tool with no url argument (e.g.
    browser_click) must pass through untouched regardless of its args."""
    handler_called = []

    async def handler(request):
        handler_called.append(request)
        return "ok"

    request = _FakeRequest("browser_click", {"selector": "#submit"})
    result = await _ssrf_guard_interceptor(request, handler)

    assert handler_called
    assert result == "ok"
