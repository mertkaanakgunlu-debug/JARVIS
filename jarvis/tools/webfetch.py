"""URL content fetcher for deep web research (Faz 7).

Fetch priority:
  1. Firecrawl  — if FIRECRAWL_API_KEY is set; handles JS-heavy pages
  2. trafilatura — free, best-in-class article/text extraction
  3. httpx + tag strip — last resort for simple HTML pages
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from jarvis.url_policy import is_blocked_url as _is_blocked_url

if TYPE_CHECKING:
    from jarvis.config import Settings

_MAX_CHARS = 8000
_TIMEOUT = 15

# BUG-6-ssrf (Faz 4): this tool fetches whatever URL the model is told to --
# including URLs suggested by content it has already read (a prompt-injection
# vector: a page could tell JARVIS to "now fetch http://169.254.169.254/...").
# SSRF guard itself now lives in jarvis/url_policy.py (GPT-5.6 review
# remediation, Faz 4) so jarvis/mcp_integration.py's browser_navigate guard
# shares the exact same logic instead of a second, possibly-drifting copy.


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
        # [BLOCKED], not [ERROR]: this is a policy refusal, not a fetch
        # failure -- same convention as shell_run's deny-list and the MCP
        # browser guard ("[BLOCKED: ...] Refusing to navigate"), and the
        # structural signal the eval oracle's BLOCKED verdicts key on (C9's
        # SSRF block was invisible to it under the old prefix, live-found
        # 2026-07-18).
        return f"[BLOCKED] Refusing to fetch this URL ({why}): {url}"

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
