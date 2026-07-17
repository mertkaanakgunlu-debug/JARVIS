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
    "procedure", "mail", "calendar", "drive", "media", "finance", "tasks",
    "web", "mcp", "files", "data", "system", "memory",
]

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
    "data": [
        r"\banaliz", r"\bgrafik", r"\bçiz", r"\bplot\b", r"\bchart\b",
        r"\brapor", r"\bhesapla", r"\bmatematik", r"\bdenklem", r"\bsimül",
        r"\bcsv\b", r"\bexcel\b", r"\bveri",
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
    ],
    "memory": [r"\bnot\b", r"\bnotlar", r"\bvault\b", r"\barşiv", r"\bkaydettiğim"],
    "procedure": [r"\bprosedür", r"\bprocedure\b", r"\biş akışı", r"\bworkflow"],
    "mcp": [r"\btarayıcı", r"\bbrowser\b", r"\bplaywright\b", r"\bmcp\b", r"\btıkla", r"\bclick\b"],
}

_COMPILED: dict[str, list[re.Pattern]] = {
    domain: [re.compile(p, re.IGNORECASE) for p in patterns]
    for domain, patterns in _DOMAIN_PATTERNS.items()
}

_EXPLICIT_INTENT = re.compile(r"\baracıyla|\baraç\b|\btool\b|\bkullanarak", re.IGNORECASE)


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
    text = query or ""
    scores: dict[str, int] = {}
    for domain, patterns in _COMPILED.items():
        hits = sum(1 for p in patterns if p.search(text))
        if hits:
            scores[domain] = hits

    explicit = bool(_EXPLICIT_INTENT.search(text)) or "procedure" in scores
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
                # procedure_save is opt-in: explicit wording only
                if domain == "procedure" and not route.explicit_tool_intent:
                    continue
                names.append(n)
        return names

    selected: list[str] = []
    for i, domain in enumerate(route.domains):
        group = [n for n in members(domain) if n not in selected]
        if not group:
            continue
        if len(selected) + len(group) <= MAX_TOOLS_PER_TURN:
            selected.extend(group)
        elif i == 0:
            selected.extend(group[:MAX_TOOLS_PER_TURN])
    return selected
