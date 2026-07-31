"""Typed evidence + unbacked-claim detection -- Post-MVP Faz 1 (honesty kernel).

Two plan items live here.

**Item 3, typed evidence.** The composer and every check downstream of it see
a structured EvidenceSet built from ExecutionEnvelopes -- artifacts,
operations, facts -- never a regex sweep over the tool's prose. The external
review's objection to number-scanning was specific and correct: *"Bunu 3
adimda yapabiliriz"* and *"89 islemin 45'i gelir"* feed the same extractor,
so a numeric claim-checker manufactures false positives out of ordinary
sentences. Numbers are therefore never the trigger here; nothing in this
module looks at one.

**Item 2, the zero-tool hallucination class.** The recorded live incident:
the model reported a PNG at a path, no tool had run, no file existed. The
existing VerifiedExecutionSummary machinery cannot see this -- it reasons
about operations that RAN, and in this turn none did, so its envelope list
is empty and every check it offers is vacuously satisfied. summary.py's
audit_claims() has the same structural blind spot for a different reason:
it returns early unless summary.any_failed, which an empty operation list
can never be. So the check had to be written rather than re-pointed; it is
the one place in this system where the model's TEXT is evidence, because a
claim only exists in text.

Precision, not recall, is the design constraint. A false block is worse than
a missed hallucination: it makes JARVIS deny work it actually did, and the
plan's own risk table says blocking happens only on a PROVABLE contradiction.
Three independent conditions must therefore all hold before anything fires:

  1. the response makes a COMPLETION assertion -- a curated closed list of
     perfective forms (olusturdum / kaydedildi / "I've created"), never a
     suffix heuristic. Ability and future forms (olusturabilirim,
     olusturacagim) and participles ("daha once olusturdugum grafik") are
     excluded by construction, so an OFFER to do something never trips it.
  2. no negation/failure language anywhere in the response -- a model that
     correctly says it could not do something is not claiming it did.
  3. the claim contradicts the evidence: either a named file is neither a
     declared artifact of this turn NOR present on disk, or a SIDE EFFECT
     (saved / sent / exported -- never a generic "created", see the verb
     classes below) is asserted for a turn in which no operation ran at all.

Condition 3's "nor present on disk" clause is what keeps this honest in the
ambiguous direction. If the file the response names really is there, we do
not know that the sentence is false -- the user may have asked about
something written last week -- so we say nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from jarvis.execution.summary import VerifiedExecutionSummary

# Extensions that make a bare token a file reference. Deliberately the
# artifact formats this assistant actually produces or is asked about --
# not "any dotted token", which would swallow version numbers and domains.
ARTIFACT_EXTENSIONS = (
    "png", "jpg", "jpeg", "gif", "svg", "webp",
    "xlsx", "xls", "csv", "tsv",
    "pdf", "tex", "docx", "pptx",
    "json", "md", "txt", "html",
)

_EXT_GROUP = "|".join(ARTIFACT_EXTENSIONS)

# A file reference: an optional path prefix (Windows drive, POSIX root, or
# plain directory components) followed by a name with an artifact extension.
#
# Two exclusions, both from measured misfires on the first run of this
# regex, both in the direction of reading LESS:
#   parentheses are not path characters here -- with them in the stem class,
#     "Excel (.xlsx) hazirlayabilirim" (an offer, naming the FORMAT and no
#     file) matched as the file "(.xlsx";
#   spaces are not path characters either -- with them in the directory
#     class, "C:/out/a.png ve ./sub/b.xlsx" matched as ONE reference,
#     swallowing the prose between two real paths. Real directories with
#     spaces do exist; for those this yields the trailing component, which
#     still matches by basename, whereas swallowing prose does not degrade
#     gracefully at all.
# The stem is also required to be non-empty, which is what makes a bare
# ".xlsx" not a file reference.
_FILE_REF_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[\\/])?"          # optional drive or root
    r"(?:[\w\-.]+[\\/])*"                 # optional directory components
    r"[\w\-.]+"                           # stem
    rf"\.(?:{_EXT_GROUP})\b",
    re.IGNORECASE,
)

# Completion assertions -- curated, closed, per-language. First-person past
# and passive past only.
#
# Split into two classes on purpose, because the two branches of the check
# below need different strengths of evidence:
#
#   SIDE-EFFECT verbs (persistence or transmission: kaydettim, gonderdim,
#     indirdim, "saved", "sent") assert something OUTSIDE the conversation.
#     They are self-anchoring -- you cannot "save" a thing into a chat
#     message -- so on their own they are enough to convict a turn in which
#     no tool ran.
#   GENERIC completion verbs (olusturdum, hazirladim, yazdim, "I created")
#     are not, and this was measured rather than assumed: the very first
#     probe of this detector flagged *"Sizin icin bir liste olusturdum: 1)
#     sut 2) ekmek"* -- a model composing a list inline, which is exactly
#     what it should do and involves no side effect at all. A generic verb
#     therefore only convicts when the response also NAMES A FILE that
#     turns out not to exist.
_SIDE_EFFECT_PATTERNS = [re.compile(p, re.I) for p in (
    r"\bkaydettim\b", r"\bkaydedildi\b", r"\bkay[ıi]t ettim\b",
    r"\bg[öo]nderdim\b", r"\bg[öo]nderildi\b",
    r"\bindirdim\b", r"\bindirildi\b",
    r"\bd[ıi][şs]a aktard[ıi]m\b", r"\baktar[ıi]ld[ıi]\b",
    r"\bdosyaya yazd[ıi]m\b", r"\bdiske yazd[ıi]m\b",
    r"\bI(?:'ve| have)? (?:saved|sent|exported|downloaded)\b",
    r"\b(?:has|have) been (?:saved|sent|exported|downloaded)\b",
    r"\b(?:saved|written|exported) (?:it )?(?:to|at|into)\b",
)]

_GENERIC_COMPLETION_PATTERNS = [re.compile(p, re.I) for p in (
    # Turkish, first person past
    r"\bolu[şs]turdum\b", r"\bhaz[ıi]rlad[ıi]m\b", r"\byazd[ıi]m\b",
    r"\b[çc]izdim\b", r"\bekledim\b", r"\b[üu]rettim\b",
    r"\btamamlad[ıi]m\b", r"\bhallettim\b", r"\byapt[ıi]m\b",
    # Turkish, passive past
    r"\bolu[şs]turuldu\b", r"\bhaz[ıi]rland[ıi]\b", r"\byaz[ıi]ld[ıi]\b",
    r"\b[çc]izildi\b", r"\beklendi\b", r"\b[üu]retildi\b",
    r"\btamamland[ıi]\b", r"\byap[ıi]ld[ıi]\b",
    # English
    r"\bI(?:'ve| have)? (?:created|generated|written|wrote|added|produced|made)\b",
    r"\b(?:has|have) been (?:created|generated|written|added|produced)\b",
    r"\bhere(?:'s| is) the (?:file|chart|report|workbook|spreadsheet)\b",
)]

_COMPLETION_PATTERNS = _SIDE_EFFECT_PATTERNS + _GENERIC_COMPLETION_PATTERNS

# Reused verbatim in spirit from summary.audit_claims: any admission of
# failure anywhere in the response disqualifies the whole thing. A hedged or
# partially-negative answer is the model behaving correctly.
_NEGATION_PATTERNS = [re.compile(p, re.I) for p in (
    r"\bfail\w*\b", r"\berror\b", r"\bcould ?n[o']?t\b", r"\bcannot\b", r"\bcan ?not\b",
    r"\bunable\b", r"\bdid ?n[o']?t\b", r"\bwas not\b", r"\bwere ?n[o']?t\b",
    r"\bno file\b", r"\bnot (?:created|saved|generated|found|available)\b",
    r"ba[şs]ar[ıi]s[ıi]z", r"\bhata\b", r"\byap[ıi]lamad[ıi]\b",
    r"olu[şs]turulamad[ıi]", r"g[öo]nderilemed[ıi]", r"kaydedilemed[ıi]",
    r"\bolmad[ıi]\b", r"bulunamad[ıi]", r"eri[şs]ilemed[ıi]",
    # Deliberately NOT a bare \byok\b. "yok" is one of the most common words
    # in conversational Turkish ("sorun yok", "baska bir sey yok"), so a bare
    # match would let a single throwaway reassurance switch the whole gate
    # off -- a recall hole wide enough to drive the original hallucination
    # through. Anchored to the nouns that make it an actual admission.
    r"\b(?:dosya|dosyas[ıi]|kay[ıi]t|kayd[ıi]|veri|sonu[çc]|[çc][ıi]kt[ıi])\s+yok\b",
)]


class ArtifactEvidence(BaseModel):
    path: str
    exists: bool
    capability: str = ""


class OperationEvidence(BaseModel):
    capability: str
    display_status: str

    @property
    def succeeded(self) -> bool:
        return self.display_status in ("confirmed", "reported_success_unverified")


class EvidenceSet(BaseModel):
    """Everything this turn can actually prove happened.

    facts is declared and deliberately left empty this phase -- the plan
    names it (transaction_count, closing_balance, ...) but no tool emits
    structured facts yet, and an empty dict is the honest representation of
    that. It is NOT populated by scraping numbers out of tool output; see
    this module's docstring.
    """
    artifacts: list[ArtifactEvidence] = []
    operations: list[OperationEvidence] = []
    facts: dict[str, str] = {}

    @property
    def any_operation(self) -> bool:
        return bool(self.operations)

    @property
    def verified_artifact_paths(self) -> list[str]:
        return [a.path for a in self.artifacts if a.exists]


def build_evidence_set(
    summary: VerifiedExecutionSummary | None,
    envelopes: list[dict] | None = None,
) -> EvidenceSet:
    """Turn a turn's verified summary (+ raw envelopes, which carry the
    declared artifact paths) into typed evidence. Never raises on a
    malformed envelope -- same discipline as build_verified_summary."""
    operations = [
        OperationEvidence(capability=op.capability, display_status=op.display_status)
        for op in (summary.operations if summary is not None else [])
    ]
    artifacts: list[ArtifactEvidence] = []
    for raw in envelopes or []:
        if not isinstance(raw, dict):
            continue
        capability = str(raw.get("capability") or "")
        for path in raw.get("artifacts") or []:
            try:
                exists = Path(str(path)).is_file()
            except (OSError, ValueError):
                # ValueError: Windows raises it for embedded nulls / illegal
                # characters, and these strings come from model output.
                exists = False
            artifacts.append(ArtifactEvidence(path=str(path), exists=exists, capability=capability))
    return EvidenceSet(artifacts=artifacts, operations=operations)


def _normalize(path_text: str) -> str:
    return path_text.replace("\\", "/").strip().strip("'\"`.,;:)(").lower()


def file_references(text: str) -> list[str]:
    """Every file this response names. Deduplicated, original spelling kept."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _FILE_REF_RE.finditer(text or ""):
        raw = match.group(0).strip()
        key = _normalize(raw)
        if key and key not in seen:
            seen.add(key)
            out.append(raw)
    return out


