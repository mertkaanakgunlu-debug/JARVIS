"""Transaction extraction: deterministic parser first, local LLM as fallback.

Rewritten 2026-07-30. What it was, and why every part of that had to change:

  * **Cloud-only.** It built ChatGoogleGenerativeAI directly and was gated by
    ``cloud_extractors_enabled()``, which is true only for CLOUD_POLICY=auto.
    This project's default is ``off``, so the gate returned False, the extractor
    returned None for every mail, and ``finance('sync')`` was a permanent no-op
    that reported "0 islem kaydedildi" without a word about why. The
    transactions table had 0 rows.
  * **Instructed to guess.** The schema's date field said "Use today's date if
    not found" and the prompt said "Bilgi bulunamazsa en iyi tahmini yap" (make
    your best guess). A mail whose date could not be read became a transaction
    dated today; a mail with no readable amount became a plausible number. That
    is silent ledger corruption, not graceful degradation.
  * **LLM-first for template mail.** Bank notifications are templates. A regex
    reads them reproducibly, for free, offline, and cannot invent a number.

The design now:

  1. jarvis/finance_parser.parse_transaction() -- deterministic, primary.
  2. Only if that cannot resolve a mail that nonetheless LOOKS like a
     transaction, one local LLM call (get_llm("fast"), so Ollama first and cloud
     only as a configured fallback tier -- no CLOUD_POLICY gate needed).
  3. **Every value the model returns is validated against text actually present
     in the mail.** An amount whose digits appear nowhere in the message, or a
     date matching neither the body nor the Date header, is refused. The model is
     allowed to interpret; it is not allowed to supply facts.
  4. Anything still unresolved is a ParseRejection carrying a reason. Callers
     report rejections; they are never swallowed.

Return contract: ParsedTransaction | ParseRejection. Never None, never raises --
"None" was ambiguous between "not a transaction", "could not read it" and
"extraction is switched off entirely", and those need different reactions.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from jarvis.finance_parser import (
    REJECT_NO_AMOUNT,
    REJECT_NO_DATE,
    REJECT_NOT_A_TRANSACTION,
    ParsedTransaction,
    ParseRejection,
    categorize,
    looks_like_transaction,
    parse_transaction,
)

logger = logging.getLogger(__name__)

REJECT_LLM_UNAVAILABLE = "llm_unavailable"
REJECT_LLM_NO_EVIDENCE = "llm_value_not_in_mail"
REJECT_LLM_DECLINED = "llm_insufficient_evidence"

try:
    from pydantic import BaseModel, Field

    class TransactionExtract(BaseModel):
        """Note what is NOT here: any instruction to guess, infer or default.

        ``sufficient_evidence`` exists so the model has an honest way to say no.
        Without it, a model asked for a required float will always produce one.
        """

        sufficient_evidence: bool = Field(
            ...,
            description=(
                "True ONLY if the mail states an explicit transaction amount AND "
                "a date AND makes clear whether money left or entered the "
                "account. False if any of those is absent. Do not infer or "
                "estimate them."
            ),
        )
        date: str = Field(
            default="",
            description=(
                "Transaction date/time as it appears in the mail, ISO 8601 "
                "(YYYY-MM-DDTHH:MM:SS). Empty if the mail does not state one. "
                "NEVER substitute today's date."
            ),
        )
        amount: float = Field(
            default=0.0,
            description=(
                "Absolute transaction amount exactly as written in the mail, no "
                "sign. Turkish format: 1.234,56 means 1234.56. 0 if absent."
            ),
        )
        currency: str = Field(
            default="",
            description="Currency code from the mail: TRY (TL), USD, EUR, GBP. Empty if absent.",
        )
        direction: Literal["expense", "income", ""] = Field(
            default="",
            description=(
                "'expense' if money left the account, 'income' if it entered. "
                "Empty if the mail does not make this clear."
            ),
        )
        merchant: str = Field(
            default="", description="Merchant/payee/counterparty name from the mail. Empty if absent."
        )

    _HAS_PYDANTIC = True

except ImportError:  # pragma: no cover -- pydantic is a hard dependency in practice
    _HAS_PYDANTIC = False
    TransactionExtract = None  # type: ignore


_EXTRACT_PROMPT = """Sen bir Türk bankası bildirim maili okuyucusun.

GÖREV: Aşağıdaki mailde AÇIKÇA YAZAN işlem bilgilerini çıkar.

KESİN KURAL: Hiçbir değeri tahmin etme, çıkarsama veya varsayma.
- Mailde tarih yazmıyorsa date alanını boş bırak. Bugünün tarihini ASLA yazma.
- Mailde tutar yazmıyorsa amount = 0 bırak.
- Para giriş/çıkış yönü belli değilse direction alanını boş bırak.
- Bu üç bilgiden herhangi biri eksikse sufficient_evidence = false yap.

Eksik bilgiyi tamamlamak senin işin DEĞİL. Eksik bilgiyi bildirmek senin işin.

