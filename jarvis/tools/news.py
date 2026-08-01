"""Headlines from RSS/Atom feeds — keyless, deterministic (Post-MVP Faz 3).

Why RSS and not a search API. The briefing's gate is "0 fabricated items", and
a headline is the easiest thing in a briefing to fabricate convincingly: it is
a plausible Turkish sentence about a plausible event. A feed gives three
properties a search-and-summarize path cannot:

  * the title is a **verbatim string** from the publisher, not a paraphrase,
  * it carries its own URL, so a claim is checkable rather than trusted,
  * it costs no API key, so the briefing does not silently degrade the day a
    quota runs out.

Tavily is already wired (``web_search``) and stays the tool for "araştır bunu".
It is the wrong instrument for "what happened today" precisely because its
value is synthesis, and synthesis is the step Faz 3 removes from the data path.

Parsing is stdlib ``xml.etree.ElementTree`` -- no feedparser dependency for
what is two element layouts. Both are handled because the defaults span both:
BBC Türkçe and TRT Haber publish RSS 2.0, NTV publishes Atom. That was
verified against the live endpoints on 2026-08-01, not assumed from the
format's popularity.

Failure is **per feed**, never per section. A dead feed contributes nothing
and reports its own error; the remaining feeds still produce headlines. The
one endpoint that failed during that same live check (sozcu.com.tr/feed, HTTP
404) is why this is written that way rather than as a single try/except around
the whole fetch.
"""

from __future__ import annotations

import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)

_ATOM = "{http://www.w3.org/2005/Atom}"

DEFAULT_TIMEOUT_SEC = 8.0
DEFAULT_LIMIT = 5

# Turkish general-news feeds, all confirmed returning HTTP 200 on 2026-08-01.
# Overridable wholesale via Settings.news_feeds -- these are a default, not a
# claim about which outlets the owner wants. Deliberately three: enough that
# one outlet's outage or editorial slant does not define the briefing, few
# enough that the concurrent fetch stays inside the section's deadline.
DEFAULT_FEEDS: tuple[str, ...] = (
    "https://feeds.bbci.co.uk/turkce/rss.xml",   # RSS 2.0
    "https://www.ntv.com.tr/gundem.rss",         # Atom
    "https://www.trthaber.com/sondakika.rss",    # RSS 2.0
)

_USER_AGENT = "JARVIS/1.0 (personal assistant; RSS reader)"

_WHITESPACE = re.compile(r"\s+")


class NewsUnavailable(RuntimeError):
    """No feed produced a single headline. Carries the per-feed reasons so the
    briefing can say what failed instead of showing an empty list."""


@dataclass(frozen=True)
class NewsItem:
    """One headline, exactly as published. `title` is never rewritten -- only
    HTML-unescaped and whitespace-collapsed, both of which are reversible
    presentation fixes rather than edits to the claim."""

    title: str
    url: str
    source: str
    published: str = ""

    def as_line(self) -> str:
        return f"{self.source}: {self.title}" + (f" ({self.url})" if self.url else "")


@dataclass(frozen=True)
class FeedFailure:
    """A feed that could not be read, and why."""

    url: str
    reason: str

    def as_line(self) -> str:
        return f"{_host(self.url)}: {self.reason}"


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except ValueError:
        return url


def _reason(exc: Exception) -> str:
    """A one-line failure reason, short enough to sit inside a briefing bullet.

    httpx's HTTPStatusError message is three lines and ends with a link to
    MDN's status-code page -- useful in a traceback, noise in a sentence the
    assistant reads out loud. The exception TYPE is kept because it is the part
    that distinguishes a timeout from a 404 from malformed XML.
    """
    text = _WHITESPACE.sub(" ", str(exc)).strip()
    text = text.split(" For more information check:")[0].strip()
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _clean(text: Any) -> str:
    """Unescape entities and collapse whitespace. Nothing else.

    A headline that arrives as "Bakan a&#231;&#305;klad&#305;" is the same
    claim as "Bakan açıkladı"; a headline that arrives with a line break in it
    is the same claim on one line. Neither is a rewrite. Anything beyond this
    -- truncation, summarizing, "cleaning up" -- would make the title stop
    being a verbatim quote, which is the only reason this source is trusted.
    """
    if not isinstance(text, str):
        return ""
    return _WHITESPACE.sub(" ", html.unescape(text)).strip()


