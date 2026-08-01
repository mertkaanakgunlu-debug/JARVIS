"""Faz 2.4 — capability-closure sweep across all 13 router domains.

Invariant (GPT-5.6 review + this session): a typical task routed to a domain
must be completable with ONLY that domain's exposed tools. B6 was the first
breach found — the ``data`` domain exposed no way to get user-typed numbers onto
disk for the file-only plot_data; Faz 1.3 closed it by giving plot_data inline
data, so the B6 query now routes to ``data`` AND that domain can chart it.

This file locks the router-membership half of closure (right domain + its
completion tool visible). The execution half — that the visible tool can finish
with inline/available inputs — lives in the per-tool tests (e.g. test_plot_inline
for data). Together they prevent another B6.

It also pins the diacritic-fold fix (Faz 2.4): ASR/casual input drops Turkish
ç/ğ/ı/ö/ş/ü and apostrophes, which used to mis-route ("grafik ciz" →
conversation, "Drivea yukle" → files with google_drive invisible).
"""
from __future__ import annotations

import pytest

from jarvis.graph.tool_router import (
    MAX_TOOLS_PER_TURN,
    _fold,
    classify_query,
    select_tool_names,
)
from jarvis.tool_registry import TOOL_SPECS

ALL_NAMES = list(TOOL_SPECS)


# ── 13-domain membership closure ─────────────────────────────────────────────
# (query, expected_primary, tools that MUST be visible to complete the task)
CLOSURE = [
    ("Masaüstündeki rapor.pdf dosyasını oku", "files", {"file_read", "pdf_read"}),
    ("Python 3.13 çıkış tarihini internette ara ve ilk sonucu oku", "web",
        {"web_search", "url_read"}),                    # two-step: search → read
    ("Son 3 mailimi listele", "mail", {"gmail"}),
    ("Yarın saat 15'te diş hekimi randevusu ekle", "calendar", {"google_calendar"}),
    ("Bu dosyayı Google Drive'a yükle", "drive", {"google_drive"}),
    ("Şu sayılarla basit bir çizgi grafiği çiz: 1, 4, 9, 16", "data", {"plot_data"}),  # B6
    ("Şu PowerShell komutunu çalıştır: Get-Date", "system", {"shell_run", "python_run"}),
    ("Yarın sabah 9'da beni spor için hatırlat", "tasks", {"schedule", "todo"}),
    ("Spotify'da jazz çalma listesi çal", "media", {"spotify"}),
    ("Bu ayki harcamalarımı göster", "finance", {"finance"}),
    ("Arşivimde geçen haftaki toplantı notlarını bul", "memory", {"vault_search"}),
    ("Şu prosedürü kaydet: önce su haznesini doldur sonra filtreyi tak", "procedure",
        {"procedure_save"}),
]


@pytest.mark.parametrize("query,expected,required", CLOSURE, ids=[c[1] for c in CLOSURE])
def test_domain_routes_and_exposes_completion_tools(query, expected, required):
    route = classify_query(query)
    assert route.primary_domain == expected, f"{query!r} → {route.primary_domain}"
    selected = set(select_tool_names(route, ALL_NAMES))
    missing = required - selected
    assert not missing, f"closure gap in {expected}: {missing} not visible (has {selected})"
    assert len(selected) <= MAX_TOOLS_PER_TURN


def test_mcp_routes_even_though_native_set_is_empty():
    # MCP is quarantined + config-gated: routing must still land there so that,
    # WHEN a browser MCP is enabled, its tools are the ones exposed.
    route = classify_query("Tarayıcıda şu butona tıkla")
    assert route.primary_domain == "mcp"


# ── diacritic-insensitivity (Faz 2.4 fix) ────────────────────────────────────

@pytest.mark.parametrize("proper,ascii_,expected", [
    ("Şu sayılarla çizgi grafiği çiz: 1,4,9,16", "su sayilarla cizgi grafik ciz: 1,4,9,16", "data"),
    ("Spotify'da şarkı çal", "Spotifyda sarki cal", "media"),
    ("Şu prosedürü kaydet: önce X", "su proseduru kaydet: once X", "procedure"),
    ("Bu dosyayı Google Drive'a yükle", "Bu dosyayi Google Drivea yukle", "drive"),
    ("Yarın takvime toplantı ekle", "Yarin takvime toplanti ekle", "calendar"),
    ("Görevlerimi listele", "Gorevlerimi listele", "tasks"),
])
def test_routing_is_diacritic_insensitive(proper, ascii_, expected):
    assert classify_query(proper).primary_domain == expected
    assert classify_query(ascii_).primary_domain == expected


def test_ascii_drive_still_exposes_google_drive():
    route = classify_query("Bu dosyayi Google Drivea yukle")
    assert "google_drive" in select_tool_names(route, ALL_NAMES)


def test_fold_maps_turkish_to_ascii_lowercase():
    assert _fold("ÇizGİ Şarkı Ürün") == "cizgi sarki urun"
    assert _fold("GRAFİK") == "grafik"


