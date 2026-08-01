"""Post-MVP Faz 2.75, Paket F — which tools a turn actually gets to see.

Three defects, all verified against the running router before they were fixed:

1. Generic VERBS were domain patterns. `\\blistele` sat in "files" and
   `\\bara\\b` in "web", so "Son 3 mailimi listele" scored mail=1, files=1 and
   read as a two-domain chain -- 4 of 16 realistic single-call requests.

2. `\\bpdf\\b` required a word boundary. Turkish attaches suffixes straight onto
   the acronym, so "PDFteki toplantıları takvime ekle" matched nothing in
   "files" and the model was handed **no tool that can read a PDF**.
   ("PDF'teki" worked only because an apostrophe is a boundary.)

3. Slots were filled per domain in route order. "…satis.csv…oku ve grafiğini
   çiz" routes to [files, data]; files has 7 tools and took 7 of 8 slots, so
   data got one -- data_analyze -- and **plot_data was never offered**. The
   Faz 2.5 A/B then measured the model "failing to complete the chain" 0/10 on
   both tiers. It was never given the tool that draws charts.
"""
from __future__ import annotations

import pytest

from jarvis.graph.tool_router import (
    MAX_TOOLS_PER_TURN,
    classify_query,
    select_tool_names,
)
from jarvis.tool_registry import TOOL_SPECS

ALL = list(TOOL_SPECS)


def _tools(query: str) -> list[str]:
    return select_tool_names(classify_query(query), ALL, query)


# ── 1. generic verbs are not capabilities ─────────────────────────────────────

@pytest.mark.parametrize("query,domain", [
    ("Son 3 mailimi listele", "mail"),
    ("Bu haftaki toplantılarımı listele", "calendar"),
    ("Notlarımı ara", "memory"),
    ("Yapılacaklarımı listele", "tasks"),
])
def test_a_verb_does_not_add_a_domain(query, domain):
    assert classify_query(query).domains == [domain]


@pytest.mark.parametrize("query,domain", [
    ("Masaüstündeki dosyaları listele", "files"),
    ("Dosyayı oku", "files"),
    ("İnternette araştır", "web"),
])
def test_the_noun_still_carries_the_domain(query, domain):
    """Removing the verbs must not remove the routing. Every real request
    names the thing as well as the operation."""
    assert classify_query(query).primary_domain == domain


# ── 2. a Turkish suffix on an acronym ─────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "PDFteki toplantıları takvime ekle",
    "PDF'teki toplantıları takvime ekle",
    "pdfteki tabloyu oku",
    "şu pdf dosyasını oku",
])
def test_naming_a_pdf_offers_a_pdf_reader(query):
    assert "pdf_read" in _tools(query), f"{query!r} -> {_tools(query)}"


@pytest.mark.parametrize("query,tool", [
    ("csvyi oku", "csv_read"),
    ("csv'yi oku", "csv_read"),
    ("excelde ne var", "excel_read"),
    ("excel dosyasını aç", "excel_read"),
])
def test_naming_a_format_offers_its_reader(query, tool):
    assert tool in _tools(query), f"{query!r} -> {_tools(query)}"


def test_a_format_word_is_not_counted_twice():
    """\\bcsv MOVED from "data" to "files" rather than being copied.

    Leaving it in both would score the word twice -- the mistake the module's
    own header warns about for \\brapor -- and let a one-format request outrank
    a genuinely two-domain one."""
    route = classify_query("csvyi oku")
    assert route.domains == ["files"]
    assert route.confidence == 1.0


# ── 3. the tool the user's second verb asked for ──────────────────────────────

CHAIN = "Masaüstündeki satis.csv dosyasını oku ve aylardaki satışların çizgi grafiğini çiz"


def test_the_chain_request_is_offered_both_halves():
    """The regression that silently invalidated a whole measurement round."""
    selected = _tools(CHAIN)
    assert "csv_read" in selected, selected
    assert "plot_data" in selected, selected


def test_the_named_format_is_ranked_first_in_its_domain():
    """Relevance ordering, so truncation drops the irrelevant tools rather
    than whichever the registry happened to list last."""
    selected = _tools(CHAIN)
    assert selected[0] == "csv_read", selected