def parse_feed(payload: bytes, feed_url: str = "") -> tuple[str, list[NewsItem]]:
    """Bytes of an RSS 2.0 or Atom document → (feed title, items).

    Split out from fetching so the parser is testable against fixture bytes
    with no network at all -- the same separation jarvis/finance_parser.py
    uses, and for the same reason: a parser that can only be exercised through
    a live HTTP call gets tested against whatever the internet happened to
    return that day.
    """
    import xml.etree.ElementTree as ET

    # A UTF-8 BOM before the XML declaration is legal on the wire and fatal to
    # ElementTree ("XML or text declaration not at start of entity"). One
    # default feed (anadoluajansi) ships one; strip it rather than lose the
    # feed to a byte the standard permits.
    if payload[:3] == b"\xef\xbb\xbf":
        payload = payload[3:]

    root = ET.fromstring(payload)

    channel = root.find("channel")
    if channel is not None:                      # RSS 2.0
        title_el = channel.find("title")
        source = _clean(title_el.text if title_el is not None else "") or _host(feed_url)
        items = [_rss_item(el, source) for el in channel.findall("item")]
    elif root.tag == f"{_ATOM}feed":             # Atom
        title_el = root.find(f"{_ATOM}title")
        source = _clean(title_el.text if title_el is not None else "") or _host(feed_url)
        items = [_atom_entry(el, source) for el in root.findall(f"{_ATOM}entry")]
    else:
        raise NewsUnavailable(f"tanınmayan besleme biçimi (<{root.tag}>)")

    # An entry with no title is not a headline. Dropped rather than rendered
    # as an empty bullet the model would then have to explain.
    return source, [item for item in items if item.title]


def _rss_item(el: Any, source: str) -> NewsItem:
    def text_of(tag: str) -> str:
        child = el.find(tag)
        return _clean(child.text if child is not None else "")

    return NewsItem(
        title=text_of("title"),
        url=text_of("link"),
        source=source,
        published=text_of("pubDate"),
    )


def _atom_entry(el: Any, source: str) -> NewsItem:
    title_el = el.find(f"{_ATOM}title")
    published_el = el.find(f"{_ATOM}published")
    if published_el is None:
        published_el = el.find(f"{_ATOM}updated")

    # Atom puts the URL in an attribute, and an entry may carry several links
    # (alternate, related, enclosure). "alternate" is the human-readable page;
    # falling back to the first link with an href keeps a feed that omits the
    # rel attribute working, since rel="alternate" is Atom's own default.
    url = ""
    for link in el.findall(f"{_ATOM}link"):
        href = link.get("href") or ""
        if not href:
            continue
        if (link.get("rel") or "alternate") == "alternate":
            url = href
            break
        if not url:
            url = href

    return NewsItem(
        title=_clean(title_el.text if title_el is not None else ""),
        url=_clean(url),
        source=source,
        published=_clean(published_el.text if published_el is not None else ""),
    )


