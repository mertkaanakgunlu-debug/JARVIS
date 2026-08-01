"""Post-MVP Faz 3 — the RSS/Atom news source.

The fixtures below are trimmed from what the three default feeds actually
served on 2026-08-01, keeping the structural features that differ between
them: BBC Türkçe and TRT Haber publish RSS 2.0, NTV publishes Atom with the
URL in a link ATTRIBUTE rather than element text. Both layouts are here
because both are in the default feed list -- a parser that only handled the
common one would silently drop a third of the briefing's headlines and look
fine in a test written against a single fixture.

`_fetch_one` is the seam. Patching it exercises the concurrency, ordering,
dedup and per-feed failure logic without a socket.
"""
from __future__ import annotations

import pytest

from jarvis.tools import news as news_tool
from jarvis.tools.news import (
    NewsItem,
    fetch_headlines,
    news_headlines,
    parse_feed,
)

# Encoded, not written as bytes literals: a bytes literal cannot hold a
# non-ASCII character, and the Turkish text is the point -- these feeds are
# where the diacritics come from.
RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>BBC Turkish</title>
  <item>
    <title>&#304;lk ba&#351;l&#305;k &amp; devam&#305;</title>
    <link>https://example.org/1</link>
    <pubDate>Sat, 01 Aug 2026 07:43:26 GMT</pubDate>
  </item>
  <item>
    <title>İkinci
      başlık</title>
    <link>https://example.org/2</link>
  </item>
</channel></rss>""".encode("utf-8")

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="text">ntv.com.tr</title>
  <entry>
    <title type="text">Atom başlık</title>
    <published>2026-08-01T14:19:23+03:00</published>
    <link rel="enclosure" href="https://example.net/image.jpg"/>
    <link rel="alternate" href="https://example.net/haber"/>
  </entry>
  <entry>
    <title type="text">rel'siz link</title>
    <link href="https://example.net/2"/>
  </entry>
</feed>""".encode("utf-8")

BOM_RSS = b"\xef\xbb\xbf" + RSS


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_rss_items_are_read_verbatim():
    source, items = parse_feed(RSS)
    assert source == "BBC Turkish"
    assert items[0].title == "İlk başlık & devamı"      # entities unescaped
    assert items[0].url == "https://example.org/1"
    assert items[0].published == "Sat, 01 Aug 2026 07:43:26 GMT"


def test_a_wrapped_title_becomes_one_line_and_nothing_else():
    """Whitespace collapse is a presentation fix, not an edit -- the claim is
    unchanged. Anything beyond it (truncation, summarizing) would stop the
    title being a verbatim quote, which is the only reason this source is
    trusted at all."""
    _source, items = parse_feed(RSS)
    assert items[1].title == "İkinci başlık"


def test_atom_takes_the_url_from_the_alternate_link_attribute():
    source, items = parse_feed(ATOM)
    assert source == "ntv.com.tr"
    assert items[0].url == "https://example.net/haber"   # not the enclosure
    assert items[0].published == "2026-08-01T14:19:23+03:00"


def test_atom_falls_back_when_rel_is_omitted():
    """rel="alternate" is Atom's own default, so a link without it is still
    the article."""
    _source, items = parse_feed(ATOM)
    assert items[1].url == "https://example.net/2"


def test_a_utf8_bom_does_not_lose_the_feed():
    """Legal on the wire and fatal to ElementTree. One default feed ships one."""
    source, items = parse_feed(BOM_RSS)
    assert source == "BBC Turkish"
    assert len(items) == 2


def test_a_titleless_entry_is_dropped_not_rendered_empty():
    payload = """<rss version="2.0"><channel><title>X</title>
      <item><link>https://example.org/a</link></item>
      <item><title>Gerçek</title><link>https://example.org/b</link></item>
    </channel></rss>""".encode("utf-8")
    _source, items = parse_feed(payload)
    assert [i.title for i in items] == ["Gerçek"]


def test_an_unknown_document_shape_raises_rather_than_returning_nothing():
    with pytest.raises(Exception):
        parse_feed(b"<html><body>not a feed</body></html>")


def test_a_feed_without_a_title_falls_back_to_its_host():
    payload = b"""<rss version="2.0"><channel>
      <item><title>T</title><link>https://example.org/a</link></item>
    </channel></rss>"""
    source, _items = parse_feed(payload, "https://haber.example.org/rss")
    assert source == "haber.example.org"


# ── Aggregation ──────────────────────────────────────────────────────────────

