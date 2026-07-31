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

import re
from dataclasses import dataclass, field

MAX_TOOLS_PER_TURN = 8
MAX_DOMAINS_PER_TURN = 3

# Ambiguity guard: more specific/external domains outrank generic local ones
# on equal keyword-hit scores ("Son 3 mailimi listele" is mail, even though
# "listele" also smells like the filesystem).
_DOMAIN_PRIORITY = [
    "procedure", "workflow", "mail", "calendar", "drive", "media", "finance",
    "tasks", "web", "mcp", "files", "data", "report", "math", "system", "memory",
]

# Domains that are opt-in via explicit wording only, never picked up from
# general vibes — same reasoning as procedure_save originally: a capability
# consequential enough (persisting a procedure; here, actually KICKING OFF a
# multi-step workflow that can execute real side effects) that an ambiguous
# keyword hit must not be enough to expose it.
_EXPLICIT_ONLY_DOMAINS = frozenset({"procedure", "workflow"})

_DOMAIN_PATTERNS: dict[str, list[str]] = {
    "files": [
        r"\bdosya", r"\bklas[öo]r", r"\bdizin", r"\bfolder\b", r"\bfile",
        r"\bdirectory\b", r"\bmasaüstü", r"\bdesktop\b", r"\boku\b",
        r"\.(txt|md|pdf|csv|xlsx?|docx?|json|py|log)\b", r"\bpdf\b", r"\blistele",
    ],
    "web": [
        r"\binternet", r"\bweb\b", r"\bsite", r"\bsayfa", r"\bhaber",
        r"\bsearch\b", r"\bara\b", r"\baraştır", r"https?://", r"\bwww\.",
        r"\b[\w-]+\.(com|net|org|io|dev|edu|gov)\b",
    ],
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
    "data": [
        r"\banaliz", r"\bgrafik", r"\bçiz", r"\bplot\b", r"\bchart\b",
        r"\bcsv\b", r"\bexcel\b", r"\bveri", r"\btablo",
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


def select_tool_names(route: ToolRoute | None, available: list[str]) -> list[str]:
    """Resolve a route to concrete tool names from what's actually available.

    None (no route in state — background/proactive paths, old checkpoints)
    ⇒ the full pre-router toolset, so nothing regresses behind this feature.
    Whole domains are added in route order while the total stays within
    MAX_TOOLS_PER_TURN; a domain that would overflow is skipped, the primary
    domain is never skipped (truncated instead if it alone exceeds the cap).
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

    # Groups first, so slot reservation can see what every routed domain wants.
    groups = [(d, members(d)) for d in route.domains]
    groups = [(d, g) for d, g in groups if g]

    selected: list[str] = []
    for i, (domain, group) in enumerate(groups):
        group = [n for n in group if n not in selected]
        if not group:
            continue
        # Reserve at least one slot for each LATER routed domain before letting
        # this one fill up. Without this, a domain whose size equals
        # MAX_TOOLS_PER_TURN consumes the entire budget and every subsequent
        # domain is dropped -- which is exactly what happened to the 8-tool
        # "data" domain: the MVP prompt routed to [data, mail] and mail became
        # invisible, so the model was asked to read mail with no mail tool.
        # Splitting "data" fixed today's instance; this fixes the class, for the
        # next domain that grows.
        reserved = sum(1 for _, later in groups[i + 1:] if later)
        room = MAX_TOOLS_PER_TURN - len(selected) - reserved
        if i == 0:
            # The primary domain is never skipped outright -- truncated at worst,
            # and always given at least one tool.
            room = max(room, 1)
        if len(group) <= room:
            selected.extend(group)
        elif room > 0:
            selected.extend(group[:room])
    return selected
