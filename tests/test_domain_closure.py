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
