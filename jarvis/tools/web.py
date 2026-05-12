"""Tavily web search wrapper for JARVIS tools."""

from __future__ import annotations

_cache: dict[str, str] = {}


def _get_client(api_key: str):
    try:
        from tavily import TavilyClient
        return TavilyClient(api_key=api_key)
    except ImportError:
        return None


def tavily_search_raw(query: str, api_key: str, max_results: int = 5) -> list[dict]:
    """Search with Tavily and return raw result dicts (title, url, content, score)."""
    if not api_key:
        return []
    client = _get_client(api_key)
    if not client:
        return []
    try:
        response = client.search(query=query, max_results=max_results)
        return response.get("results", [])
    except Exception:
        return []


def tavily_search(query: str, api_key: str, max_results: int = 5) -> str:
    """Search the web with Tavily. Returns markdown-formatted results."""
    if not api_key:
        return "[ERROR] TAVILY_API_KEY not set in .env — web search unavailable."

    cache_key = f"{query}:{max_results}"
    if cache_key in _cache:
        return _cache[cache_key]

    client = _get_client(api_key)
    if not client:
        return "[ERROR] tavily-python not installed. Run: pip install tavily-python"

    try:
        response = client.search(query=query, max_results=max_results)
    except Exception as e:
        return f"[ERROR] Tavily search failed: {e}"

    results = response.get("results", [])
    if not results:
        return f"No results found for: {query}"

    lines = [f"**Search results for:** {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = r.get("content", "").strip()[:400]
        lines.append(f"**{i}. {title}**\nURL: {url}\n{content}\n")

    result_str = "\n".join(lines)
    _cache[cache_key] = result_str
    return result_str