def _fetch_one(feed_url: str, timeout: float) -> tuple[str, list[NewsItem]]:
    import httpx

    response = httpx.get(
        feed_url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    return parse_feed(response.content, feed_url)


def fetch_headlines(
    feeds: "list[str] | tuple[str, ...] | None" = None,
    limit: int = DEFAULT_LIMIT,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> tuple[list[NewsItem], list[FeedFailure]]:
    """Top headlines across `feeds`, plus whatever failed.

    Feeds are fetched concurrently (they are independent network waits, and
    the briefing has a p50 < 5 s budget to share among four sources), but the
    RESULT ORDER IS NOT the completion order: results are re-sorted into the
    configured feed order and then interleaved round-robin, one headline per
    feed per pass.

    Both properties are deliberate. Determinism means the same feeds produce
    the same briefing regardless of which server answered first -- a briefing
    that reorders itself between runs cannot be regression-tested. Round-robin
    means a feed that publishes every four minutes cannot fill the whole list
    while two other outlets go unheard; taking the first N in feed order would
    make `limit=5` mean "the first outlet, five times".
    """
    from jarvis.nlu.temporal import fold

    urls = [u for u in (feeds if feeds is not None else DEFAULT_FEEDS) if str(u).strip()]
    if not urls:
        return [], []

    per_feed: dict[str, list[NewsItem]] = {}
    failures: list[FeedFailure] = []

    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        futures = {pool.submit(_fetch_one, url, timeout): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                _source, items = future.result()
                per_feed[url] = items
            except Exception as exc:  # noqa: BLE001 -- network, HTTP, or XML
                failures.append(FeedFailure(url, _reason(exc)))
                logger.info("news: feed failed %s -- %s", url, exc)

    ordered: list[NewsItem] = []
    seen_titles: set[str] = set()
    depth = max((len(items) for items in per_feed.values()), default=0)
    for index in range(depth):
        for url in urls:                       # configured order, not arrival order
            items = per_feed.get(url) or []
            if index >= len(items):
                continue
            item = items[index]
            # Two outlets running the same wire story is one piece of news, not
            # two. Deduped on the title alone -- anything fuzzier would be a
            # judgement call about whether two claims are the same claim, which
            # is the kind of decision this module exists to avoid making.
            #
            # fold(), not casefold(). str.casefold is locale-independent and
            # therefore wrong for Turkish in both directions: "AYNI".casefold()
            # is "ayni" while "Aynı".casefold() is "aynı", so a headline the
            # wire service ran in caps and the same headline in title case did
            # not match. Same İ/ı trap jarvis/graph/tool_router.py's header
            # documents; fold() is the project's existing answer to it.
            key = fold(item.title)
            if key in seen_titles:
                continue
            seen_titles.add(key)
            ordered.append(item)
            if len(ordered) >= limit:
                return ordered, failures
    return ordered, failures


def fetch_for_settings(
    settings: "Settings | None" = None,
    limit: int = 0,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> tuple[list[NewsItem], list[FeedFailure]]:
    """The configured feeds and headline count. One place, so the briefing
    service and the model-facing tool cannot disagree about the source list."""
    feeds = list(getattr(settings, "news_feeds", None) or DEFAULT_FEEDS)
    count = limit or int(getattr(settings, "news_headline_count", None) or DEFAULT_LIMIT)
    return fetch_headlines(feeds, limit=count, timeout=timeout)


def news_headlines(limit: int = 0, settings: "Settings | None" = None) -> str:
    """Model-facing string for the `news` tool."""
    try:
        items, failures = fetch_for_settings(settings, limit=limit)
    except Exception as exc:  # noqa: BLE001 -- defensive: fetch_for_settings absorbs its own
        return f"[ERROR] Haber başlıkları alınamadı: {exc}"

    if not items:
        detail = "; ".join(f.as_line() for f in failures) or "besleme yapılandırılmamış"
        return f"[ERROR] Haber başlığı alınamadı ({detail})."

    lines = [f"[News] {len(items)} başlık:"]
    lines.extend(f"• {item.as_line()}" for item in items)
    if failures:
        # Named, not swallowed: "3 of 5 sources answered" is a different
        # statement than "here is the news", and the model must be able to
        # make the honest one.
        lines.append(
            "(Ulaşılamayan kaynak: " + "; ".join(f.as_line() for f in failures) + ")"
        )
    return "\n".join(lines)
