"""Post-MVP Faz 2.5 — the role router's contract, and proof it is on the path.

Two regimes, per the plan's test strategy. Everything here is regime A
(deterministic, runs once): the classifier takes text and returns a tier, so
repeating it n times measures nothing. Whether qwen3:8b actually still picks
the right tool without its thinking channel is regime B and lives in the live
A/B measurement, not here.

The last group is the one that matters most. Faz 2 spent a whole phase on a
gate that passed 2235 unit tests and 40/40 mutation rounds while scoring the
wrong input, and a role router fails the same silent way: the turn still
answers, it is just slower or sloppier than intended. So these do not stop at
"select_role returned fast" -- they follow the decision through _route_query
and the agent node and assert on the role string that actually reaches
providers.get_llm.
"""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import jarvis.providers as providers
from jarvis.agent import _route_query
from jarvis.config import Settings
from jarvis.graph.nodes import make_agent_node
from jarvis.graph.role_router import (
    FAST,
    REASONING,
    RoleDecision,
    select_role,
)
from jarvis.graph.tool_router import ToolRoute, classify_query


def _decide(query: str, needs_planning: bool = False) -> RoleDecision:
    """Classify for real, then decide — never a hand-built route.

    A hand-built ToolRoute would let this file agree with itself while
    disagreeing with what classify_query actually produces for the sentence.
    """
    return select_role(query, classify_query(query), needs_planning)


# ── fast: the tier has to be earned ───────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "Merhaba, bugün nasılsın?",
    "Teşekkürler, harikasın",
    "Adın ne?",
])
def test_conversation_is_fast(query):
    d = _decide(query)
    assert d.role == FAST
    assert d.reason == "conversation"


@pytest.mark.parametrize("query,domain", [
    ("Bugünkü takvimimi göster", "calendar"),
    ("Masaüstündeki dosyaları listele", "files"),
    ("Yarın saat 15:00'te Baran'la toplantı ekle", "calendar"),
    ("Yapılacaklar listeme süt almayı ekle", "tasks"),
])
def test_single_deterministic_tool_is_fast(query, domain):
    route = classify_query(query)
    assert route.primary_domain == domain, "premise: this query routes where the test claims"
    d = select_role(query, route, False)
    assert d.role == FAST
    assert d.reason == "single_domain_tool"


def test_next_meeting_is_not_read_as_a_sequence():
    """"sonraki" (next) must not match the "sonra" (then) sequencing rule.

    A stem-anchored \\bsonra would fire here, and "sonraki toplantım ne zaman"
    is one of the most ordinary questions this assistant gets -- it would have
    silently paid the reasoning tier forever.
    """
    d = _decide("Sonraki toplantım ne zaman?")
    assert d.role == FAST


# ── reasoning: the default, and every road back to it ─────────────────────────

def test_think_prefix_always_wins():
    """An explicit request to deliberate is never overridden by a heuristic —
    including on a query the router would otherwise call trivial."""
    d = _decide("Merhaba", needs_planning=True)
    assert d.role == REASONING
    assert d.reason == "explicit_think"


def test_no_route_falls_back_to_reasoning():
    """Unsure ⇒ today's behaviour, not a guess."""
    d = select_role("herhangi bir şey", None, False)
    assert d.role == REASONING
    assert d.reason == "no_route"


def test_multi_domain_chain_is_reasoning():
    query = ("Maillerimi kontrol et, hesabımdaki para akışını analiz et, "
             "bir excel tablosuna dönüştür ve grafikle")
    route = classify_query(query)
    assert len(route.domains) > 1, "premise: the MVP chain spans domains"
    d = select_role(query, route, False)
    assert d.role == REASONING
    assert d.reason == "multi_domain"


@pytest.mark.parametrize("query,domain", [
    ("Yapay zeka ajanları hakkında araştırma yap", "web"),
    ("Bu konuda bir rapor yaz", "report"),
    ("Şu komutu terminalde çalıştır", "system"),
    ("Tarayıcıyı aç", "mcp"),
])
def test_deliberative_domains_stay_reasoning(query, domain):
    route = classify_query(query)
    assert route.primary_domain == domain, "premise: this query routes where the test claims"
    d = select_role(query, route, False)
    assert d.role == REASONING
    assert d.reason == "deliberative_domain"