def _serve(monkeypatch, table: dict):
    """table: {feed_url: [NewsItem, ...] | Exception}"""
    def fake_fetch_one(url, timeout):
        result = table[url]
        if isinstance(result, Exception):
            raise result
        return "src", result

    monkeypatch.setattr(news_tool, "_fetch_one", fake_fetch_one)


def _items(source: str, count: int) -> list[NewsItem]:
    return [NewsItem(f"{source}-{i}", f"https://{source}/{i}", source) for i in range(count)]


def test_headlines_interleave_instead_of_letting_one_outlet_fill_the_list(monkeypatch):
    """Taking the first N in feed order would make limit=5 mean "the first
    outlet, five times"."""
    _serve(monkeypatch, {"a": _items("a", 5), "b": _items("b", 5), "c": _items("c", 5)})
    items, failures = fetch_headlines(["a", "b", "c"], limit=5)
    assert [i.title for i in items] == ["a-0", "b-0", "c-0", "a-1", "b-1"]
    assert failures == []


def test_order_follows_the_configured_feeds_not_arrival(monkeypatch):
    """Deterministic output is what makes the briefing regression-testable at
    all; results are re-sorted out of completion order on purpose."""
    _serve(monkeypatch, {"a": _items("a", 2), "b": _items("b", 2)})
    first = [i.title for i in fetch_headlines(["a", "b"], limit=4)[0]]
    second = [i.title for i in fetch_headlines(["a", "b"], limit=4)[0]]
    assert first == second == ["a-0", "b-0", "a-1", "b-1"]


def test_one_dead_feed_costs_only_itself(monkeypatch):
    """The reason failure is per-feed: sozcu.com.tr returned 404 during the
    live check that chose these defaults."""
    _serve(monkeypatch, {
        "alive": _items("alive", 2),
        "dead": RuntimeError("404 Not Found"),
    })
    items, failures = fetch_headlines(["alive", "dead"], limit=5)
    assert [i.title for i in items] == ["alive-0", "alive-1"]
    assert len(failures) == 1
    assert "404" in failures[0].reason


def test_the_same_wire_story_from_two_outlets_counts_once(monkeypatch):
    shared = NewsItem("Aynı başlık", "https://a/1", "a")
    _serve(monkeypatch, {
        "a": [shared],
        "b": [NewsItem("AYNI BAŞLIK", "https://b/1", "b"), NewsItem("Farklı", "https://b/2", "b")],
    })
    items, _failures = fetch_headlines(["a", "b"], limit=5)
    assert [i.title for i in items] == ["Aynı başlık", "Farklı"]


def test_a_short_feed_does_not_stop_the_longer_ones(monkeypatch):
    _serve(monkeypatch, {"a": _items("a", 1), "b": _items("b", 3)})
    items, _failures = fetch_headlines(["a", "b"], limit=4)
    assert [i.title for i in items] == ["a-0", "b-0", "b-1", "b-2"]


def test_no_feeds_configured_is_empty_not_an_error(monkeypatch):
    assert fetch_headlines([], limit=5) == ([], [])


# ── The model-facing string ──────────────────────────────────────────────────

def test_total_failure_is_reported_as_a_failure(monkeypatch):
    """"Here is the news" and "no source answered" are different statements,
    and only one of them is true when every feed is down."""
    _serve(monkeypatch, {"a": RuntimeError("timeout")})
    monkeypatch.setattr(news_tool, "DEFAULT_FEEDS", ("a",))
    out = news_headlines(3, None)
    assert out.startswith("[ERROR]")


def test_a_partial_failure_is_named_not_swallowed(monkeypatch):
    _serve(monkeypatch, {"a": _items("a", 2), "b": RuntimeError("timeout")})
    monkeypatch.setattr(news_tool, "DEFAULT_FEEDS", ("a", "b"))
    out = news_headlines(2, None)
    assert out.startswith("[News]")
    assert "Ulaşılamayan kaynak" in out


def test_the_failure_reason_fits_on_one_line():
    """httpx's HTTPStatusError message is three lines ending in an MDN link --
    useful in a traceback, noise in a sentence read out loud."""
    long_exc = RuntimeError(
        "Client error '404 Not Found' for url 'https://x/y'\n"
        "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404"
    )
    reason = news_tool._reason(long_exc)
    assert "\n" not in reason
    assert "developer.mozilla.org" not in reason
    assert reason.startswith("RuntimeError:")


def test_configured_feeds_override_the_defaults(monkeypatch):
    class FakeSettings:
        news_feeds = ["only-this"]
        news_headline_count = 2

    _serve(monkeypatch, {"only-this": _items("x", 4)})
    items, _failures = news_tool.fetch_for_settings(FakeSettings())
    assert [i.title for i in items] == ["x-0", "x-1"]
