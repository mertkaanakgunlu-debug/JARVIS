"""URL content fetcher for deep web research (Faz 7).

Fetch priority:
  1. Firecrawl  — if FIRECRAWL_API_KEY is set; handles JS-heavy pages
  2. trafilatura — free, best-in-class article/text extraction
  3. httpx + tag strip — last resort for simple HTML pages
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

_MAX_CHARS = 8000
_TIMEOUT = 15

# BUG-6-ssrf (Faz 4): this tool fetches whatever URL the model is told to --
# including URLs suggested by content it has already read (a prompt-injection
# vector: a page could tell JARVIS to "now fetch http://169.254.169.254/...").
# Block requests that would reach this machine itself, other hosts on its
# LAN, or a cloud metadata endpoint -- an attacker-controlled public domain
# resolving to one of these via DNS rebinding is exactly why this checks the
# resolved IP, not just the hostname string.
_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def _is_blocked_address(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def _is_blocked_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return True, "unparseable URL"
    host = (parsed.hostname or "").lower()
    if not host:
        return True, "no host in URL"
    if host in _BLOCKED_HOSTNAMES:
        return True, f"blocked host: {host}"
    if _is_blocked_address(host):
        return True, f"blocked address: {host}"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, ""  # let the real fetch surface the DNS failure
    for info in infos:
        resolved_ip = info[4][0]
        if _is_blocked_address(resolved_ip):
            return True, f"{host} resolves to a private/local address ({resolved_ip})"
    return False, ""


def _firecrawl_fetch(url: str, api_key: str, max_chars: int) -> str | None:
    try:
        from firecrawl import FirecrawlApp

        app = FirecrawlApp(api_key=api_key)
        result = app.scrape_url(url, formats=["markdown"])
        md = (result.get("markdown") or result.get("content") or "").strip()
        if not md:
            return None
        title = (result.get("metadata") or {}).get("title", "")
        header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"
        return header + md[:max_chars]
    except Exception:
        return None


def _trafilatura_fetch(url: str, max_chars: int) -> str | None:
    try:
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        if not text or len(text.strip()) < 80:
            return None
        return f"Source: {url}\n\n{text[:max_chars]}"
    except Exception:
        return None


def _httpx_fetch(url: str, max_chars: int) -> str | None:
    try:
        import httpx

        headers = {"User-Agent": "Mozilla/5.0 (compatible; JARVIS-research/1.0)"}
        r = httpx.get(url, headers=headers, timeout=_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        html = r.text
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 100:
            return None
        return f"Source: {url}\n\n{text[:max_chars]}"
    except Exception:
        return None


def fetch_url(url: str, settings: "Settings", max_chars: int = _MAX_CHARS) -> str:
    """Fetch a URL and return clean readable text.

    Tries Firecrawl (if configured), then trafilatura, then httpx+strip.
    Returns an error string if all methods fail.
    """
    if not url.startswith(("http://", "https://")):
        return f"[ERROR] Invalid URL (must start with http/https): {url}"

    blocked, why = _is_blocked_url(url)
    if blocked:
        return f"[ERROR] Refusing to fetch this URL ({why}): {url}"

    if settings.firecrawl_api_key:
        result = _firecrawl_fetch(url, settings.firecrawl_api_key, max_chars)
        if result:
            return result

    result = _trafilatura_fetch(url, max_chars)
    if result:
        return result

    result = _httpx_fetch(url, max_chars)
    if result:
        return result

    return f"[ERROR] Could not extract readable content from: {url}"
