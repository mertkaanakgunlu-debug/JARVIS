"""finance_extractor: local-first, evidence-validated, and stingy with inference.

Three properties are pinned here, each one a bug that actually happened:

1. **It works with no cloud.** It used to be gated by cloud_extractors_enabled(),
   true only for CLOUD_POLICY=auto. Under this project's default ("off") it
   returned None for every mail, so finance('sync') was a permanent no-op that
   said "0 islem kaydedildi" and never mentioned that extraction was disabled.

2. **It never accepts a value the mail does not contain.** The old prompt said
   "make your best guess" and the old schema said "use today's date if not
   found". Now a model-supplied amount whose digits appear nowhere in the mail,
   or a date the mail cannot support, is refused.

3. **It does not spend inference on provably hopeless mails.** A no_amount or
   no_date rejection cannot be rescued by a model, because property 2 would
   reject whatever the model returned. Escalating anyway cost 5.2s and 1.8s of
   local inference on the fixture -- enough, under real turn contention, to push
   finance('sync') past its 60s tool timeout. The worker thread still committed
   7 transactions while the awaiting side was cancelled, so the call left no
   execution_end and no trace row, and the model never learned it had worked.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.finance_extractor import (
    REJECT_LLM_NO_EVIDENCE,
    _amount_appears_in,
    _date_is_supported,
    extract_transaction,
)
from jarvis.finance_parser import (
    REJECT_NO_AMOUNT,
    REJECT_NO_DATE,
    REJECT_NOT_A_TRANSACTION,
    ParsedTransaction,
    ParseRejection,
)

_SETTINGS = SimpleNamespace(cloud_policy="off", local_model="qwen3:8b")

_GOOD_BODY = (
    "07.07.2026 14:32 tarihinde kartiniz ile MIGROS TICARET AS isyerinde "
    "250,75 TL tutarinda alisveris islemi gerceklestirilmistir."
)


class _ExplodingLLM:
    """Any use of this is a test failure: it proves an LLM call was made."""

    def __init__(self):
        self.calls = 0

    def with_structured_output(self, *a, **k):
        self.calls += 1
        raise AssertionError("the extractor made an LLM call it should not have")


@pytest.fixture
def no_llm_allowed(monkeypatch):
    llm = _ExplodingLLM()
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *a, **k: llm)
    return llm


# ── 1. works offline ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parses_without_any_model_call(no_llm_allowed):
    result = await extract_transaction("Burgan - Islem", _GOOD_BODY, _SETTINGS)

    assert isinstance(result, ParsedTransaction)
    assert result.amount == pytest.approx(-250.75)
    assert no_llm_allowed.calls == 0, "a parseable mail must not reach the model"


# ── 3. no inference on hopeless mails ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_amount_does_not_escalate_to_the_model(no_llm_allowed):
    result = await extract_transaction(
        "Burgan - Islem Bilgilendirmesi",
        "16.07.2026 tarihinde kartiniz ile bir alisveris islemi "
        "gerceklestirilmistir. Detaylar icin uygulamayi kullaniniz.",
        _SETTINGS,
    )

    assert isinstance(result, ParseRejection)
    assert result.reason == REJECT_NO_AMOUNT, (
        "the reason must stay the accurate parser reason, not an LLM-laundered one"
    )
    assert no_llm_allowed.calls == 0


@pytest.mark.asyncio
async def test_no_date_does_not_escalate_to_the_model(no_llm_allowed):
    result = await extract_transaction(
        "Burgan - Kartli Islem",
        "Kartiniz ile CARREFOUR isyerinde 675,25 TL tutarinda alisveris "
        "islemi gerceklestirilmistir.",
        _SETTINGS,
        date_header="",
    )

    assert isinstance(result, ParseRejection)
    assert result.reason == REJECT_NO_DATE
    assert no_llm_allowed.calls == 0


@pytest.mark.asyncio
async def test_marketing_mail_does_not_escalate_to_the_model(no_llm_allowed):
    result = await extract_transaction(
        "Size Ozel Kredi Firsati!",
        "100.000 TL'ye varan ihtiyac kredisi firsatini kacirmayin. "
        "Kampanya kosullari icin web sitemizi ziyaret edin.",
        _SETTINGS,
    )

    assert isinstance(result, ParseRejection)
    assert result.reason == REJECT_NOT_A_TRANSACTION
    assert no_llm_allowed.calls == 0


# ── 2. evidence validation of model output ────────────────────────────────────

@pytest.mark.parametrize("amount,present", [
    (250.75, True),      # "250,75 TL"
    (250.0, True),        # the whole part alone is written
    (99999.99, False),    # nowhere in the mail
    (251.0, False),
])
def test_amount_must_appear_in_the_mail(amount, present):
    assert _amount_appears_in(_GOOD_BODY, amount) is present


def test_amount_matching_is_grouping_insensitive():
    """1.850,00 / 1850,00 / 1850.00 are the same written figure."""
    body = "1.850,00 TL tutarinda"
    assert _amount_appears_in(body, 1850.00) is True


@pytest.mark.parametrize("iso,ok", [
    ("2026-07-07T14:32:00", True),    # 07.07.2026 is in the body
    ("2026-07-09T00:00:00", False),   # plausible, but not written anywhere
    ("", False),
    ("not-a-date", False),
])
def test_date_must_be_evidenced_by_the_mail(iso, ok):
    assert _date_is_supported(_GOOD_BODY, "", iso) is ok


def test_date_header_counts_as_evidence():
    """A real fallback: the header is when the bank sent the notification."""
    assert _date_is_supported(
        "no date in this body", "Thu, 16 Jul 2026 12:30:00 +0300", "2026-07-16T12:30:00"
    ) is True


@pytest.mark.asyncio
async def test_a_hallucinated_amount_is_refused(monkeypatch):
    """The escalation path's guard: a mail ambiguous only in DIRECTION reaches the
    model, and a model that then invents an amount must not be believed."""
    class _Structured:
        async def ainvoke(self, _prompt):
            return SimpleNamespace(
                sufficient_evidence=True,
                date="2026-07-07T14:32:00",
                amount=99999.99,            # nowhere in the mail
                currency="TRY",
                direction="expense",
                merchant="MIGROS",
            )

    monkeypatch.setattr(
        "jarvis.providers.get_llm",
        lambda *a, **k: SimpleNamespace(with_structured_output=lambda *x, **y: _Structured()),
    )
    # Amount and date present, direction deliberately ambiguous -> escalates.
    body = "07.07.2026 14:32 tarihinde MIGROS islemi 250,75 TL"
    result = await extract_transaction("Islem", body, _SETTINGS)

    assert isinstance(result, ParseRejection)
    assert result.reason == REJECT_LLM_NO_EVIDENCE


@pytest.mark.asyncio
async def test_a_model_that_declines_is_respected(monkeypatch):
    class _Structured:
        async def ainvoke(self, _prompt):
            return SimpleNamespace(
                sufficient_evidence=False, date="", amount=0.0,
                currency="", direction="", merchant="",
            )

    monkeypatch.setattr(
        "jarvis.providers.get_llm",
        lambda *a, **k: SimpleNamespace(with_structured_output=lambda *x, **y: _Structured()),
    )
    body = "07.07.2026 14:32 tarihinde MIGROS islemi 250,75 TL"
    result = await extract_transaction("Islem", body, _SETTINGS)

    assert isinstance(result, ParseRejection)


@pytest.mark.asyncio
async def test_a_model_failure_is_a_rejection_not_a_crash(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("ollama is down")

    monkeypatch.setattr("jarvis.providers.get_llm", _boom)
    body = "07.07.2026 14:32 tarihinde MIGROS islemi 250,75 TL"
    result = await extract_transaction("Islem", body, _SETTINGS)

    assert isinstance(result, ParseRejection)
    assert result.detail


@pytest.mark.asyncio
async def test_never_returns_none(no_llm_allowed):
    """The old contract returned None for three different situations -- "not a
    transaction", "unreadable", and "extraction is switched off" -- which need
    different reactions. Callers now always get one or the other dataclass."""
    for subject, body in [
        ("", ""),
        ("Burgan", "merhaba"),
        ("Burgan - Islem", _GOOD_BODY),
    ]:
        out = await extract_transaction(subject, body, _SETTINGS)
        assert isinstance(out, (ParsedTransaction, ParseRejection))