def _claims_completion(text: str) -> bool:
    return any(p.search(text) for p in _COMPLETION_PATTERNS)


def _claims_side_effect(text: str) -> bool:
    return any(p.search(text) for p in _SIDE_EFFECT_PATTERNS)


def _admits_failure(text: str) -> bool:
    return any(p.search(text) for p in _NEGATION_PATTERNS)


def _backed_by(reference: str, evidence: EvidenceSet) -> bool:
    """Is this named file backed by something real?

    Backed when it matches a declared artifact of this turn that exists --
    by full normalized path or by basename, since a response legitimately
    shortens "C:/.../data/plots/run-x/chart.png" to "chart.png" -- or when
    the named path simply exists on disk (see the module docstring on why an
    existing file is never called a lie).
    """
    ref = _normalize(reference)
    ref_base = ref.rsplit("/", 1)[-1]
    for artifact in evidence.artifacts:
        if not artifact.exists:
            continue
        declared = _normalize(artifact.path)
        if ref == declared or declared.endswith("/" + ref) or ref.endswith("/" + declared):
            return True
        if ref_base and ref_base == declared.rsplit("/", 1)[-1]:
            return True
    try:
        if Path(reference).is_file():
            return True
    except (OSError, ValueError):
        pass  # unusable as a path -- see build_evidence_set's note
    return False


