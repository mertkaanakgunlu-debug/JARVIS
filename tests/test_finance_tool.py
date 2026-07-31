"""jarvis/tools/finance.py -- BUG-15 regression.

_sync_burgan()'s msg_ids regex expected a bare "[id]" at the start of a line,
but jarvis/tools/gmail.py's _fmt_message() actually emits "• [id]  Subject"
(bullet-prefixed) -- the regex never matched, so sync always reported zero
messages found regardless of what Gmail actually returned. The subject/body
extraction from the "read" action had the same root cause: it looked for a
"Konu:"/"---" format that _fmt_message() has never produced.

Mocks only the real external boundary (the Google API service object) and
the LLM extraction call -- everything else (gmail_control's real formatting,
finance.py's real regex parsing, FinanceStore's real SQLite writes) runs for
real, so this fails if the regex/format contract drifts again.
"""
from __future__ import annotations

import base64
from types import SimpleNamespace

from jarvis.tools import finance


_SUBJECT = "Burgan Bank - Odeme Bildirimi"
_BODY_TEXT = "Hesabinizdan 150.00 TRY tutarinda odeme yapilmistir. Uye isyeri: MIGROS"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _fake_full_message(msg_id: str) -> dict:
    return {
        "id": msg_id,
        "snippet": _BODY_TEXT[:50],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": _SUBJECT},
                {"name": "From", "value": "bilgi@burgan.com.tr"},
                {"name": "Date", "value": "Wed, 15 Jul 2026 10:00:00 +0300"},
            ],
            "body": {"data": _b64(_BODY_TEXT)},
        },
    }


class _FakeMessages:
    def __init__(self, msg_id: str):
        self._msg_id = msg_id
        # Call counters: the structured path must fetch each message ONCE.
        # The old sync searched (which already fetched format="full" per hit),
        # threw the payload away, then re-fetched every message with a separate
        # `read` -- 2N round trips to recover data it had just discarded.
        self.list_calls = 0
        self.get_calls = 0

    def list(self, **kwargs):
        self.list_calls += 1
        self.last_query = kwargs.get("q", "")
        return SimpleNamespace(execute=lambda: {"messages": [{"id": self._msg_id}]})

    def get(self, **kwargs):
        self.get_calls += 1
        return SimpleNamespace(execute=lambda: _fake_full_message(kwargs["id"]))


class _FakeUsers:
    def __init__(self, msg_id: str):
        self._messages = _FakeMessages(msg_id)

    def messages(self):
        return self._messages


class _FakeService:
    def __init__(self, msg_id: str = "18f2a3b4c5d6e7f8"):
        self._users = _FakeUsers(msg_id)

    def users(self):
        return self._users


def test_sync_finds_and_saves_a_message(monkeypatch, isolated_cwd):
    """The structured path delivers subject/body/date to the extractor intact.

    Updated 2026-07-30: sync no longer regexes gmail_control's display output
    (BUG-15's root cause) -- it consumes gmail.search_messages(). The original
    assertions are kept because they still pin the thing that mattered: the
    extractor must receive the real subject and the real body, with no display
    formatting bleeding into either.
    """
    from jarvis.tools import gmail as gmail_tool
    from jarvis import finance_extractor
    from jarvis.finance_parser import ParsedTransaction

    monkeypatch.setattr(gmail_tool, "_get_service", lambda settings: _FakeService())

    captured: dict = {}

    async def fake_extract_transaction(subject, body, settings, date_header=""):
        captured["subject"] = subject
        captured["body"] = body
        captured["date_header"] = date_header
        return ParsedTransaction(
            date="2026-07-15T10:00:00", amount=-150.0, currency="TRY",
            merchant="MIGROS", category="shopping", description="Odeme",
            direction="expense",
        )

    monkeypatch.setattr(finance_extractor, "extract_transaction", fake_extract_transaction)

    settings = SimpleNamespace()
    result = finance.finance_control(action="sync", settings=settings)

    assert "1 işlem kaydedildi" in result, result
    assert captured["subject"] == _SUBJECT
    assert _BODY_TEXT in captured["body"]
    assert "•" not in captured["body"]  # display formatting must not leak into body
    # The RFC-2822 header reaches the extractor, which is what lets a mail with
    # no in-body date be dated from real evidence instead of from now().
    assert "2026" in captured["date_header"]

    store = finance._get_store(settings)
    saved = store.recent_transactions(limit=5)
    assert len(saved) == 1
    assert saved[0]["merchant"] == "MIGROS"