def test_every_routed_domain_gets_a_fair_share():
    route = classify_query(CHAIN)
    assert len(route.domains) == 2, "premise"
    selected = _tools(CHAIN)
    per_domain: dict[str, int] = {}
    for name in selected:
        per_domain[TOOL_SPECS[name].domain] = per_domain.get(TOOL_SPECS[name].domain, 0) + 1
    share = MAX_TOOLS_PER_TURN // len(route.domains)
    for domain in route.domains:
        # A domain cannot be given more tools than it has -- `data` only owns
        # two. The guarantee is its full share OR everything it owns.
        owned = sum(1 for n in ALL if TOOL_SPECS[n].domain == domain)
        assert per_domain.get(domain, 0) >= min(share, owned), (
            f"{domain} got {per_domain.get(domain, 0)} of {selected}"
        )


def test_the_cap_still_holds():
    for query in (CHAIN, "PDFteki toplantıları takvime ekle",
                  "Maillerimi kontrol et, hesabımdaki para akışını analiz et, "
                  "bir excel tablosuna dönüştür ve grafikle"):
        assert len(_tools(query)) <= MAX_TOOLS_PER_TURN, query


def test_the_cap_holds_even_with_more_domains_than_slots():
    """Not reachable from classify_query today (MAX_DOMAINS_PER_TURN is 3), and
    that is exactly why it needs asserting directly.

    Round 1 hands each routed domain `max(1, MAX // len(groups))` tools, so with
    more domains than slots the floor of 1 each overshoots the cap. A route can
    arrive from an old checkpoint or a raised MAX_DOMAINS_PER_TURN; the final
    slice is what makes the promise unconditional, and without this test a
    mutation deleting it survives.
    """
    from jarvis.graph.tool_router import ToolRoute

    many = [d for d in
            ("mail", "calendar", "drive", "media", "finance", "tasks",
             "web", "files", "data", "memory")]
    route = ToolRoute(many[0], many, 0.1, False)
    selected = select_tool_names(route, ALL, "hepsi")
    assert len(selected) <= MAX_TOOLS_PER_TURN, selected


def test_a_single_domain_turn_is_unchanged():
    """Fair share must be a no-op when there is only one domain to share with."""
    selected = _tools("Masaüstündeki dosyaları listele")
    files = [n for n in ALL if TOOL_SPECS[n].domain == "files"]
    assert set(selected) == set(files)


# ── ordering must not silently reshuffle a domain it cannot rank ──────────────

def test_the_domain_word_does_not_rank_tools_against_each_other():
    """"mail" is in itu_mail's name and not in gmail's, which says nothing
    about which mailbox the user meant -- it ranked the university mailbox
    first for "Son 3 mailimi listele" until the domain word was excluded."""
    assert _tools("Son 3 mailimi listele") == ["gmail", "itu_mail"]


def test_no_query_means_registry_order():
    """The parameter is optional so every existing caller and old checkpoint
    keeps working; without it, ordering is what it always was."""
    route = classify_query("Masaüstündeki dosyaları listele")
    assert select_tool_names(route, ALL) == [
        n for n in ALL if TOOL_SPECS[n].domain == "files"
    ]


# ── nothing is dropped in silence ─────────────────────────────────────────────

def test_dropping_tools_is_logged(caplog):
    """When a turn goes wrong the first question is "did the model even have
    the tool", and that was unanswerable from the logs -- which is how
    plot_data went missing for an entire measurement round."""
    import logging

    # CHAIN routes to files(7) + data(2) = 9 candidates for 8 slots, so
    # exactly one is dropped. A query where everything fits would log nothing
    # and the test would pass for the wrong reason.
    with caplog.at_level(logging.INFO, logger="jarvis.graph.tool_router"):
        selected = _tools(CHAIN)
    assert len(selected) == MAX_TOOLS_PER_TURN, "premise: this turn overflows"
    assert any("dropped" in r.message or "dropped" in r.getMessage()
               for r in caplog.records), caplog.text
