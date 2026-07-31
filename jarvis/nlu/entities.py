"""Person-name resolution with confidence bands — never a silent substitution.

Post-MVP Faz 2, plan item 3. The failure this closes is the "Baranla → Baranda"
class: Turkish attaches case endings to names ("Baran'la" = with Baran), a
model relays the inflected form verbatim, and the calendar ends up holding an
event for a person who does not exist.

**The rule that makes this safe: a stem is only ever adopted when a SOURCE
corroborates it.** Blind suffix-stripping is worse than the bug it fixes —
"Metin" would become "Met", "Erdem" would become "Erd" — so this module never
normalizes on the strength of its own morphology. It generates candidate stems,
asks the sources whether any of them is a real person, and adopts one only if
the answer comes back yes. An unknown name is left exactly as the user wrote it.

Source priority is the plan's:

    1. Google Contacts        — authoritative, opt-in (see GoogleContactsSource)
    2. JARVIS durable entities — what this assistant has actually seen before
    3. Conversation context    — names mentioned in this conversation

Confidence bands are the plan's, and they are deliberately conservative in
the direction that matters:

    >= 0.95   normalize automatically
    0.75-0.95 ask the user first
    < 0.75    leave the input alone

A false positive on a person's name is more damaging than leaving a typo in
place — "Baran" silently rewritten to the wrong "Barış" in a meeting invite is
a mistake the user cannot see happening.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Protocol, Sequence

from jarvis.nlu.temporal import AUTO_THRESHOLD, band, fold

__all__ = [
    "PersonCandidate", "EntityResolution", "PersonSource",
    "StaticPersonSource", "DurableEntitySource", "ConversationSource",
    "GoogleContactsSource", "SourceChain", "candidate_stems", "resolve_person",
]

# ── Turkish case endings ─────────────────────────────────────────────────────
#
# Longest first: "Baran'ndan" must lose "ndan", not "n". Buffer consonants
# (the y/n that appear between a vowel-final name and a vowel-initial ending)
# are folded into the ending rather than modelled separately — this is a
# lookup aid, not a morphological analyser, and its output is always checked
# against a real source before it is believed.
_ENDINGS: tuple[str, ...] = (
    "ndan", "nden", "yla", "yle", "nin", "nın", "nun", "nün",
    "dan", "den", "tan", "ten", "nda", "nde", "yi", "yı", "yu", "yü",
    "la", "le", "da", "de", "ta", "te", "na", "ne", "ya", "ye",
    "in", "ın", "un", "ün", "im", "ım", "um", "üm",
    "a", "e", "i", "ı", "u", "ü", "n",
)

# A stem shorter than this is not worth trusting even with a source hit — at
# 2 characters almost any ending "matches" almost any name.
_MIN_STEM = 3

_APOSTROPHE = re.compile(r"[’']")

# Source trust ceilings. A resolution can never score above its source's
# ceiling, whatever the string match looks like — the whole point of the
# priority order is that "seen in this conversation once" is weaker evidence
# than "in the user's contact list", even when both match exactly.
SOURCE_CEILING: dict[str, float] = {
    "contacts": 0.99,
    "durable_entities": 0.96,
    "conversation": 0.86,
}


@dataclass(frozen=True)
class PersonCandidate:
    name: str
    source: str
    score: float
    matched_stem: str
    exact: bool


@dataclass(frozen=True)
class EntityResolution:
    mention: str            # exactly what the user/model wrote
    canonical: str          # what to use — equals `mention` unless band == "auto"
    stem: str               # the stem that matched, "" if none did
    confidence: float
    candidates: tuple[PersonCandidate, ...] = ()
    reason: str = ""
    inflected: bool = False  # was a case ending stripped to get the match?

    @property
    def band(self) -> str:
        return band(self.confidence)

    @property
    def changed(self) -> bool:
        return self.canonical != self.mention

    @property
    def alternatives(self) -> tuple[str, ...]:
        """Distinct candidate names, best first — what to offer in an "ask"."""
        seen: list[str] = []
        for c in self.candidates:
            if c.name not in seen:
                seen.append(c.name)
        return tuple(seen)


class PersonSource(Protocol):
    """Somewhere person names can be looked up. Ordered by trust, not by cost."""

    name: str

    def available(self) -> bool: ...

    def people(self) -> Sequence[str]:
        """Every known person name. Small by construction for all three real
        sources (a personal contact list, one user's entity table, one
        conversation), so this is a list rather than a query interface."""
        ...


def candidate_stems(mention: str) -> list[tuple[str, bool]]:
    """[(stem, is_inflected)] for a mention, most-likely first.

    The mention itself always comes first and always with is_inflected=False:
    a name that is already correct must never be beaten by a shortened guess.
    """
    raw = (mention or "").strip()
    if not raw:
        return []

    out: list[tuple[str, bool]] = [(raw, False)]
    seen = {fold(raw)}

    # Apostrophe form ("Baran'la") is unambiguous in Turkish orthography — the
    # apostrophe exists precisely to separate a proper noun from its ending.
    if _APOSTROPHE.search(raw):
        head = _APOSTROPHE.split(raw)[0].strip()
        if len(head) >= _MIN_STEM and fold(head) not in seen:
            out.append((head, True))
            seen.add(fold(head))

    folded = fold(raw)
    for ending in _ENDINGS:
        if not folded.endswith(ending):
            continue
        stem = raw[: len(raw) - len(ending)]
        if len(stem) < _MIN_STEM:
            continue
        if fold(stem) in seen:
            continue
        out.append((stem, True))
        seen.add(fold(stem))
    return out


def _edit_distance_le_1(a: str, b: str) -> bool:
    """True when a and b differ by at most one insert/delete/substitute.
    Cheap and bounded — deliberately not a general fuzzy matcher, because a
    looser one produces exactly the confident-wrong-name failure this module
    exists to prevent."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long):
        if short[i] != long[j]:
            if skipped:
                return False
            skipped = True
            j += 1
            continue
        i += 1
        j += 1
    return True