@pytest.mark.parametrize("query", [
    "Takvimimi göster, sonra yarına bir toplantı ekle",
    "Dosyayı oku, ardından bana özetini ver",
    "List my files, then delete the old ones",
])
def test_explicit_sequencing_is_reasoning(query):
    d = _decide(query)
    assert d.role == REASONING
    assert d.reason == "sequenced_steps"


@pytest.mark.parametrize("query", [
    "Baran'a bir mail gönder",
    "Bu maile cevap yaz",
    "Son mesaja bir taslak hazırla",
])
def test_sending_mail_composes_prose_and_stays_reasoning(query):
    """Reading mail is a lookup; sending it is authorship that leaves the
    machine. The domain is on the fast list for the first, not the second."""
    d = _decide(query)
    assert d.role == REASONING
    assert d.reason == "composes_prose"


def test_reading_mail_is_still_fast():
    """The rule above must not swallow the reads that put mail on the list."""
    d = _decide("Baran'dan mail geldi mi")
    assert d.role == FAST
    assert d.reason == "single_domain_tool"


def test_a_generic_verb_can_split_one_request_into_two_domains():
    """A known cost of the multi_domain rule, pinned rather than hidden.

    "listele" is a *files* pattern in tool_router, so "Son 3 mailimi listele"
    scores mail=1, files=1 and reads as a two-domain chain -- one call's worth
    of work paying the reasoning tier. A survey of 16 realistic single-call
    requests (2026-08-01) hit this 4 times: "listele" and "ara" are the verbs,
    files/web/drive the phantom domains.

    Not fixed here, and the reason matters. The available refinement -- let a
    multi-domain route stay fast when every domain is a fast one -- also lets
    "PDF'teki toplantıları takvime ekle" through, and that one genuinely is two
    dependent calls. One imperative verb cannot tell those apart, so the honest
    fix is in tool_router (a generic verb owned by a specific domain), not in a
    second heuristic layered on top of it.

    Nothing regresses meanwhile: these turns take the reasoning tier today too.
    When tool_router is fixed, this test goes red and points at the right file.
    """
    route = classify_query("Son 3 mailimi listele")
    assert route.domains == ["mail", "files"]
    assert select_role("Son 3 mailimi listele", route, False).reason == "multi_domain"


@pytest.mark.parametrize("query", [
    "En son mailimi göster",
    "En son toplantım neydi",
])
def test_superlative_en_son_is_not_a_sequencing_word(query):
    """"en son" is "the latest" far more often than it is "lastly" — as a
    sequencer it read one lookup as two steps."""
    assert _decide(query).reason != "sequenced_steps"


def test_two_distinct_imperatives_in_one_domain_is_reasoning():
    """The same-domain chain the domain count cannot see."""
    d = _decide("Yarınki toplantıyı takvime ekle ve bana göster")
    assert d.role == REASONING
    assert d.reason == "multiple_imperatives"


@pytest.mark.parametrize("query,collision", [
    ("İndirilenlerdeki dosyayı listele", "indirilenler = the Downloads folder, not the verb indir"),
    ("Çizgi grafiğini masaüstüne kaydet", "çizgi = the noun 'line', not the verb çiz"),
])
def test_imperative_stems_do_not_match_common_nouns(query, collision):
    """Both words are ones this project says constantly — "indirilenler" is
    named in every system prompt's environment block, and "çizgi grafiği" is
    the plan's own wording. A bare stem match on either turns a one-step
    request into a two-step one and silently gives back the phase's win."""
    from jarvis.graph.role_router import _imperative_count
    from jarvis.nlu.temporal import fold

    assert _imperative_count(fold(query)) == 1, collision


def test_repeated_same_imperative_is_still_one_step():
    """Counting hits instead of distinct stems would call this two steps."""
    route = classify_query("Bu dosyaları listele, tüm dosyaları listele")
    d = select_role("Bu dosyaları listele, tüm dosyaları listele", route, False)
    assert d.role == FAST


