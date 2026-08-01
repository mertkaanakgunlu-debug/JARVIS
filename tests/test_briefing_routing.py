"""Post-MVP Faz 3 — the briefing's wiring, not just its logic.

`jarvis/briefing.py` can be perfect and the feature still dead: if
"bugün neler var" routes to `conversation`, the model is handed zero tools and
answers from imagination -- which is precisely the failure this phase exists
to remove, arriving through the routing layer instead of the model.

So these tests go through the REAL entry point (`jarvis.agent._route_query`,
what every interface calls) rather than asserting on the router in isolation.
That is the MEMORY.md lesson written down: a guard with green tests may never
be on the path.

Two other things are asserted here rather than assumed:

  * `daily_briefing` must be L1. Faz 2.75's proactive clamp blocks any
    `risk_level >= 2` call on an unattended turn, so a scheduled morning
    briefing would be structurally blocked at L2 despite writing nothing.
  * `\\bhaber` must have MOVED from the "web" domain to "news", not been
    copied. A pattern in two domains scores its word twice and lets a
    single-intent request outrank a genuinely two-domain one.
"""
from __future__ import annotations

import pytest

from jarvis.agent import _route_query
from jarvis.config import Settings
from jarvis.graph.role_router import FAST, REASONING, for_unattended_turn
from jarvis.graph.tool_router import _DOMAIN_PATTERNS, select_tool_names
from jarvis.tool_registry import TOOL_SPECS, get_spec

BRIEFING_TOOLS = ("daily_briefing", "weather", "news")
ALL_TOOLS = sorted(TOOL_SPECS)


def _route(query: str):
    """Classify and pick a tier the way a real turn does."""
    return _route_query(query, needs_planning=False)


# ── The acceptance sentence reaches the tool ─────────────────────────────────

@pytest.mark.parametrize("query", [
    "bugün neler var",
    "JARVIS bugün neler var?",
    "Bugün ne var?",
    "günlük özet",
    "günün özeti nedir",
    "brifing ver",
    "briefing",
    "günüme bak",
    "günüm nasıl görünüyor",
    "what's on today?",
])
def test_a_briefing_request_is_offered_the_briefing_tool(query):
    route, _decision = _route(query)
    assert route.primary_domain == "briefing", route
    assert "daily_briefing" in select_tool_names(route, ALL_TOOLS, query)


@pytest.mark.parametrize("query", ["bugün neler var", "günlük özet", "brifing ver"])
def test_a_pure_briefing_request_is_one_domain_and_therefore_fast(query):
    """The plan's role table puts the briefing workflow on the fast tier, and
    it earns it the same way every other fast turn does: exactly one domain.
    A briefing that took the reasoning tier would pay back this phase's whole
    latency budget to rephrase a list that is already correct."""
    route, decision = _route(query)
    assert route.domains == ["briefing"]
    assert decision.role == FAST
    assert decision.reason == "single_domain_tool"


def test_the_briefing_offers_ONLY_the_briefing_tool():
    """One call, no chain. Faz 2.5 measured a two-dependent-call request at
    0/10 on both tiers; the briefing sidesteps that class entirely by having
    nothing to chain."""
    route, _decision = _route("bugün neler var")
    assert select_tool_names(route, ALL_TOOLS, "bugün neler var") == ["daily_briefing"]


# ── Patterns that must NOT fire ──────────────────────────────────────────────

@pytest.mark.parametrize("query,expected", [
    # \bbugün as a briefing pattern would make a phantom second domain out of
    # nearly every request that mentions today -- the failure Faz 2.75 removed
    # three generic verbs to fix.
    ("bugünkü takvimimi göster", "calendar"),
    ("bugün kaç mail geldi", "mail"),
    ("bugünün harcamalarını göster", "finance"),
    ("bugün eklediğim notları aç", "memory"),
])
def test_merely_saying_today_is_not_a_briefing_request(query, expected):
    route, _decision = _route(query)
    assert route.primary_domain == expected, route
    assert "briefing" not in route.domains


@pytest.mark.parametrize("query,expected", [
    # \bhava alone would catch both of these: "havalimanı" (airport) and
    # "havale" (bank transfer). Same substring class as the "ok" in "çok"
    # incident the tool router's header was written about.
    ("havalimanına giden yolu bul", None),
    ("hesabıma havale geldi mi", "finance"),
])
def test_words_that_merely_start_with_hava_are_not_weather(query, expected):
    route, _decision = _route(query)
    assert "weather" not in route.domains, route
    if expected:
        assert route.primary_domain == expected