@dataclass
class StaticPersonSource:
    """A fixed list — the test double, and the shape every real source takes."""

    names: Sequence[str]
    name: str = "conversation"
    _available: bool = True

    def available(self) -> bool:
        return self._available and bool(self.names)

    def people(self) -> Sequence[str]:
        return list(self.names)


@dataclass
class DurableEntitySource:
    """People JARVIS has recorded across sessions (the `entities` table).

    The store is injected rather than imported so this package keeps its
    no-heavy-dependencies property — jarvis.nlu must stay importable from
    policy_guard, which deliberately imports nothing graph- or DB-shaped.
    """

    store: object  # SessionStore-shaped: .top_entities(n) -> list[dict]
    name: str = "durable_entities"
    limit: int = 500

    def available(self) -> bool:
        return self.store is not None

    def people(self) -> Sequence[str]:
        try:
            rows = self.store.top_entities(self.limit)
        except Exception:  # noqa: BLE001 — a name lookup must never break a turn
            return []
        return [r["name"] for r in rows if r.get("type") == "person" and r.get("name")]


@dataclass
class ConversationSource:
    """Names already mentioned in this conversation.

    Weakest source on purpose: "the model wrote this word two turns ago" is
    self-corroboration, and its ceiling (0.86) sits inside the ASK band, so a
    conversation-only match can never auto-normalize.
    """

    names: Sequence[str] = field(default_factory=list)
    name: str = "conversation"

    def available(self) -> bool:
        return bool(self.names)

    def people(self) -> Sequence[str]:
        return list(self.names)


@dataclass
class GoogleContactsSource:
    """Google Contacts via the People API — the plan's first-priority source.

    **Off unless explicitly enabled** (`settings.google_contacts_enabled`), and
    that is not timidity: the People API needs the `contacts.readonly` scope,
    and Google issues one token per scope SET. Adding it to the existing
    calendar/gmail credentials would invalidate the token those integrations
    are working with today and force the owner through a fresh consent screen
    — breaking two live features to add a third. This source therefore keeps
    its OWN token file, so turning it on is additive and turning it off again
    costs nothing.

    Enabling it is an owner decision (one re-consent, `python
    scripts/auth_setup.py` after setting the flag), not something to switch on
    from here.
    """

    settings: object = None
    name: str = "contacts"
    _cache: list[str] | None = field(default=None, repr=False)

    _SCOPES = ["https://www.googleapis.com/auth/contacts.readonly"]

    def available(self) -> bool:
        return bool(getattr(self.settings, "google_contacts_enabled", False))

    def _token_file(self):
        from jarvis import paths

        return paths.project_data_dir() / ".contacts_token.json"

    def people(self) -> Sequence[str]:
        if not self.available():
            return []
        if self._cache is not None:
            return self._cache
        self._cache = []
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            token_file = self._token_file()
            if not token_file.exists():
                return self._cache
            creds = Credentials.from_authorized_user_file(str(token_file), self._SCOPES)
            service = build("people", "v1", credentials=creds)
            result = (
                service.people()
                .connections()
                .list(resourceName="people/me", personFields="names", pageSize=1000)
                .execute()
            )
            names: list[str] = []
            for person in result.get("connections", []):
                for entry in person.get("names", []):
                    display = entry.get("displayName") or entry.get("givenName")
                    if display:
                        names.append(display)
            self._cache = names
        except Exception:  # noqa: BLE001 — never let contacts break a turn
            self._cache = []
        return self._cache


