"""Sprint 2 Faz 2A — deterministic capability router + turn-scoped binding.

The 16-scenario manual list (2026-07-16 round 2) is the acceptance target:
"doğru domain seçimi ≥ %90". The table test below runs those prompts (and
the review's multi-domain examples) through classify_query and pins the
expected routing. Also pins the substring-false-positive regression that
killed _is_trivially_simple ("ok" in "çok", "hi" in "tarihi").
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from jarvis.graph.tool_router import (
    MAX_TOOLS_PER_TURN,
    ToolRoute,
    classify_query,
    select_tool_names,
)
from jarvis.tool_registry import TOOL_SPECS

ALL_NAMES = list(TOOL_SPECS)


# ── classification table: manual-test prompts → expected primary domain ──────

@pytest.mark.parametrize("query,expected_primary", [
    ("merhaba", "conversation"),                                        # A1
    ("Benim en sevdiğim renk mavi, aklında tut", "conversation"),       # A2
    ("en sevdiğim renk neydi?", "conversation"),                        # A3
    ("Çalışma dizinindeki dosyaları listele", "files"),                 # B4
    ('jarvis_test.txt adında bir dosya oluştur ve içine "merhaba dünya" yaz', "files"),  # B5a
    ("jarvis_test.txt dosyasını oku", "files"),                         # B5b
    ("Şu sayılarla basit bir çizgi grafiği çiz: 1, 4, 9, 16", "data"),  # B6
    ("example.com sayfasında ne yazıyor?", "web"),                      # C7
    ("Python 3.13'ün çıkış tarihi ne? İnternette ara.", "web"),         # C8
    ("http://localhost:8132/status adresini url_read aracıyla oku", "web"),  # C9
    ("shell aracıyla dir komutunu çalıştır", "system"),                 # D10
    ("mertkaanakgunlu@gmail.com adresine konusu 'JARVIS testi' olan bir deneme maili gönder", "mail"),  # D12
    ("Bugün takvimimde ne var?", "calendar"),                           # E14
    ("Son 3 mailimi listele", "mail"),                                  # E15
    ("Şu prosedürü kaydet: önce su haznesini doldur, sonra filtreyi tak", "procedure"),  # F16
])
def test_manual_round_prompts_route_correctly(query, expected_primary):
    assert classify_query(query).primary_domain == expected_primary


def test_multi_domain_task_unions_domains():
    route = classify_query("Bu PDF içindeki toplantıları takvimime ekle")
    assert route.primary_domain == "calendar"
    assert "files" in route.domains
    names = select_tool_names(route, ALL_NAMES)
    assert "google_calendar" in names
    assert "pdf_read" in names
    assert len(names) <= MAX_TOOLS_PER_TURN


def test_substring_false_positives_are_dead():
    # the exact morphology traps that killed _is_trivially_simple
    for q in ("çok güzel oldu tamam", "bunun tarihi önemli mi", "heyecanlıyım"):
        assert classify_query(q).primary_domain == "conversation", q


# ── selection rules ───────────────────────────────────────────────────────────

def test_conversation_selects_zero_tools():
    route = classify_query("merhaba")
    assert select_tool_names(route, ALL_NAMES) == []


def test_missing_route_falls_back_to_full_set():
    # background/proactive paths and old checkpoints carry no route
    assert select_tool_names(None, ALL_NAMES) == ALL_NAMES
    assert select_tool_names(ToolRoute.from_dict(None), ALL_NAMES) == ALL_NAMES


def test_subset_never_exceeds_cap():
    for q in (
        "dosyadaki veriyi analiz edip grafik çiz ve rapor yaz",
        "mailleri oku, takvime toplantı ekle, drive'a yükle",
        "PDF dosyasındaki tabloyu excel'e çevirip hesapla",
    ):
        names = select_tool_names(classify_query(q), ALL_NAMES)
        assert 0 < len(names) <= MAX_TOOLS_PER_TURN, (q, names)


def test_procedure_save_requires_explicit_intent():
    explicit = classify_query("Şu prosedürü kaydet: adım adım kahve yap")
    assert "procedure_save" in select_tool_names(explicit, ALL_NAMES)

    # same domain forced WITHOUT explicit wording → tool stays hidden
    implicit = ToolRoute("procedure", ["procedure"], 1.0, explicit_tool_intent=False)
    assert select_tool_names(implicit, ALL_NAMES) == []


def test_mcp_tools_are_quarantined():
    available = ALL_NAMES + ["browser_click"]  # dynamic tool, not in TOOL_SPECS
    # a files turn must never expose it
    files_route = classify_query("dosyaları listele")
    assert "browser_click" not in select_tool_names(files_route, available)
    # explicit browser wording routes to the mcp domain and exposes it
    mcp_route = classify_query("tarayıcıda siteyi aç ve butona tıkla")
    assert mcp_route.primary_domain == "mcp"
    assert "browser_click" in select_tool_names(mcp_route, available)


def test_route_dict_roundtrip():
    route = classify_query("Son 3 mailimi listele")
    assert ToolRoute.from_dict(route.to_dict()) == route


# ── make_agent_node binds the scoped subset ───────────────────────────────────

class _RecordingLLM:
    def __init__(self, subset_names):
        self.subset_names = subset_names

    async def ainvoke(self, messages):
        return AIMessage(content=f"bound:{len(self.subset_names)}")


@pytest.mark.asyncio
async def test_agent_node_binds_route_subset(monkeypatch):
    from langchain_core.tools import tool as lc_tool
    import jarvis.providers as providers
    from jarvis.config import Settings

    made: list[tuple[str, list[str]]] = []

    def fake_get_llm(role, settings, *, tools=None, max_output_tokens=None):
        names = [t.name for t in (tools or [])]
        made.append((role, names))
        return _RecordingLLM(names)

    monkeypatch.setattr(providers, "get_llm", fake_get_llm)

    @lc_tool
    def gmail(q: str) -> str:
        """fake gmail"""
        return "x"

    @lc_tool
    def file_read(path: str) -> str:
        """fake file_read"""
        return "x"

    from jarvis.graph.nodes import make_agent_node
    node = make_agent_node([gmail, file_read], Settings(_env_file=None))

    # pure-mail route → only gmail bound (E15's "listele" wording legitimately
    # unions mail+files; with just 2 tools available both would fit the cap,
    # so pin the single-domain case with unambiguous wording instead)
    mail_route = classify_query("gmail gelen kutusuna bak").to_dict()
    out = await node({"messages": [], "tool_route": mail_route, "use_pro_agent": False})
    assert out["messages"][0].content == "bound:1"
    assert made[-1] == ("fast", ["gmail"])

    # conversation route → bare model (no tools)
    conv_route = classify_query("merhaba").to_dict()
    out = await node({"messages": [], "tool_route": conv_route, "use_pro_agent": False})
    assert out["messages"][0].content == "bound:0"
    assert made[-1] == ("fast", [])

    # no route → legacy full set
    out = await node({"messages": [], "use_pro_agent": True})
    assert out["messages"][0].content == "bound:2"
    assert made[-1] == ("reasoning", ["gmail", "file_read"])

    # cache: same (role, subset) never composes twice
    n_before = len(made)
    await node({"messages": [], "tool_route": mail_route, "use_pro_agent": False})
    assert len(made) == n_before
