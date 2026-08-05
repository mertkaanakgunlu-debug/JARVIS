"""Which turns carry a completion contract — decided from the user's words.

The resolver runs at the entry point, before the graph, and its output is a
promise the system then holds itself to. So the FALSE rows matter more than
the true ones: a false positive makes JARVIS produce a file nobody asked for
and, worse, hands a repair round to a model that was already answering
correctly. When in doubt this returns nothing.
"""
from __future__ import annotations

import pytest

from jarvis.nlu.output_intent import required_outputs_for

CHART = [{"kind": "chart", "operation": "create"}]


def _is_chart_creation(requirements: list[dict]) -> bool:
    """kind/operation only -- deliberately blind to whether a `source` key
    is also present, since Source Binding (Pr_2) adds one whenever the query
    names an explicit file. Tests that care about the exact shape, source
    included or not, check it directly instead of through this helper."""
    return (
        len(requirements) == 1
        and requirements[0].get("kind") == "chart"
        and requirements[0].get("operation") == "create"
    )


@pytest.mark.parametrize("query", [
    # the measured case: the 2x2 experiment's own target query
    "Masaüstündeki satis.csv dosyasının aylık satış grafiğini çiz",
    "satis.csv'nin grafiğini çiz",
    "bu dosyanın grafiğini oluştur",
    "verilerden bir grafik hazırla",
    "satis.csv'yi grafik olarak göster",
    "excel dosyasındaki satışların grafiğini çıkar",
    "tablodaki değerleri görselleştir",
    "plot the sales column from satis.csv",
    "draw a chart of the data",
    "create a plot from this csv file",
])
def test_an_explicit_chart_request_carries_a_creation_contract(query):
    """Some of these queries also name an explicit file and therefore carry a
    `source` binding (see the "Source Binding" section below) -- this test's
    own concern is only "is a chart/create requirement present at all",
    which _is_chart_creation checks without being coupled to that."""
    assert _is_chart_creation(required_outputs_for(query))


@pytest.mark.parametrize("query", [
    # analysis is not a chart, however data-heavy
    "satis.csv'yi analiz et",
    "veriyi özetle",
    "satışları incele",
    "dosyadaki en yüksek satışı söyle",
    "bu csv'de kaç satır var?",
    "summarise the data in satis.csv",
    "what is the highest value in this file?",
    # no data anywhere in sight
    "merhaba",
    "bugün hava nasıl?",
    "",
    "   ",
])
def test_a_request_without_an_explicit_chart_carries_none(query):
    assert required_outputs_for(query) == []


@pytest.mark.parametrize("query", [
    "grafik çizebiliyor musun?",
    "grafik oluşturma özelliğin var mı?",
    "chart çizme yeteneğin var mı?",
    "can you draw charts?",
    "do you support plotting?",
])
def test_a_question_about_the_capability_is_not_a_request(query):
    """A capability question has no data to draw from -- which is exactly what
    separates it from a polite request, so nothing here keys on the question
    mark or the mood."""
    assert required_outputs_for(query) == []


@pytest.mark.parametrize("query", [
    "bu dosyanın grafiğini çizebilir misin?",
    "satis.csv'yi grafik olarak gösterebilir misin?",
    "verinin grafiğini çizer misin acaba",
    "could you plot the csv for me?",
])
def test_a_polite_request_is_still_a_request(query):
    """Turkish `-ebilir misin` is far more often a courteous imperative than a
    question about ability. Treating the form as a capability question would
    silently drop the contract from the politest phrasings -- and those are
    how the owner actually writes."""
    assert _is_chart_creation(required_outputs_for(query))


@pytest.mark.parametrize("query", [
    "grafik istemiyorum, sadece sayıları ver",
    "csv'yi oku ama grafik çizme",
    "dosyanın grafiğini silme",
    "no chart please, just the numbers",
])
def test_a_refusal_of_a_chart_never_creates_a_contract(query):
    assert required_outputs_for(query) == []


@pytest.mark.parametrize("query", [
    "bu mimariyi görselleştir",
    "sistem akışını görselleştirir misin",
    "visualise the architecture",
])
def test_visualising_something_that_is_not_data_is_not_a_chart(query):
    """"görselleştir" on its own covers diagrams too. Requiring a data
    reference alongside it is what keeps an architecture sketch from becoming
    a promise to produce a matplotlib PNG."""
    assert required_outputs_for(query) == []