def test_bare_ve_is_not_a_step_boundary():
    """"Baran ve Mehmet" is one event, not two operations. Treating every
    conjunction as a sequence would send most ordinary requests back to
    reasoning and undo the phase."""
    d = _decide("Yarın 15:00'te Baran ve Mehmet'le toplantı ekle")
    assert d.role == FAST


# ── the fast tier is for attended turns only ──────────────────────────────────

def test_a_proactive_prompt_would_otherwise_have_gone_fast():
    """The premise for the next test, asserted rather than assumed.

    monitor.py's real calendar prompt routes to a single fast domain, so
    without the unattended pin Faz 2.5 would have quietly moved background
    self-checks onto the non-thinking tier.
    """
    prompt = 'Takvimde yaklaşan bir etkinlik var: "Proje toplantısı".'
    assert select_role(prompt, classify_query(prompt), False).role == FAST


def test_unattended_turns_never_take_the_fast_tier():
    from jarvis.graph.role_router import for_unattended_turn

    pinned = for_unattended_turn(RoleDecision(FAST, "single_domain_tool"))
    assert pinned.role == REASONING
    assert pinned.reason == "unattended"


def test_unattended_leaves_an_already_deliberate_reason_intact():
    """Overwriting the reason would lose why the turn was hard in the first
    place — the attribution is the point of carrying one."""
    from jarvis.graph.role_router import for_unattended_turn

    original = RoleDecision(REASONING, "multi_domain")
    assert for_unattended_turn(original).reason == "multi_domain"


@pytest.mark.parametrize("method", ["proactive_turn", "background_turn"])
def test_both_unattended_entry_points_apply_the_pin(method):
    """Source-level, because driving either path end-to-end needs a live model.

    A guard is only worth its tests if it is actually on the path -- and both
    of these call _route_query directly, so a future entry point that forgets
    the pin is exactly the regression this catches.
    """
    import inspect

    from jarvis.agent import JarvisAgent

    src = inspect.getsource(getattr(JarvisAgent, method))
    assert "for_unattended_turn(role_decision)" in src, (
        f"{method} routes a turn nobody is watching without pinning the tier"
    )


# ── the boolean the graph has always carried ──────────────────────────────────

def test_use_pro_agent_maps_to_the_reasoning_role():
    assert RoleDecision(REASONING, "x").use_pro_agent is True
    assert RoleDecision(FAST, "x").use_pro_agent is False


# ── wiring: is the decision actually on the path? ─────────────────────────────

class _StubLLM:
    async def ainvoke(self, messages, **kwargs):
        return AIMessage(content="ok")


def _role_reaching_get_llm(monkeypatch, query: str) -> str:
    """Drive query → _route_query → state → agent_node → providers.get_llm.

    The assertion target is the role string get_llm is actually CALLED with,
    not select_role's return value: every link in that chain is a place the
    decision could be dropped, and a test that stops at the classifier would
    stay green through all of them.
    """
    seen: list[str] = []

    def spy(role, settings, *, tools=None, max_output_tokens=None):
        seen.append(role)
        return _StubLLM()

    monkeypatch.setattr(providers, "get_llm", spy)

    route, decision = _route_query(query, False)
    node = make_agent_node(tools=[], settings=Settings(_env_file=None))
    state = {
        "messages": [HumanMessage(content=query)],
        "use_pro_agent": decision.use_pro_agent,
        "tool_route": route.to_dict(),
    }
    asyncio.run(node(state))
    assert seen, "agent node never resolved a model"
    return seen[0]


def test_single_tool_query_reaches_the_model_as_fast(monkeypatch):
    assert _role_reaching_get_llm(monkeypatch, "Bugünkü takvimimi göster") == FAST


def test_multi_domain_query_reaches_the_model_as_reasoning(monkeypatch):
    query = ("Maillerimi kontrol et, hesabımdaki para akışını analiz et, "
             "bir excel tablosuna dönüştür ve grafikle")
    assert _role_reaching_get_llm(monkeypatch, query) == REASONING