class ClaimVerdict(BaseModel):
    """Why the gate fired (or that it didn't). `reasons` is written for the
    audit log and for the repair prompt, not for the user."""
    unbacked: bool = False
    unbacked_files: list[str] = []
    zero_operation_claim: bool = False
    reasons: list[str] = []


def detect_unbacked_claims(text: str, evidence: EvidenceSet) -> ClaimVerdict:
    """The decisive check for the zero-tool hallucination class.

    See this module's docstring for the three conditions and why each one is
    there. Returns a clean verdict (unbacked=False) for every response that
    does not provably contradict the evidence -- including every response
    this check simply cannot judge.
    """
    if not text or not text.strip():
        return ClaimVerdict()
    if not _claims_completion(text):
        return ClaimVerdict()
    if _admits_failure(text):
        return ClaimVerdict()

    reasons: list[str] = []
    references = file_references(text)
    unbacked_files = [ref for ref in references if not _backed_by(ref, evidence)]
    if unbacked_files:
        reasons.append(
            "response reports producing " + ", ".join(unbacked_files)
            + " -- not declared by any tool this turn and not present on disk"
        )
    # The zero-operation branch requires that the response named NO file at
    # all -- not merely that no named file was unbacked. A response that
    # names a file which really is there has produced positive evidence, and
    # convicting it because the envelope list happens to be empty (mode
    # switched on mid-conversation, a resumed checkpoint) would be exactly
    # the "provable contradiction" standard this gate is not allowed to
    # relax. Beyond that, only a SIDE-EFFECT verb convicts here; see the
    # verb-class comment above for the measured false positive that ruled
    # out generic completion verbs.
    zero_operation_claim = (
        not references
        and not evidence.any_operation
        and _claims_side_effect(text)
    )
    if zero_operation_claim:
        reasons.append(
            "response asserts something was saved or sent, but no tool ran at all this turn"
        )
    return ClaimVerdict(
        unbacked=bool(reasons),
        unbacked_files=unbacked_files,
        zero_operation_claim=zero_operation_claim,
        reasons=reasons,
    )


