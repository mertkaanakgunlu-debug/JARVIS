"""Did the user explicitly ask for an artifact? — decided from their words.

Post-MVP Faz 6. The completion contract only means anything if "the user
asked for a chart" is settled BEFORE the turn runs, deterministically. Two
reasons it cannot be the verifying node's job:

* the same model that skipped the tool would be deciding whether the tool was
  needed -- an answer that never called `plot_data` has every incentive to
  conclude none was required;
* the answer must be identical on the control and treatment arms of the A/B,
  or the measurement compares two different populations of turns.

So this is a pure function of the query string. No LLM, no state, no IO --
the same shape as nlu/temporal.py and nlu/event_text.py next door.

**The rule: a chart expression AND a data reference.** Both halves earn their
place:

* Without the data half, "grafik çizebiliyor musun?" (can you draw charts?)
  becomes a promise to produce a file, and so does "bu mimariyi görselleştir"
  -- a request for a diagram, which `plot_data` cannot draw and should not
  try to.
* Without the chart half, every "analyse the CSV" turn would carry a contract
  and a repair budget it has no use for.

Note what the rule does NOT key on: the interrogative. Turkish `-ebilir
misin` ("could you...") is far more often a courteous imperative than a
question about ability, and the owner writes that way. "Bu dosyanın grafiğini
çizebilir misin?" is a request; "grafik çizebiliyor musun?" is not -- and the
thing that separates them is the file, not the mood.

Scope is deliberately CREATION-only this phase. "grafiği kırmızı yap" is a
real request with a real postcondition, but a different one (an existing
object changes; nothing new appears), so it carries no requirement here
rather than a wrong one.
"""

from __future__ import annotations

import re

#: Turkish consonant mutation makes the stem grafik/grafiğ; matching the stem
#: covers grafik, grafiği, grafiğini, grafikler without a stemmer.
_CHART_NOUN = re.compile(r"\bgrafi[kğ]|\bchart\b|\bplot\b|\bçizelge", re.IGNORECASE)

#: Producing verbs. Deliberately not "analiz/özet/incele" -- those are what a
#: chart request is most often confused WITH.
_CREATE_VERB = re.compile(
    r"\bçiz|\boluştur|\byap|\bhazırla|\bgöster|\bçıkar|\bver\b"
    r"|\bdraw\b|\bcreate\b|\bmake\b|\bgenerate\b|\bplot\b|\bshow\b|\bproduce\b",
    re.IGNORECASE,
)

#: "Visualise" is a chart expression on its own -- but only ever alongside a
#: data reference, since it covers diagrams and sketches too.
_VISUALISE = re.compile(r"\bgörselleştir|\bvisuali[sz]e\b", re.IGNORECASE)

#: The data half. A filename with a tabular extension is the strongest form;
#: the vocabulary terms cover "bu dosyanın", "tablodaki", "verilerden".
_DATA_FILE = re.compile(
    r"\b[\w\-. ]+\.(?:csv|xlsx|xlsm|xls|tsv|json|parquet)\b", re.IGNORECASE
)
_DATA_WORD = re.compile(
    r"\bveri|\bdosya|\btablo|\bsütun|\bsutun|\bkolon|\bsayfa\b|\bexcel\b|\bcsv\b"
    r"|\bdata\b|\bfile\b|\btable\b|\bcolumn\b|\brow\b|\bsheet\b|\bspreadsheet\b"
    r"|\bdataset\b|\bvalues\b|\bdeğerler",
    re.IGNORECASE,
)

#: Checked FIRST and wins outright. "grafik çizme" (don't draw a chart) shares
#: its stem with "çiz", so a rule that scored the positive signals first would
#: read a refusal as a request.
_REFUSAL = re.compile(
    r"grafi[kğ]\w*\s+(?:çizme|oluşturma|yapma|silme|istemiyorum|istemem|gerekmiyor)\b"
    r"|grafi[kğ]\w*\s+(?:istemiyorum|istemem)"
    r"|\bgrafi[kğ]\w*\s*\w*\s*(?:gerek yok|gereksiz)\b"
    r"|\bno (?:chart|plot|graph)\b|\bwithout a (?:chart|plot|graph)\b"
    r"|\bdon'?t (?:draw|plot|create a chart)\b",
    re.IGNORECASE,
)

#: Revision, not creation -- this phase's contract cannot describe it, so it
#: must not claim one. Checked before the positive rule for the same reason as
#: the refusal: "grafiği kırmızı yap" contains both a chart noun and a verb.
_REVISION = re.compile(
    r"grafi[kğ]\w*\s+\w+\s+(?:yap|olsun|çevir)\b"
    r"|\b(?:başlığı|rengi|rengini|türünü|tipini)\b"
    r"|\b(?:sütun|çizgi|pasta|bar|line|pie)\s+grafi[kğ]\w*\s+olsun\b"
    r"|\bmake the (?:chart|plot|graph)\b",
    re.IGNORECASE,
)

CHART_CREATION: tuple[dict[str, str], ...] = ({"kind": "chart", "operation": "create"},)


def required_outputs_for(query: object) -> list[dict]:
    """The artifacts this turn is committed to producing, from the query alone.

    Returns a FRESH list every call -- it goes straight into graph state and
    through a checkpointer, where a shared module-level object would be one
    accidental mutation away from leaking across turns.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    text = query.strip()

    if _REFUSAL.search(text) or _REVISION.search(text):
        return []

    has_data = bool(_DATA_FILE.search(text) or _DATA_WORD.search(text))
    if not has_data:
        return []

    if _VISUALISE.search(text):
        return [dict(spec) for spec in CHART_CREATION]
    if _CHART_NOUN.search(text) and _CREATE_VERB.search(text):
        return [dict(spec) for spec in CHART_CREATION]
    return []
