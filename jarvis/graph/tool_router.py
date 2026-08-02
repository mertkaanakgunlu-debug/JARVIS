"""Capability router — deterministic, turn-scoped tool selection (Sprint 2, Faz 2A).

Why: the live manual rounds (2026-07-16) showed qwen2.5:7b handling tool
calls fine with a handful of tools + the real system prompt (isolated
measurement 4/4) and collapsing under all ~36 schemas at once (raw-JSON-as-
text, echoing a previous answer, drifting into Chinese). The router shrinks
what the model SEES per turn; it does not touch what can EXECUTE — policy
guard, kill switch, external-write gate and the Faz 1B limits all sit after
it and hold even if routing misfires.

Deterministic by design: a word-boundary TR+EN pattern table, no LLM call.
Patterns are stem-anchored (``\\btakvim`` covers takvime/takvimimde) because
Turkish suffixes attach at the end; bare two-letter probes are banned — the
``"ok" in "çok"`` / ``"hi" in "tarihi"`` substring incident is why
``_is_trivially_simple`` (which this module retires) had to die.

Routing rules (external review, GPT-5.6 2026-07-17):
  * conversation → zero tools (bare model; short-term recall lives in
    history, durable facts in the extractor pipeline — neither needs a tool)
  * one clear domain → that domain's subset
  * multi-domain task ("PDF'teki toplantıları takvime ekle") → union of at
    most MAX_DOMAINS_PER_TURN domains, MAX_TOOLS_PER_TURN tools total
  * ambiguous → NEVER the full set; zero tools and the model asks/answers
  * procedure_save only on explicit intent ("prosedürü kaydet", not vibes)
  * MCP tools are quarantined in their own 'mcp' domain — nothing exposes
    them without explicit browser/automation wording
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_TOOLS_PER_TURN = 8
MAX_DOMAINS_PER_TURN = 3

# Ambiguity guard: more specific/external domains outrank generic local ones
# on equal keyword-hit scores ("Son 3 mailimi listele" is mail, even though
# "listele" also smells like the filesystem).
_DOMAIN_PRIORITY = [
    "procedure", "workflow", "mail", "calendar", "briefing", "drive", "media",
    "finance", "tasks", "weather", "news", "web", "mcp", "files", "data",
    "report", "math", "system", "memory",
]

# "briefing" sits BELOW "calendar" deliberately. Both fire on "takvimimde
# bugün neler var" (calendar on \btakvim, briefing on "bugün neler var"), and
# on that tie the user named the calendar explicitly -- so the calendar owns
# the turn and the briefing rides along as the second domain. The pure request
# ("bugün neler var", nothing else) scores briefing alone, which is what makes
# it a single-domain turn and therefore a `fast` one.

# Domains that are opt-in via explicit wording only, never picked up from
# general vibes — same reasoning as procedure_save originally: a capability
# consequential enough (persisting a procedure; here, actually KICKING OFF a
# multi-step workflow that can execute real side effects) that an ambiguous
# keyword hit must not be enough to expose it.
_EXPLICIT_ONLY_DOMAINS = frozenset({"procedure", "workflow"})

_DOMAIN_PATTERNS: dict[str, list[str]] = {
    # Post-MVP Faz 2.75 (Paket F) removed three GENERIC VERBS from this table:
    # \boku\b and \blistele from here, \bara\b from "web". They named an
    # operation, not a capability, so any request that used one picked up a
    # phantom second domain -- "Son 3 mailimi listele" scored mail=1, files=1
    # and read as a two-domain chain. A survey of 16 realistic single-call
    # requests hit that 4 times, and the cost is not only the wasted slots:
    # role_router sends a multi-domain route to the slower reasoning tier.
    # Nothing is lost, because the NOUN is still there in every real request
    # ("dosyaları listele" matches \bdosya, "notlarımı ara" matches \bnot).
    #
    # \bpdf\b became \bpdf for a bug the word boundary caused: Turkish attaches
    # suffixes straight onto the acronym, so "PDFteki toplantıları takvime
    # ekle" matched nothing here and the model was handed NO tool that can read
    # a PDF. ("PDF'teki" matched only because the apostrophe is a boundary.)
    "files": [
        r"\bdosya", r"\bklas[öo]r", r"\bdizin", r"\bfolder\b", r"\bfile",
        r"\bdirectory\b", r"\bmasaüstü", r"\bdesktop\b",
        r"\.(txt|md|pdf|csv|xlsx?|docx?|json|py|log)\b",
        # File-FORMAT names live with the tools that open them (pdf_read,
        # csv_read, excel_read are all "files"), per this table's own rule that
        # a pattern moves with its tool. \bcsv and \bexcel were left in "data"
        # when that domain was split out on 2026-07-30, so "csvyi oku" offered
        # data_analyze and plot_data but not csv_read.
        #
        # Stems, not \b-terminated: Turkish attaches the suffix straight onto
        # the acronym. "PDFteki", "csvyi", "excelde" match none of \bpdf\b,
        # \bcsv\b, \bexcel\b -- and "PDFteki toplantıları takvime ekle" was
        # therefore handed no PDF-capable tool at all.
        r"\bpdf", r"\bcsv", r"\bexcel",
    ],
    "web": [
        # \bhaber MOVED to the "news" domain in Post-MVP Faz 3 -- not copied.
        # It named a capability this project now actually has a tool for
        # (news_headlines over real feeds), and leaving it in both would score
        # the word twice, the exact mistake this table's header warns about.
        r"\binternet", r"\bweb\b", r"\bsite", r"\bsayfa",
        r"\bsearch\b", r"\baraştır", r"https?://", r"\bwww\.",
        r"\b[\w-]+\.(com|net|org|io|dev|edu|gov)\b",
    ],
    # ── Post-MVP Faz 3 ───────────────────────────────────────────────────────
    # Briefing patterns are all MULTI-WORD or briefing-specific on purpose.
    # The tempting short one -- \bbugün -- would fire on "bugünkü takvimimi
    # göster", "bugün kaç mail geldi", "bugünün harcamaları": a phantom second
    # domain on nearly every request that mentions today, which is the failure
    # Faz 2.75 (Paket F) removed three generic verbs to fix. A briefing is a
    # specific thing the user asks for by name, not a mood.
    #
    # A bare "günaydın" is deliberately NOT here. It is a greeting, and firing
    # four network calls on it would make saying good morning cost five
    # seconds. The plan's acceptance is the explicit request.
    "briefing": [
        r"\bbrifing", r"\bbriefing\b", r"\bbrief me\b",
        r"\bgünlük özet", r"\bgünün özeti", r"\bgün[üu]n [öo]zeti",
        r"\bbugün neler var", r"\bbugün ne var", r"\bbugün nelerim var",
        r"\bgünüme bak", r"\bgünüm nasıl", r"\bgüne başla",
        r"\bwhat'?s on today", r"\bwhat's my day",
    ],
    # \bhava alone is banned: "havalimanı" (airport) and "havale" (bank
    # transfer -- a finance word) both start with it, and a weather tool
    # offered for a money transfer is the substring class this module's header
    # was written about.
    "weather": [
        r"\bhava durum", r"\bhava nasıl", r"\bhavalar", r"\bsıcaklık",
        r"\byağmur", r"\bkar yağ", r"\bmeteoroloji", r"\bderece mi\b",
        r"\bweather\b", r"\bforecast\b",
    ],
    "news": [r"\bhaber", r"\bgündem", r"\bmanşet", r"\bson dakika"],
    "mail": [
        r"\bmail", r"\be-?posta", r"\bgmail\b", r"\binbox\b",
        r"\bgelen kutusu", r"\bmesaj",
    ],
    "calendar": [
        r"\btakvim", r"\btoplantı", r"\brandevu", r"\betkinlik",
        r"\bcalendar\b", r"\bmeeting", r"\bappointment", r"\bevent",
    ],
    "drive": [r"\bdrive\b", r"\bbulut", r"\bupload\b", r"\byükle"],
    # data / report / math were one "data" domain until 2026-07-30 (see
    # _TOOL_DOMAINS in jarvis/tool_registry.py for why it was split). The
    # patterns MOVED with their tools rather than being duplicated: leaving
    # \brapor in both "data" and "report" would score the word twice and let one
    # intent outrank a genuinely two-domain request.
    # \bcsv and \bexcel MOVED to "files" in Faz 2.75 (Paket F) rather than
    # being copied there. Leaving them in both would score the word twice --
    # the exact mistake this table's own header warns about for \brapor -- and
    # would let a one-format request outrank a genuinely two-domain one.
    "data": [
        r"\banaliz", r"\bgrafik", r"\bçiz", r"\bplot\b", r"\bchart\b",
        r"\bveri", r"\btablo",
    ],
    "report": [
        r"\brapor", r"\blatex\b", r"\bpdf rapor", r"\bmakale", r"\bmetin yaz",
        r"\bözet çıkar", r"\bderleme",
    ],
    "math": [
        r"\bhesapla", r"\bmatematik", r"\bdenklem", r"\bintegral", r"\btürev",
        r"\bsimül", r"\bgeodezi", r"\bkoordinat",
    ],
    "system": [
        r"\bshell\b", r"\bkomut", r"\bçalıştır", r"\bterminal\b",
        r"\bpowershell\b", r"\bpython\b", r"\bkod\b", r"\bscript", r"\bkota",
        r"\bpanel",
    ],
    "tasks": [
        r"\bgörev", r"\byapılacak", r"\btodo\b", r"\bhatırlat", r"\bzamanla",
        r"\bschedule\b", r"\balarm",
    ],
    "media": [
        r"\bspotify\b", r"\bmüzik", r"\bşarkı", r"\bçal\b", r"\bplaylist",
        r"\bses\b", r"\bvolume\b",
    ],
    "finance": [
        r"\bbütçe", r"\bharcama", r"\bgider", r"\bfatura", r"\bfinans",
        r"\bhisse", r"\bdolar", r"\beuro\b",
        # Added 2026-07-30: the owner's own MVP wording ("hesabımdaki para
        # akışını analiz et") matched NONE of the patterns above, so the finance
        # domain scored zero on the one request it exists to serve.
        #
        # `\bhesab` is the possessive/oblique stem of "hesap" (account):
        # hesabım, hesabımdaki, hesabında. Note it deliberately does NOT collide
        # with math's `\bhesapla` ("calculate") -- folded, "hesabimdaki" matches
        # \bhesab but not \bhesapla, and "hesapla" matches \bhesapla but not
        # \bhesab (p != b). The nominative "hesap" is left out on purpose: it is
        # the stem of hesapla* too, so `\bhesap` would drag every arithmetic
        # request into the finance domain.
        r"\bhesab", r"\bpara ak", r"\bnakit ak", r"\bgelir",
        r"\bekstre", r"\bbanka", r"\bişlem geçmiş", r"\bhesap hareket",
        r"\bcash ?flow", r"\btransaction", r"\bburgan",
    ],
    "memory": [r"\bnot\b", r"\bnotlar", r"\bvault\b", r"\barşiv", r"\bkaydettiğim"],
    "procedure": [r"\bprosedür", r"\bprocedure\b", r"\biş akışı"],
    # Agent Runtime rev.2, Faz 7 Part 2: workflow_start/workflow_status. Bare
    # "workflow" moved here from "procedure" -- saying just that word now
    # means "run this as a multi-step task", not "remember this procedure"
    # (procedure_save's own Turkish-native triggers, "prosedür"/"iş akışı",
    # are untouched). Gated explicit-only (_EXPLICIT_ONLY_DOMAINS) for the
    # same reason procedure_save is: this can actually execute real tool
    # calls, not just persist a description.
    "workflow": [r"\bworkflow\b", r"\bçok adımlı görev", r"\bmulti-?step\b"],
    "mcp": [r"\btarayıcı", r"\bbrowser\b", r"\bplaywright\b", r"\bmcp\b", r"\btıkla", r"\bclick\b"],
}

# Turkish diacritic fold. ASR transcripts and casual typing routinely drop
# ç/ğ/ı/ö/ş/ü (and the ' in "Drive'a"), which silently broke routing — a
# closure sweep found "grafik ciz" → conversation (no tools), "Spotifyda çal"
# → conversation, "Drivea yükle" → files (google_drive invisible). Folding BOTH
# the query and the pattern sources to lowercase ASCII fixes it and, as a
# bonus, sidesteps the Turkish İ/ı re.IGNORECASE casing trap this module's
# header already warns about (the "ok in çok" incident). translate() only
# rewrites letters, so regex metacharacters (\b \. \w (|) ? + [-]) are untouched.
_TR_FOLD = str.maketrans({
    "ç": "c", "ğ": "g", "ı": "i", "İ": "i", "ö": "o", "ş": "s", "ü": "u", "I": "i",
    "Ç": "c", "Ğ": "g", "Ö": "o", "Ş": "s", "Ü": "u",
})


def _fold(s: str) -> str:
    """Lowercase + Turkish-diacritic-fold to ASCII for diacritic-insensitive matching."""
    return s.translate(_TR_FOLD).lower()


_COMPILED: dict[str, list[re.Pattern]] = {
    domain: [re.compile(_fold(p), re.IGNORECASE) for p in patterns]
    for domain, patterns in _DOMAIN_PATTERNS.items()
}

_EXPLICIT_INTENT = re.compile(_fold(r"\baracıyla|\baraç\b|\btool\b|\bkullanarak"), re.IGNORECASE)


@dataclass(frozen=True)
class ToolRoute:
    """Routing verdict for one turn. Serialized as a plain dict in graph
    state (msgpack/checkpoint friendly) — see to_dict/from_dict."""
    primary_domain: str
    domains: list[str] = field(default_factory=list)
    confidence: float = 0.0
    explicit_tool_intent: bool = False

    def to_dict(self) -> dict:
        return {
            "primary_domain": self.primary_domain,
            "domains": list(self.domains),
            "confidence": self.confidence,
            "explicit_tool_intent": self.explicit_tool_intent,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "ToolRoute | None":
        if not data or not isinstance(data, dict):
            return None
        return cls(
            primary_domain=str(data.get("primary_domain") or "conversation"),
            domains=[str(d) for d in (data.get("domains") or [])],
            confidence=float(data.get("confidence") or 0.0),
            explicit_tool_intent=bool(data.get("explicit_tool_intent")),
        )


def classify_query(query: str) -> ToolRoute:
    """Score every domain by distinct pattern hits; no hits ⇒ conversation."""
    text = _fold(query or "")  # diacritic-insensitive: patterns are folded to match
    scores: dict[str, int] = {}
    for domain, patterns in _COMPILED.items():
        hits = sum(1 for p in patterns if p.search(text))
        if hits:
            scores[domain] = hits

    explicit = bool(_EXPLICIT_INTENT.search(text)) or bool(_EXPLICIT_ONLY_DOMAINS & set(scores))
    if not scores:
        return ToolRoute("conversation", ["conversation"], 1.0, explicit)

    ranked = sorted(
        scores,
        key=lambda d: (-scores[d], _DOMAIN_PRIORITY.index(d) if d in _DOMAIN_PRIORITY else 99),
    )[:MAX_DOMAINS_PER_TURN]
    total = sum(scores.values())
    confidence = 1.0 if len(scores) == 1 else round(scores[ranked[0]] / total, 2)
    return ToolRoute(ranked[0], ranked, confidence, explicit)


# Which domain owns the tools that revise a given kind of working-set object
# (Post-MVP Faz 4). Only `chart` has revision tools today; the rest are the
# store's declared kinds, mapped now so adding their tools is a tool change and
# not also a routing change.
_KIND_DOMAINS: dict[str, str] = {
    "chart": "data", "table": "data", "email": "mail", "report": "report",
    # Post-MVP Faz 5: a staged calendar proposal. "Ekle" / "onayla" / "olur"
    # name no capability, so the turn that APPROVES an event would otherwise
    # classify as `conversation` and get zero tools -- the same shape the chart
    # revision hit, with a worse failure mode, since the user believes the
    # event was created.
    "calendar_candidate": "calendar",
}


def with_active_object(route: ToolRoute, kind: str) -> ToolRoute:
    """Let a live working-set object claim a turn that matched NOTHING.

    Post-MVP Faz 4. A revision is usually a sentence with no capability noun in
    it at all -- *"rengini kırmızı yap"*, *"biraz daha büyük olsun"*, *"eski
    haline getir"*. None of those match this module's table, so they classify
    as `conversation`, the model is handed zero tools, and it answers *"tamam,
    kırmızı yaptım"* without touching anything. That is the same shape as the
    Faz 3 weather finding: a plausible answer exists, so the model writes one.

    Guessing revision vocabulary was the obvious fix and is the wrong one --
    it is unbounded, and this module has already had to delete three generic
    verbs for inventing phantom domains. State answers it exactly: if this
    conversation HAS an editable object, an unclassifiable turn is far more
    likely to be about it than about nothing.

    Deliberately narrow to the `conversation` case. Applying it to every turn
    would attach the chart domain to *"hava durumu nasıl"*, making it
    multi-domain and therefore reasoning-tier -- paying a latency tax on every
    turn for the rest of the conversation. A turn that already routed somewhere
    named what it wanted.

    Note what this does NOT do: it never removes a domain, and it cannot turn a
    turn that matched nothing into one that skips the gate. It changes which
    tools the model SEES, and every safety layer sits after it, unchanged.
    """
    domain = _KIND_DOMAINS.get((kind or "").strip().lower())
    if not domain or route.primary_domain != "conversation":
        return route
    return ToolRoute(domain, [domain], route.confidence, route.explicit_tool_intent)


def _by_relevance(names: list[str], folded_query: str) -> list[str]:
    """Tools the query actually names, first; everything else in registry order.

    Deterministic and deliberately shallow: a tool's own name tokens
    (``csv_read`` → csv, read) checked against the folded query. No model, no
    embedding, no scoring table to drift.

    ``_GENERIC_NAME_TOKENS`` are skipped because every tool in a domain shares
    them -- "read" would make csv_read, pdf_read, excel_read and file_read all
    equally "relevant" to the word "oku" and rank nothing.
    """
    def score(name: str) -> int:
        tokens = {t for t in name.split("_") if t not in _GENERIC_NAME_TOKENS}
        return -sum(1 for t in tokens if t and t in folded_query)

    return sorted(names, key=score)  # stable: ties keep registry order


# Tokens that carry no information about WHICH tool inside a domain to prefer.
# Two kinds:
#   verbs shared by most members  — read, write, list, ...
#   the domain's own word         — "mail" is why itu_mail outranked gmail on
#                                   "Son 3 mailimi listele": itu_mail's name
#                                   happens to embed the domain word and
#                                   gmail's does not, which says nothing about
#                                   which mailbox the user meant.
_GENERIC_NAME_TOKENS = frozenset({
    "read", "write", "list", "get", "set", "data", "search", "run", "tool",
    "content", "compose", "compile", "analyze", "solve", "doc", "append",
    "mail", "file", "google", "web",
})


def select_tool_names(
    route: ToolRoute | None,
    available: list[str],
    query: str = "",
) -> list[str]:
    """Resolve a route to concrete tool names from what's actually available.

    None (no route in state — background/proactive paths, old checkpoints)
    ⇒ the full pre-router toolset, so nothing regresses behind this feature.

    Post-MVP Faz 2.75 (Paket F) replaced "fill each domain in route order until
    the cap" with an equal share per routed domain, filled relevance-first.
    The old rule quietly starved the second domain of a two-domain request:

        "Masaüstündeki satis.csv dosyasını oku ve grafiğini çiz"
          -> [files, data]; files has 7 tools and took 7 of the 8 slots,
             so `data` got exactly one -- data_analyze -- and **plot_data was
             never offered at all**.

    The model was then measured "failing to complete the chain" in the Faz 2.5
    A/B (0/10 on both tiers). It was not offered the tool that draws charts.
    Six of those seven file tools were irrelevant to a request that named a
    .csv; relevance ordering puts csv_read first and the fair share leaves room
    for the domain the user's second verb asked for.

    `query` is optional so every existing caller and old checkpoint keeps
    working -- without it the ordering is simply registry order, i.e. the
    previous behaviour within each share.
    """
    if route is None:
        return list(available)
    if route.primary_domain == "conversation":
        return []

    from jarvis.tool_registry import TOOL_SPECS

    def members(domain: str) -> list[str]:
        names = []
        for n in available:
            spec = TOOL_SPECS.get(n)
            spec_domain = getattr(spec, "domain", "") if spec else "mcp"
            if (spec_domain or "mcp") == domain:
                # procedure_save / workflow_start are opt-in: explicit wording only
                if domain in _EXPLICIT_ONLY_DOMAINS and not route.explicit_tool_intent:
                    continue
                names.append(n)
        return names

    folded = _fold(query or "")
    groups = [(d, _by_relevance(members(d), folded)) for d in route.domains]
    groups = [(d, g) for d, g in groups if g]
    if not groups:
        return []

    # Round 1 — an equal share each, so no domain can starve another. This is
    # the reservation idea the previous version had, generalized: reserving one
    # slot per later domain stopped a domain being dropped ENTIRELY, but still
    # let a large one leave the next with a single tool.
    share = max(1, MAX_TOOLS_PER_TURN // len(groups))
    selected: list[str] = []
    for _domain, group in groups:
        selected.extend(n for n in group[:share] if n not in selected)

    # Round 2 — leftovers, in route order, so the primary domain gets the
    # slack when the others are small.
    for _domain, group in groups:
        room = MAX_TOOLS_PER_TURN - len(selected)
        if room <= 0:
            break
        selected.extend([n for n in group if n not in selected][:room])

    dropped = [n for _d, g in groups for n in g if n not in selected]
    if dropped:
        # No silent truncation. When a turn goes wrong the first question is
        # "did the model even have the tool", and that used to be unanswerable
        # from the logs -- which is how plot_data went missing for a whole
        # measurement round without anyone noticing.
        logger.info(
            "tool_router: %d/%d tools dropped for %s -> %s",
            len(dropped), len(dropped) + len(selected), route.domains, dropped,
        )
    return selected[:MAX_TOOLS_PER_TURN]