def repair_instruction(verdict: ClaimVerdict) -> str:
    """The single bounded repair round's correction (plan item 5). States the
    contradiction as fact and asks for an honest rewrite -- it does not
    supply replacement wording, so the model still answers in the user's own
    language and register."""
    detail = "; ".join(verdict.reasons)
    return (
        "[System verification -- this is ground truth, not a suggestion]\n"
        f"Your draft answer is contradicted by what actually executed: {detail}.\n"
        "Rewrite the answer so it claims ONLY what really happened. Do not name "
        "a file that was not produced, and do not say an action was carried out "
        "when it was not. If the request still needs a tool call or missing "
        "information, say so plainly. Reply in the same language as the user."
    )


def honest_failure_report(verdict: ClaimVerdict, turkish: bool) -> str:
    """Used only when the bounded repair round ALSO produced an unbacked
    claim. Plain, first-person, no invented detail -- the plan's own example
    sentence ("Efendim, istediginiz dosyayi olusturamadim")."""
    if turkish:
        if verdict.unbacked_files:
            return (
                "Efendim, istediğiniz dosyayı oluşturamadım — az önce verdiğim "
                "cevapta bir dosya adı geçiyordu ama o dosya gerçekte üretilmedi. "
                "İsterseniz tekrar deneyeyim."
            )
        return (
            "Efendim, bu turda hiçbir işlem gerçekleştirmedim — verdiğim cevap "
            "yapılmış bir iş varmış gibi görünüyordu, doğrusu bu değil. "
            "İsterseniz şimdi yapayım."
        )
    if verdict.unbacked_files:
        return (
            "I could not produce the file you asked for. My draft answer named a "
            "file that was never actually created, so I am not going to report it "
            "as done. Say the word and I'll try again."
        )
    return (
        "I did not actually carry out any action this turn. My draft answer read "
        "as if I had, which was wrong. Tell me to go ahead and I'll do it now."
    )