Konu: {subject}
---
{body}
---
"""


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _amount_appears_in(text: str, amount: float) -> bool:
    """Is this amount actually written in the mail?

    Compares digit sequences, so 1.234,56 / 1234,56 / 1234.56 all match the same
    written figure while a number the model invented matches nothing. This is the
    check that makes a hallucinated amount unusable rather than merely unlikely.
    """
    haystack = _digits(text)
    if not haystack:
        return False
    whole = int(abs(amount))
    cents = round((abs(amount) - whole) * 100)
    candidates = {f"{whole}{cents:02d}", str(whole)}
    if cents == 0:
        candidates.add(f"{whole}00")
    return any(c and c in haystack for c in candidates)


def _date_is_supported(text: str, date_header: str, iso: str) -> bool:
    """Is this date evidenced by the mail body or its Date header?"""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return False
    year, month, day = m.group(1), m.group(2), m.group(3)
    # Any ordering the bank might use, with or without zero padding.
    variants = {
        f"{day}.{month}.{year}", f"{int(day)}.{int(month)}.{year}",
        f"{day}/{month}/{year}", f"{year}-{month}-{day}",
    }
    if any(v in text for v in variants):
        return True
    if date_header:
        from jarvis.finance_parser import _find_datetime
        header_iso = _find_datetime("", "", date_header)
        if header_iso and header_iso[:10] == iso[:10]:
            return True
    return False


async def extract_transaction(
    subject: str,
    body: str,
    settings,
    date_header: str = "",
) -> ParsedTransaction | ParseRejection:
    """Extract one transaction, or explain why it could not be extracted."""
    # 1. Deterministic first.
    result = parse_transaction(subject, body, date_header)
    if isinstance(result, ParsedTransaction):
        return result

    # 2. Escalate to the LLM ONLY when it could actually help.
    #
    # A mail that isn't a transaction gets no call at all -- marketing mail is
    # the case that matters, since it contains amounts ("100.000 TL'ye varan
    # kredi") a model will happily return as a transaction.
    #
    # More subtly, REJECT_NO_AMOUNT and REJECT_NO_DATE are *provably*
    # unrecoverable, so escalating them is guaranteed waste rather than a
    # long shot: step 4 below refuses any amount whose digits do not appear in
    # the mail, and any date not evidenced by the body or the Date header. If the
    # parser found neither, no model answer can pass that validation -- the only
    # possible outcomes are REJECT_LLM_NO_EVIDENCE (the model guessed, caught) or
    # REJECT_LLM_DECLINED (the model admitted it). Both are the rejection we
    # already have.
    #
    # This is not a micro-optimisation. Measured on the 12-mail fixture, those
    # two mails cost 5.2s and 1.8s of local inference in isolation; inside a real
    # turn, where the same Ollama instance is also serving the orchestrator, they
    # pushed finance('sync') past its 60s ToolSpec timeout. The tool's worker
    # thread finished and committed 7 transactions, but the awaiting side was
    # cancelled -- so the call produced no execution_end and no tool_trace row,
    # and the model never learned the sync had succeeded. Removing the pointless
    # calls removes the timeout with it.
    if result.reason in (REJECT_NOT_A_TRANSACTION, REJECT_NO_AMOUNT, REJECT_NO_DATE):
        return result
    if not looks_like_transaction(subject, body):
        return ParseRejection(REJECT_NOT_A_TRANSACTION, "failed the transaction pre-check")
    if not _HAS_PYDANTIC:
        return ParseRejection(REJECT_LLM_UNAVAILABLE, "pydantic not installed")

    # 3. One local LLM call for the residue the parser could not read.
    try:
        from jarvis.providers import get_llm

        llm = get_llm("fast", settings, max_output_tokens=512)
        # function_calling, not json_schema: tool-calling is the mode this
        # project has proven reliable on qwen3:8b, and json_schema support on
        # Ollama's OpenAI-compatible endpoint is version-dependent.
        structured = llm.with_structured_output(
            TransactionExtract, method="function_calling"
        )
        extracted = await structured.ainvoke(
            _EXTRACT_PROMPT.format(subject=subject[:200], body=body[:2000])
        )
    except Exception as exc:  # noqa: BLE001 -- any model/transport failure is a rejection
        logger.debug("LLM extraction failed: %s", exc)
        return ParseRejection(REJECT_LLM_UNAVAILABLE, f"{type(exc).__name__}: {exc}"[:160])

    if extracted is None:
        return ParseRejection(REJECT_LLM_UNAVAILABLE, "model returned nothing")

    # 4. Validate. The model may interpret; it may not supply facts.
    if not getattr(extracted, "sufficient_evidence", False):
        return ParseRejection(REJECT_LLM_DECLINED, "model reported insufficient evidence")

    text = f"{subject}\n{body}"
    amount = abs(float(getattr(extracted, "amount", 0.0) or 0.0))
    direction = (getattr(extracted, "direction", "") or "").strip()
    currency = (getattr(extracted, "currency", "") or "").strip().upper()
    iso = (getattr(extracted, "date", "") or "").strip()

    if amount <= 0:
        return ParseRejection(REJECT_NO_AMOUNT, "model gave no positive amount")
    if not _amount_appears_in(text, amount):
        return ParseRejection(
            REJECT_LLM_NO_EVIDENCE, f"amount {amount} does not appear in the mail"
        )
    if direction not in ("expense", "income"):
        return ParseRejection(REJECT_LLM_NO_EVIDENCE, "model gave no usable direction")
    if not _date_is_supported(text, date_header, iso):
        return ParseRejection(
            REJECT_LLM_NO_EVIDENCE, f"date {iso!r} is not evidenced by the mail"
        )
    if currency == "TL":
        currency = "TRY"
    if currency not in ("TRY", "USD", "EUR", "GBP"):
        return ParseRejection(REJECT_LLM_NO_EVIDENCE, f"unusable currency {currency!r}")

    merchant = (getattr(extracted, "merchant", "") or "").strip()[:60]
    signed = amount if direction == "income" else -amount
    return ParsedTransaction(
        date=iso,
        amount=round(signed, 2),
        currency=currency,
        merchant=merchant,
        category=categorize(merchant, text, direction),
        description=(subject or merchant or "")[:120],
        direction=direction,
    )