@pytest.mark.parametrize("query", [
    "grafiği kırmızı yap",
    "başlığı '2026 Satışları' yap",
    "sütun grafiği olsun",
    "make the chart red",
])
def test_a_revision_request_is_out_of_this_phases_scope(query):
    """Revisions have their own postcondition (an existing object changes,
    nothing new is created) and their own failure modes. The contract this
    phase enforces is creation-only, so the resolver must not promise one
    here -- a `revise` requirement is a later, separate decision."""
    assert required_outputs_for(query) == []


def test_the_requirement_shape_is_operation_aware():
    """Not a bare ["chart"]: plot_data and chart_revise both produce a chart
    and are not interchangeable, so the requirement has to say which.

    Deliberately a SOURCE-FREE query -- this test's only concern is the
    kind/operation shape; the "Source Binding" section below covers the
    `source` sub-dict a named-file query additionally carries.
    """
    [requirement] = required_outputs_for("verilerden bir grafik hazırla")
    assert requirement == {"kind": "chart", "operation": "create"}


def test_the_result_is_a_fresh_list_each_call():
    """It goes straight into graph state and through a checkpointer; a shared
    module-level list would be one accidental mutation away from leaking
    between turns. Source-free query -- see the fresh-list check for a named
    file below, since that requirement also carries a nested `source` dict
    that must not alias across calls either."""
    first = required_outputs_for("verilerden bir grafik hazırla")
    first.append({"kind": "report", "operation": "create"})
    assert required_outputs_for("verilerden bir grafik hazırla") == CHART


@pytest.mark.parametrize("query", [None, 42, ["grafik çiz"]])
def test_a_non_string_query_is_not_a_contract(query):
    assert required_outputs_for(query) == []


# ── Source Binding (Pr_2): the `source` sub-dict a named file adds ─────────

@pytest.mark.parametrize("query,expected_basename", [
    ("satis.csv'nin grafiğini çiz", "satis.csv"),
    ("Masaüstündeki satis.csv dosyasının aylık satış grafiğini çiz", "satis.csv"),
    ("satis.csv'yi grafik olarak göster", "satis.csv"),
    ("plot the sales column from satis.csv", "satis.csv"),
    ("satis.csv'yi grafik olarak gösterebilir misin?", "satis.csv"),
])
def test_a_named_file_request_carries_a_source_binding(query, expected_basename):
    [requirement] = required_outputs_for(query)
    assert requirement["kind"] == "chart" and requirement["operation"] == "create"
    assert requirement["source"] == {
        "type": "file", "raw": expected_basename, "basename": expected_basename,
        "is_explicit_path": False,
    }


@pytest.mark.parametrize("query", [
    "bu dosyanın grafiğini oluştur",
    "verilerden bir grafik hazırla",
    "excel dosyasındaki satışların grafiğini çıkar",
    "tablodaki değerleri görselleştir",
    "draw a chart of the data",
    "create a plot from this csv file",
    "bu dosyanın grafiğini çizebilir misin?",
    "verinin grafiğini çizer misin acaba",
])
def test_a_generic_reference_request_carries_no_source_binding(query):
    """Section 1's rule: 'bu dosya' / 'veriler' / 'tablo' must not fabricate a
    source. Exact equality with CHART (not just kind/operation) is the point
    here -- a stray `source` key appearing would be exactly the bug this
    pins against."""
    assert required_outputs_for(query) == CHART


def test_an_absolute_path_in_the_query_is_preserved_in_the_requirement():
    query = r"C:\Users\mertk\Desktop\satis.csv dosyasının grafiğini çiz"
    [requirement] = required_outputs_for(query)
    source = requirement["source"]
    assert source["basename"] == "satis.csv"
    assert source["raw"] == r"C:\Users\mertk\Desktop\satis.csv"
    assert source.get("path"), "an already-absolute path needs no workspace to canonicalize"


def test_the_source_dict_is_a_fresh_object_each_call():
    """Same mutation-isolation concern as the requirement list itself, one
    level deeper: the nested `source` dict must not alias across turns."""
    first = required_outputs_for("satis.csv'nin grafiğini çiz")
    first[0]["source"]["basename"] = "corrupted.csv"
    second = required_outputs_for("satis.csv'nin grafiğini çiz")
    assert second[0]["source"]["basename"] == "satis.csv"