def test_route_query_returns_a_decision_not_a_bare_bool():
    """The four entry points read .role/.reason off this; a bool would still
    satisfy `if use_pro_agent:` and lose the attribution silently."""
    route, decision = _route_query("Bugünkü takvimimi göster", False)
    assert isinstance(route, ToolRoute)
    assert isinstance(decision, RoleDecision)
    assert decision.role == FAST
    assert decision.reason


def test_decision_reason_reaches_the_hud_frame():
    """The backend half of the HUD contract.

    The renderer change that shows this is compile-verified only (electron has
    no component-test harness), so the frame's shape is where a real assertion
    can live: if the key stops being emitted, the HUD silently renders nothing
    rather than failing.
    """
    from jarvis.ws import JarvisEventBus

    bus = JarvisEventBus()
    sent: list[dict] = []
    bus.emit = sent.append

    bus.model_status({"requested_role": FAST, "role_reason": "single_domain_tool",
                      "provider": "ollama", "model": "qwen3:8b"})

    assert sent and sent[0]["type"] == "model_status"
    assert sent[0]["role"] == FAST
    assert sent[0]["role_reason"] == "single_domain_tool"


def test_hud_frame_reports_no_reason_rather_than_a_wrong_one():
    """Faz 0B's invariant: a field with no value says so. A turn that ran
    before this existed has no reason, and the frame must carry null rather
    than a plausible-looking default."""
    from jarvis.ws import JarvisEventBus

    bus = JarvisEventBus()
    sent: list[dict] = []
    bus.emit = sent.append

    bus.model_status(None)
    assert sent[0]["role_reason"] is None


def test_decision_reason_reaches_the_turn_trace():
    """role_reason has to survive into last_turn_trace — that is what makes a
    slow turn attributable to a rule instead of guessed at."""
    from jarvis.llm_trace import LlmTraceRecorder

    rec = LlmTraceRecorder(requested_role=FAST, role_reason="single_domain_tool")
    rec.traces.append(_ok_trace())
    summary = rec.turn_summary()
    assert summary["requested_role"] == FAST
    assert summary["role_reason"] == "single_domain_tool"


def _ok_trace():
    from jarvis.llm_trace import LlmCallTrace

    return LlmCallTrace(
        provider="local", model="qwen3:8b", billable=False, billing="free",
        tier_index=0, node="agent", ok=True, input_tokens=10, output_tokens=5,
        latency_ms=100.0,
    )


# ── the one place a fast turn must NOT stay fast ──────────────────────────────

class _ScriptedLLM:
    def __init__(self, reply: str):
        self._reply = reply

    async def ainvoke(self, messages, **kwargs):
        return AIMessage(content=self._reply)


async def test_unbacked_claim_repair_escalates_even_on_a_fast_turn(monkeypatch):
    """The turn ran fast; its repair must not.

    The repair exists because the answer was provably contradicted. Retrying on
    the tier that just produced it spends the one allowed round on the same
    conditions -- and the plan's role table puts "critical error repair" on the
    reasoning side for exactly this reason. Faz 2.5 made most tool turns fast,
    which is what turned an inherited role here from a no-op into a real one.
    """
    from jarvis.graph.nodes import make_verification_node

    roles: list[str] = []

    def spy(role, settings=None, **kwargs):
        roles.append(role)
        return _ScriptedLLM("Grafiği çizemedim efendim.")

    monkeypatch.setattr(providers, "get_llm", spy)

    node = make_verification_node(
        settings=Settings(_env_file=None, execution_contract_mode="enforce_reversible")
    )
    await node({
        "messages": [
            HumanMessage(content="grafiği çiz ve kaydet"),
            AIMessage(content="Grafiği oluşturdum ve C:/nope/uydurma.png konumuna kaydettim."),
        ],
        "response": "Grafiği oluşturdum ve C:/nope/uydurma.png konumuna kaydettim.",
        "use_pro_agent": False,          # a fast turn
        "execution_envelopes": [],
    })

    assert roles == [REASONING], f"repair ran on {roles}, not the reasoning tier"