# ── MULTI-domain closure (2026-07-30) ────────────────────────────────────────
# The membership closure above is per-domain. B6 was a single-domain gap; the MVP
# prompt exposed a MULTI-domain one, and it was structural rather than a missing
# pattern: the "data" domain held exactly 8 tools while MAX_TOOLS_PER_TURN is
# also 8, so once "data" was primary it consumed the whole budget and every
# other routed domain was silently dropped.
#
# Measured before the fix: "Maillerimi kontrol et, hesabımdaki para akışını
# analiz et, bir excel tablosuna dönüştür ve grafikle" routed to [data, mail] and
# exposed 8 data tools with gmail AND finance both invisible. The live model then
# reported "Hesabınızda ... e-posta bulunmuyor" -- a verdict on a mailbox it had
# no tool to open.

MVP_PROMPT = (
    "Maillerimi kontrol et, hesabımdaki para akışını analiz et, "
    "bir excel tablosuna dönüştür ve grafikle"
)


def test_mvp_prompt_exposes_every_tool_the_task_needs():
    """The whole chain must be visible in ONE turn: read mail, analyse/export
    money, chart the result."""
    route = classify_query(MVP_PROMPT)
    selected = set(select_tool_names(route, ALL_NAMES))

    missing = {"gmail", "finance", "plot_data"} - selected
    assert not missing, f"MVP closure gap: {missing} not visible (has {selected})"
    assert len(selected) <= MAX_TOOLS_PER_TURN


def test_no_single_domain_can_consume_the_whole_tool_budget():
    """The invariant that makes the above robust to future growth: a domain must
    leave room for the other domains a request routed to."""
    from jarvis.graph.tool_router import _DOMAIN_PATTERNS

    oversized = {}
    for domain in _DOMAIN_PATTERNS:
        members = [n for n in ALL_NAMES if (TOOL_SPECS[n].domain or "mcp") == domain]
        if len(members) >= MAX_TOOLS_PER_TURN:
            oversized[domain] = len(members)
    assert not oversized, (
        f"domain(s) at/over the per-turn cap: {oversized}. A domain this size "
        "starves every other domain in a multi-domain request -- split it rather "
        "than raising MAX_TOOLS_PER_TURN."
    )


def test_secondary_domains_keep_a_slot_even_when_the_primary_is_large():
    """Direct test of the reservation rule, independent of today's domain sizes."""
    route = classify_query(MVP_PROMPT)
    assert len(route.domains) > 1, "premise: this is a multi-domain route"

    selected = select_tool_names(route, ALL_NAMES)
    domains_present = {TOOL_SPECS[n].domain for n in selected}
    for domain in route.domains:
        assert domain in domains_present, (
            f"routed domain {domain!r} contributed no tool; "
            f"got {sorted(domains_present)}"
        )


@pytest.mark.parametrize("query,expected", [
    # The split moved \brapor and \bhesapla out of "data"; these prove the moves
    # landed and that nothing that used to route still misroutes.
    ("Bu konuda bir rapor yaz", "report"),
    ("Şu integrali hesapla: x^2 dx", "math"),
    ("Bu denklemi çöz", "math"),
    ("Şu sayılarla çizgi grafiği çiz: 1, 4, 9, 16", "data"),
    # Faz 2.75 (Paket F) moved csv and excel from "data" to "files", for
    # the same reason rapor moved to "report": a pattern belongs with the
    # tools that serve it, and csv_read/excel_read are files tools. Before the
    # move "csvyi oku" offered data_analyze and plot_data but not csv_read.
    ("csv dosyasını analiz et", "files"),
    ("csvyi oku", "files"),
    ("excelde ne var", "files"),
])
def test_patterns_moved_out_of_data_still_route(query, expected):
    assert classify_query(query).primary_domain == expected


def test_a_format_name_reaches_the_tool_that_opens_it():
    """The point of the move, stated as behaviour rather than as a domain
    label: naming a format must expose the reader for that format."""
    for query, tool in (("csvyi oku", "csv_read"),
                        ("excelde ne var", "excel_read"),
                        ("PDFteki tabloyu oku", "pdf_read")):
        selected = select_tool_names(classify_query(query), ALL_NAMES, query)
        assert tool in selected, f"{query!r} -> {selected}"


@pytest.mark.parametrize("query", [
    "hesabımdaki para akışını göster",
    "hesap hareketlerimi listele",
    "ekstremi getir",
    "nakit akışım nasıl",
    "Burgan bildirimlerini tara",
])
def test_finance_wordings_the_owner_actually_uses(query):
    """None of these matched any finance pattern before 2026-07-30."""
    route = classify_query(query)
    assert "finance" in select_tool_names(route, ALL_NAMES), (
        f"{query!r} routed to {route.domains} without exposing finance"
    )


@pytest.mark.parametrize("calc,account", [
    ("2 üzeri 10 hesapla", "hesabımdaki para"),
    ("faizi hesapla", "hesabımın ekstresi"),
])
def test_hesapla_and_hesab_do_not_collide(calc, account):
    """Turkish consonant alternation makes this a genuine trap: "hesapla"
    (calculate) and "hesabım" (my account) share a stem prefix. `\\bhesab` must
    not catch arithmetic, and `\\bhesapla` must not catch account requests."""
    assert classify_query(calc).primary_domain == "math"
    assert classify_query(account).primary_domain == "finance"
