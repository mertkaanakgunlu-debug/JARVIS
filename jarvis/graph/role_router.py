"""Role router — which model tier a turn gets, decided in code (Post-MVP Faz 2.5).

Until now the rule was one line: conversation ⇒ ``fast``, anything else ⇒
``reasoning``. That made "bugünkü takvimimi göster" — one deterministic call
against one API — take the same tier as "şu üç kaynağı araştır ve karşılaştır".

What the two roles actually are on this machine matters for reading the rest of
this module. With ``cloud_policy="off"`` (the owner's config) BOTH roles resolve
to the same local qwen3:8b; the only difference is the thinking channel, which
``providers.get_llm`` disables for the fast role via ``local_reasoning_effort``
and leaves on for reasoning. So "pick a role" here means "decide whether qwen3
thinks first". With a cloud tier configured the same decision picks a bigger
model instead, which is why this is written in terms of roles, not effort.

**The safety direction is deliberate and asymmetric.** ``reasoning`` is the
default and ``fast`` must be *earned* by a positive, specific signal. A turn
this module is unsure about keeps exactly the treatment it gets today, so the
change can only ever remove reasoning where there is evidence it is not needed —
never add uncertainty to a turn that works. The failure modes are not
symmetrical: routing a hard turn to ``fast`` risks a wrong tool call, while
routing an easy turn to ``reasoning`` costs seconds.

Deterministic, no LLM call — same discipline as ``tool_router.classify_query``,
and for the same reason: a classifier that itself needs a model round-trip
cannot pay for a latency phase.

**One conditional cost to know before widening ``_FAST_DOMAINS``.** A ``/model``
pin (``switch_model`` → ``settings.pin_cloud_model``) applies to the *fast* role
only; ``reasoning`` ignores it. So every turn this module moves to ``fast`` is a
turn a pin can now send to a paid cloud model, where before it went to
``reasoning`` and stayed local. Structurally impossible on this owner's config —
``cloud_policy="off"`` refuses every cloud tier, pinned or not — but real under
``explicit`` or ``auto``, and worth weighing against the owner's "no cost for
now" before adding a domain here.

The decision carries a ``reason``. That is not decoration: Faz 2's most
expensive finding was a gate that passed 2235 unit tests and 40/40 mutations
while scoring the wrong input, and what made it visible was being able to ask a
live run *why* it decided what it decided. A role regression has the same shape
— the turn still works, it is just slow or sloppy — so the attribution has to
survive into the trace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis.graph.tool_router import ToolRoute
from jarvis.nlu.temporal import fold

FAST = "fast"
REASONING = "reasoning"

# Domains whose typical request is answered by ONE deterministic call against a
# structured source: look it up, list it, add it. The model supplies arguments;
# it does not have to plan.
#
# Every domain NOT listed here keeps reasoning, and the omissions are the
# argument:
#   web         — "araştır" has no defined stopping point; deciding when the
#                 answer is complete IS the reasoning
#   report/data — composition and analysis are multi-call by construction
#                 (read → aggregate → render)
#   system      — shell_run/python_run/generate_code, the three tools with no
#                 args schema at all (Faz 2 mutation round). Thinking is the
#                 only remaining check on what gets written
#   workflow    — literally "run this as several steps"
#   procedure   — persists something durable off one ambiguous sentence
#   mcp         — browser automation is a sequence, never one click
_FAST_DOMAINS = frozenset({
    "calendar", "tasks", "media", "memory", "files", "mail", "drive",
    "finance", "math",
    # Post-MVP Faz 3. The plan's own role table puts "brifing workflow'u" on
    # the fast side, and the briefing is the strongest case in this set: its
    # facts are gathered by DailyBriefingService before the model sees
    # anything, so there is no plan to make, no date to resolve and no tool
    # sequence to get right. What is left for the model is narration, which is
    # the one job a non-thinking tier is unambiguously suited to.
    #
    # It also matters more here than elsewhere. A briefing is the turn the
    # owner runs every morning, so its latency is the latency of the product.
    #
    # "weather" was in this set for exactly one afternoon and was MEASURED
    # OUT. Same query ("Hava durumu nasıl?"), n=5 per arm, nothing else
    # changed:
    #
    #     fast       1/5 called the tool -- the other 4 answered
    #                "sıcak ve güneşli, 32°C" out of thin air (it was 27.4°C
    #                and çok bulutlu)
    #     reasoning  5/5 called the tool, p50 16.2 s
    #
    # The lesson generalizes past this one domain, and it is not the axis
    # this set was originally reasoned along. What makes a domain safe for
    # the fast tier is not "one call against a structured source" -- news is
    # that too, and news scores 5/5 fast. It is whether the model believes it
    # ALREADY KNOWS the answer. Nothing in qwen3:8b's weights can produce
    # this user's calendar or today's headlines, and the model behaves
    # accordingly; a generic plausible weather report exists for every day of
    # the year, so without a thinking step it simply writes one.
    #
    # Read that before adding a domain here. The question to ask is not "is
    # this one tool call?" but "could the model fake this convincingly?"
    "briefing", "news",
})

# Explicit sequencing, not mere conjunction. Bare "ve" is excluded on purpose:
# "Baran ve Mehmet'i toplantıya ekle" is one step, and treating every "ve" as a
# step boundary would send most ordinary requests back to reasoning and undo
# the phase.
#
# Anchored with a trailing \b so "sonra" (then) does not swallow "sonraki"
# (next) — "sonraki toplantım ne zaman" is a single lookup, and this module
# reads the user's words, so a stem match here would be a self-inflicted
# slowdown on a common question.
#
# "en son" was here and was removed: in Turkish it is far more often the
# superlative "the latest" than the sequencer "lastly", and "en son mailimi
# göster" — one lookup — was reading as a two-step request. A sequencing word
# has to be one that cannot also be a modifier.
_SEQUENCING = re.compile(
    r"\bsonra\b|\bardindan\b|\bakabinde\b|\bpesinden\b"
    r"|\bthen\b|\bafter that\b|\bafterwards\b|\bfinally\b"
)

# Mail earns the fast list on its READS -- list/search/read are one structured
# call each. Sending is different in kind: the model has to author prose, and
# that prose leaves the machine. Every other fast domain returns or stores
# structured data; the domains that compose text (report, data) are already on
# the reasoning side, so this is the single crossing.
#
# Reasoned, not measured -- unlike the calendar and files entries, no live A/B
# backs this one. It is written in the direction that keeps today's behaviour,
# so being wrong costs latency on "mail gönder" and nothing else. The
# confirmation gate is untouched either way: a send still stops and asks.
_COMPOSES_PROSE = re.compile(
    r"\bgonder|\byanitla|\bcevap yaz|\btaslak|\bsend\b|\breply\b|\bdraft\b"
)

# A second imperative catches the same-domain chain the domain count cannot see:
# "takvimimi göster ve yarına toplantı ekle" routes to one domain but is two
# operations. Stems only, since Turkish imperatives take suffixes
# ("çizer misin", "listeleyip").
#
# "et" is absent by design. It is the auxiliary in "analiz et"/"kontrol et",
# so \bet would fire on nearly every phrasing of a single action and this rule
# would classify almost everything as multi-step.
#
# Two stems are narrowed against words this project says constantly:
#   ciz(?!gi) — "çizgi" is the noun "line", as in "çizgi grafiği". Without the
#               lookahead, "çizgi grafiğini kaydet" reads as two verbs.
#   indir\b   — "indirilenler" is the Downloads folder, named in every system
#               prompt's environment block. \bindir would match it and make
#               "indirilenlerdeki dosyayı listele" a two-step request.
_IMPERATIVES = re.compile(
    r"\bciz(?!gi)|\boku\b|\bokuy|\byaz\b|\byazip|\bekle|\blistele|\bgoster|\bgonder"
    r"|\bhesapla|\bdonustur|\bkaydet|\bindir\b|\bsil\b|\bguncelle|\bara\b|\barastir"
    r"|\bozetle|\banaliz|\bgrafikle|\bcevir|\btasi"
)


@dataclass(frozen=True)
class RoleDecision:
    """Which tier, and the rule that chose it."""
    role: str
    reason: str

    @property
    def use_pro_agent(self) -> bool:
        """The boolean the graph state has always carried."""
        return self.role == REASONING


def _imperative_count(folded: str) -> int:
    """Distinct imperative stems, not raw hits.

    Counting hits would score "grafiği çiz, sonra tekrar çiz" as two steps off
    one repeated verb; two DIFFERENT verbs is the thing that indicates two
    different operations.
    """
    return len({m.group(0) for m in _IMPERATIVES.finditer(folded)})


def select_role(
    query: str,
    route: ToolRoute | None,
    needs_planning: bool,
) -> RoleDecision:
    """Pick the model tier for one turn.

    ``needs_planning`` is the user's own ``/think`` prefix and always wins — an
    explicit request for deliberation is never overridden by a heuristic.

    A ``route`` of None means the caller had no classification at all (old
    checkpoints, background paths). That is the unsure case, so it takes the
    default: reasoning, exactly as today.
    """
    if needs_planning:
        return RoleDecision(REASONING, "explicit_think")

    if route is None:
        return RoleDecision(REASONING, "no_route")

    if route.primary_domain == "conversation":
        # Zero tools bound this turn; there is nothing to plan.
        return RoleDecision(FAST, "conversation")

    # More than one capability domain in one sentence is the plainest available
    # statement of "this is a chain": the MVP request ("maillerimi kontrol et,
    # hesabımdaki para akışını analiz et, excele dönüştür ve grafikle") lands
    # here on mail+finance+data.
    if len(route.domains) > 1:
        return RoleDecision(REASONING, "multi_domain")

    if route.primary_domain not in _FAST_DOMAINS:
        return RoleDecision(REASONING, "deliberative_domain")

    folded = fold(query or "")
    if route.primary_domain == "mail" and _COMPOSES_PROSE.search(folded):
        return RoleDecision(REASONING, "composes_prose")

    if _SEQUENCING.search(folded):
        return RoleDecision(REASONING, "sequenced_steps")

    if _imperative_count(folded) > 1:
        return RoleDecision(REASONING, "multiple_imperatives")

    return RoleDecision(FAST, "single_domain_tool")


def for_unattended_turn(decision: RoleDecision) -> RoleDecision:
    """Downgrade-proof a turn nobody is watching: never the fast tier.

    Applies to proactive checks (monitor.py) and background tasks
    (TaskExecutor). Two independent reasons, either one sufficient:

    Latency is not the complaint there. This phase exists because a user
    waiting on "bugünkü takvimimi göster" waits 30 seconds; a monitor poll on
    a 600-second rate limit and a job the user deliberately sent to the
    background have nobody waiting on them.

    And a proactive turn is the one path where the only thing standing between
    a misjudging model and an unwatched L2 write is a sentence in the system
    prompt — L3 is gated, L2 is not, and docs/SAFETY.md is explicit that this
    is mitigated rather than structurally closed. Whatever else is true of the
    non-thinking tier, "more reliably follows a negative instruction" is not a
    claim anyone has measured. Spending seconds nobody is counting to avoid
    testing that is not a trade.

    Net effect on behaviour: none. Every one of these turns already took the
    reasoning tier before Faz 2.5, so this keeps them exactly where they were
    and confines the change to attended turns.
    """
    if decision.role == REASONING:
        return decision
    return RoleDecision(REASONING, "unattended")