def test_sync_fetches_each_message_once_not_twice(monkeypatch, isolated_cwd):
    """N API calls, not 2N.

    The old sync searched (fetching format="full" for every hit), discarded the
    payloads, then issued a second per-message `read` to recover them. On a real
    month of notifications that doubled the Gmail quota cost for nothing.
    """
    from jarvis.tools import gmail as gmail_tool
    from jarvis import finance_extractor
    from jarvis.finance_parser import ParseRejection

    service = _FakeService()
    monkeypatch.setattr(gmail_tool, "_get_service", lambda settings: service)

    async def reject(subject, body, settings, date_header=""):
        return ParseRejection("not_a_transaction", "test")

    monkeypatch.setattr(finance_extractor, "extract_transaction", reject)
    finance.finance_control(action="sync", settings=SimpleNamespace())

    messages = service.users().messages()
    assert messages.get_calls == 1, f"one message, {messages.get_calls} fetches"


def test_sync_applies_months_back_as_a_real_date_bound(monkeypatch, isolated_cwd):
    """months_back was declared, passed down, and then never used -- the query
    was a bare `from:burgan` over the entire mailbox."""
    from jarvis.tools import gmail as gmail_tool
    from jarvis import finance_extractor
    from jarvis.finance_parser import ParseRejection

    service = _FakeService()
    monkeypatch.setattr(gmail_tool, "_get_service", lambda settings: service)

    async def reject(subject, body, settings, date_header=""):
        return ParseRejection("not_a_transaction", "test")

    monkeypatch.setattr(finance_extractor, "extract_transaction", reject)
    finance.finance_control(action="sync", months_back=1, settings=SimpleNamespace())

    query = service.users().messages().last_query
    assert "after:" in query, f"no date bound in the query: {query!r}"
    assert "from:" in query


def test_sync_reports_rejection_reasons_not_an_anonymous_tally(monkeypatch, isolated_cwd):
    """'12 atlandı' tells the owner nothing; naming the reasons points at the
    actual problem (bad filter vs unparseable format vs dead extractor)."""
    from jarvis.tools import gmail as gmail_tool
    from jarvis import finance_extractor
    from jarvis.finance_parser import ParseRejection

    monkeypatch.setattr(gmail_tool, "_get_service", lambda settings: _FakeService())

    async def reject(subject, body, settings, date_header=""):
        return ParseRejection("no_date", "no date anywhere")

    monkeypatch.setattr(finance_extractor, "extract_transaction", reject)
    result = finance.finance_control(action="sync", settings=SimpleNamespace())

    assert "no_date" in result, result
    assert "atlandı" in result


def test_sync_surfaces_an_auth_error_instead_of_reporting_zero(monkeypatch, isolated_cwd):
    """A dead Gmail token must not look like an empty mailbox -- that is exactly
    how the revoked-token incident stayed invisible."""
    from jarvis.tools import gmail as gmail_tool

    def _dead(settings):
        raise RuntimeError("Gmail authorization is no longer valid (invalid_grant)")

    monkeypatch.setattr(gmail_tool, "_get_service", _dead)
    result = finance.finance_control(action="sync", settings=SimpleNamespace())

    assert result.startswith("[ERROR]"), result
    assert "invalid_grant" in result


def test_sync_reports_none_found_when_no_messages_match(monkeypatch, isolated_cwd):
    from jarvis.tools import gmail as gmail_tool

    class _EmptyMessages:
        def list(self, **kwargs):
            return SimpleNamespace(execute=lambda: {})

    class _EmptyUsers:
        def messages(self):
            return _EmptyMessages()

    class _EmptyService:
        def users(self):
            return _EmptyUsers()

    monkeypatch.setattr(gmail_tool, "_get_service", lambda settings: _EmptyService())

    result = finance.finance_control(action="sync", settings=SimpleNamespace())
    assert "bulunamadı" in result