def test_a_bare_greeting_does_not_fire_four_network_calls():
    """"Günaydın" is a greeting. Making it cost a calendar round-trip, a
    forecast and three RSS fetches would be a surprising five seconds for
    saying good morning; the plan's acceptance is the explicit request."""
    route, _decision = _route("Günaydın JARVIS")
    assert route.primary_domain == "conversation"


# ── Weather and news route to their own tools ────────────────────────────────

@pytest.mark.parametrize("query", [
    "hava durumu nasıl", "hava nasıl", "havalar nasıl olacak",
    "yarın yağmur yağacak mı", "sıcaklık kaç derece",
])
def test_weather_questions_reach_the_weather_tool(query):
    route, _decision = _route(query)
    assert "weather" in route.domains, route
    assert "weather" in select_tool_names(route, ALL_TOOLS, query)


def test_weather_takes_the_reasoning_tier_because_fast_invented_the_answer():
    """Pinned from a live A/B, not from taste. Same query, n=5 per arm,
    nothing else changed: `fast` called the tool 1/5 and the other four
    answered "sıcak ve güneşli, 32°C" out of nothing (it was 27.4°C and çok
    bulutlu); `reasoning` called it 5/5.

    Weather is a one-call lookup against a structured source -- the property
    every other member of _FAST_DOMAINS was chosen for -- so if that property
    were the whole story this assertion would read FAST. It does not, and the
    role router's comment explains what the real axis turned out to be."""
    _route_result, decision = _route("hava durumu nasıl")
    assert decision.role == REASONING
    assert decision.reason == "deliberative_domain"


@pytest.mark.parametrize("query", ["haberler ne durumda", "gündemde ne var", "son dakika neler oldu"])
def test_news_questions_reach_the_news_tool(query):
    route, _decision = _route(query)
    assert "news" in route.domains, route
    assert "news" in select_tool_names(route, ALL_TOOLS, query)


def test_haber_moved_out_of_web_rather_than_being_copied():
    """The table's own move-don't-copy rule. Left in both, the word would
    score twice and a one-intent request would outrank a real two-domain one."""
    web_patterns = "".join(_DOMAIN_PATTERNS["web"])
    assert "haber" not in web_patterns
    assert any("haber" in p for p in _DOMAIN_PATTERNS["news"])


def test_researching_the_news_is_genuinely_two_domains():
    """The flip side of the move: "haberleri araştır" names a feed read AND an
    open-ended search, and should get both."""
    route, decision = _route("haberleri internetten araştır")
    assert set(route.domains) >= {"news", "web"}
    assert decision.role == REASONING


# ── Registry wiring ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", BRIEFING_TOOLS)
def test_every_new_tool_is_fully_classified(name):
    """The registry has four import-time closure checks (domain, timeout
    class, idempotency, alpha status). This asserts the values are the
    intended ones, not merely present."""
    spec = get_spec(name)
    assert spec is not None
    assert spec.risk_level == 1
    assert spec.requires_confirmation is False
    assert spec.side_effect_type == "external_read"
    assert spec.idempotency == "natural"
    assert spec.description.strip()
    assert spec.domain in {"briefing", "weather", "news"}


@pytest.mark.parametrize("name", BRIEFING_TOOLS)
def test_the_briefing_tools_survive_the_proactive_readonly_clamp(name):
    """The Faz 3 enabler, asserted against the real gate rather than the
    registry. confirmation_node blocks any `risk_level >= 2` call on an
    unattended turn -- an L2 briefing would be structurally blocked from the
    scheduled morning run it exists for, even though it writes nothing."""
    from jarvis import policy_guard

    decision = policy_guard.evaluate(name, {}, Settings(), interactive=False)
    assert decision.allowed
    assert decision.risk_level < 2, "would be blocked as an unsupervised write"
    assert not decision.requires_confirmation


def test_an_unattended_briefing_still_takes_the_reasoning_tier():
    """`for_unattended_turn` overrides every fast decision, and the briefing
    is not exempt. Nobody is waiting on a scheduled 07:00 run, so its latency
    is not the thing worth optimizing -- and the clamp's value is that it has
    no exceptions to reason about."""
    _route_result, decision = _route("bugün neler var")
    assert decision.role == FAST
    assert for_unattended_turn(decision).role == REASONING


def test_the_tools_are_actually_exposed_to_the_model(tmp_path, monkeypatch):
    """make_tools() is the single chokepoint every consumer shares. A spec in
    the registry with no @tool behind it is a tool the model can never call."""
    monkeypatch.chdir(tmp_path)
    from jarvis.graph.tools import make_tools

    names = {t.name for t in make_tools(tmp_path, Settings(), None)}
    assert set(BRIEFING_TOOLS) <= names