@dataclass
class SourceChain:
    """Sources in priority order. Every available source is consulted (not just
    the first that hits) — knowing that two sources agree, or that only the
    weakest one does, is exactly what the confidence score is made of."""

    sources: Sequence[PersonSource] = ()

    @classmethod
    def build(
        cls,
        *,
        settings: object = None,
        session_store: object = None,
        conversation_names: Iterable[str] = (),
    ) -> "SourceChain":
        return cls([
            GoogleContactsSource(settings=settings),
            DurableEntitySource(store=session_store),
            ConversationSource(names=list(conversation_names)),
        ])

    def available(self) -> list[PersonSource]:
        out = []
        for s in self.sources:
            try:
                if s.available():
                    out.append(s)
            except Exception:  # noqa: BLE001
                continue
        return out


# Penalty applied when the match required stripping a case ending WITHOUT an
# apostrophe to guide it. "Baran'la" -> "Baran" is orthographically explicit;
# "Baranla" -> "Baran" is a guess that happened to be corroborated, which is
# good evidence but not the same thing.
# Penalties are tuned so the resulting ladder lands where the plan wants it:
#
#   contacts          exact 0.99 · apostrophe 0.98 · inflected 0.95  -> auto
#   durable entities  exact 0.96 · apostrophe 0.95                   -> auto
#                     inflected 0.92                                 -> ask
#   conversation      anything <= 0.86                               -> ask
#   any fuzzy match / any ambiguity                                  -> ask
#
# Note what this means today: Google Contacts is off by default, so no
# inflected form auto-normalizes in the shipped configuration — only an
# already-correct name that a durable entity confirms, which is a no-op by
# definition. That is the honest state, not a claim that the feature is idle.
_INFLECTION_PENALTY = 0.04
_APOSTROPHE_PENALTY = 0.01
_FUZZY_PENALTY = 0.18
# Two different people matching the same mention is not a scoring problem, it
# is a question for the user. Capped just under AUTO so it can never act.
_AMBIGUITY_CAP = AUTO_THRESHOLD - 0.05


def resolve_person(
    mention: str,
    chain: SourceChain | None = None,
    *,
    sources: Sequence[PersonSource] | None = None,
) -> EntityResolution:
    """Resolve one person mention against the source chain.

    Returns the mention UNCHANGED unless confidence reaches the auto band.
    Callers in the ask band should surface `alternatives` as a question; callers
    below it should do nothing at all.
    """
    raw = (mention or "").strip()
    if not raw:
        return EntityResolution("", "", "", 0.0, (), "no name given")

    chain = chain or SourceChain(sources or ())
    stems = candidate_stems(raw)
    matches: list[PersonCandidate] = []

    for source in chain.available():
        ceiling = SOURCE_CEILING.get(source.name, 0.80)
        try:
            known = list(source.people())
        except Exception:  # noqa: BLE001
            continue
        for stem, inflected in stems:
            folded_stem = fold(stem)
            has_apostrophe = bool(_APOSTROPHE.search(raw)) and inflected
            for person in known:
                folded_person = fold(person)
                exact = folded_person == folded_stem
                fuzzy = not exact and len(folded_stem) >= 4 and _edit_distance_le_1(folded_person, folded_stem)
                if not (exact or fuzzy):
                    continue
                score = ceiling
                if inflected:
                    score -= _APOSTROPHE_PENALTY if has_apostrophe else _INFLECTION_PENALTY
                if fuzzy:
                    score -= _FUZZY_PENALTY
                matches.append(PersonCandidate(person, source.name, round(score, 4), stem, exact))

    if not matches:
        return EntityResolution(
            raw, raw, "", 0.0, (),
            f"'{raw}' matched no known contact or previously-seen person — left as written",
        )

    matches.sort(key=lambda c: (-c.score, not c.exact, c.name))
    best = matches[0]

    distinct = {fold(c.name) for c in matches}
    confidence = best.score
    reason = f"matched '{best.name}' in {best.source}"
    if len(distinct) > 1:
        confidence = min(confidence, _AMBIGUITY_CAP)
        others = ", ".join(sorted({c.name for c in matches if fold(c.name) != fold(best.name)}))
        reason = f"'{raw}' could be {best.name} or {others} — ambiguous, ask"
    elif best.matched_stem != raw:
        reason = f"'{raw}' resolved to '{best.name}' ({best.source})"

    inflected = fold(best.matched_stem) != fold(raw)
    canonical = best.name if band(confidence) == "auto" else raw
    return EntityResolution(
        mention=raw,
        canonical=canonical,
        stem=best.matched_stem,
        confidence=confidence,
        candidates=tuple(matches[:8]),
        reason=reason,
        inflected=inflected,
    )
