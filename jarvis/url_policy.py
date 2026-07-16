"""Shared SSRF guard for anything that fetches/navigates to a model- or
agent-supplied URL (Faz 7's url_read, Faz 5's MCP browser_navigate).

BUG-6-ssrf (Faz 4) originally lived only in jarvis/tools/webfetch.py, guarding
url_read -- but any tool that navigates to an attacker-influenceable URL has
the exact same exposure (a page's own content, or a poisoned procedure, could
tell JARVIS to "now fetch http://169.254.169.254/..."). GPT-5.6 review
remediation, Faz 4 (2026-07-15): extracted into this shared module so
jarvis/mcp_integration.py's browser_navigate guard doesn't duplicate (and
risk drifting from) the same logic.

Blocks requests that would reach this machine itself, other hosts on its
LAN, or a cloud metadata endpoint. Checks the *resolved* IP, not just the
hostname string -- an attacker-controlled public domain resolving to one of
these via DNS rebinding is exactly why.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def is_blocked_address(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def is_blocked_url(url: str) -> tuple[bool, str]:
    """Return (blocked, reason). reason is empty when not blocked."""
    try:
        parsed = urlparse(url)
    except Exception:
        return True, "unparseable URL"
    host = (parsed.hostname or "").lower()
    if not host:
        return True, "no host in URL"
    if host in _BLOCKED_HOSTNAMES:
        return True, f"blocked host: {host}"
    if is_blocked_address(host):
        return True, f"blocked address: {host}"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, ""  # let the real fetch surface the DNS failure
    for info in infos:
        resolved_ip = info[4][0]
        if is_blocked_address(resolved_ip):
            return True, f"{host} resolves to a private/local address ({resolved_ip})"
    return False, ""
