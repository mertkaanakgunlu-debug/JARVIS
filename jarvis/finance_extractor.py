"""LLM-based bank mail extractor for JARVIS (Faz 16).

Uses Gemini Flash with structured output to extract transaction details from
Turkish bank notification emails (starting with Burgan Bank).

No bank-specific parsers — the LLM reads the raw subject + body and fills a
Pydantic schema.  Falls back to None on any extraction failure so callers can
skip unparseable mails gracefully.
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

# ── Pydantic schema ────────────────────────────────────────────────────────────

try:
    from pydantic import BaseModel, Field

    class TransactionExtract(BaseModel):
        date: str = Field(
            ...,
            description="Transaction date and time in ISO format YYYY-MM-DDTHH:MM:SS. "
                        "Use today's date if not found.",
        )
        amount: float = Field(
            ...,
            description="Transaction amount in TRY (Turkish Lira). "
                        "Negative for expenses/debit, positive for income/credit.",
        )
        currency: str = Field(default="TRY", description="Currency code (TRY, USD, EUR).")
        merchant: str = Field(
            default="",
            description="Merchant or payee name (e.g. MIGROS, AMAZON, ATM). Empty if unknown.",
        )
        category: Literal[
            "food", "transport", "entertainment", "bills", "salary", "transfer", "atm",
            "shopping", "health", "education", "other"
        ] = Field(
            default="other",
            description="Category inferred from merchant and description.",
        )
        description: str = Field(
            default="",
            description="Short human-readable description of the transaction (max 100 chars).",
        )
        transaction_type: Literal["expense", "income", "transfer"] = Field(
            default="expense",
            description="'income' for credits/deposits, 'expense' for debits, 'transfer' for fund transfers.",
        )

    _HAS_PYDANTIC = True

except ImportError:
    _HAS_PYDANTIC = False
    TransactionExtract = None  # type: ignore


# ── Extractor ─────────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
Sen bir Türk bankası mail ayrıştırma asistanısın. Aşağıdaki e-posta başlığı ve içeriğinden
işlem bilgilerini çıkar:

Konu: {subject}
---
{body}
---

Lütfen işlem tarihini, tutarını (TRY cinsinden, gider için negatif), kart/hesap sahibini,
tüccar adını ve kategoriyi çıkar. Bilgi bulunamazsa en iyi tahmini yap.
"""


async def extract_transaction(
    subject: str,
    body: str,
    settings,
) -> "TransactionExtract | None":
    """Extract a TransactionExtract from a bank notification email.

    Returns None if extraction fails or pydantic is unavailable.
    """
    if not _HAS_PYDANTIC:
        logger.warning("pydantic not available — skipping transaction extraction")
        return None

    from jarvis.providers import cloud_extractors_enabled, note_degraded
    if not cloud_extractors_enabled(settings):
        note_degraded("finance_extractor")
        return None

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        model_id = getattr(settings, "finance_extractor_model", "gemini-2.5-flash")
        llm = ChatGoogleGenerativeAI(
            model=model_id,
            google_api_key=getattr(settings, "gemini_api_key", ""),
            temperature=getattr(settings, "finance_extractor_temperature", 0.0),
        )
        structured = llm.with_structured_output(TransactionExtract)
        prompt = _EXTRACT_PROMPT.format(
            subject=subject[:200],
            body=body[:2000],
        )
        result = await structured.ainvoke(prompt)
        return result  # type: ignore[return-value]
    except Exception as exc:
        logger.debug("Transaction extraction failed: %s", exc)
        return None


async def extract_transaction_sync_wrapper(subject: str, body: str, settings) -> "TransactionExtract | None":
    """Sync-friendly wrapper — use _run_coro() from tools context."""
    return await extract_transaction(subject, body, settings)
