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

    def list(self, **kwargs):
        return SimpleNamespace(execute=lambda: {"messages": [{"id": self._msg_id}]})

    def get(self, **kwargs):
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
    from jarvis.tools import gmail as gmail_tool
    from jarvis import finance_extractor

    monkeypatch.setattr(gmail_tool, "_get_service", lambda settings: _FakeService())

    captured: dict = {}

    async def fake_extract_transaction(subject, body, settings):
        captured["subject"] = subject
        captured["body"] = body
        return SimpleNamespace(
            date="2026-07-15T10:00:00", amount=-150.0, currency="TRY",
            merchant="MIGROS", category="shopping", description="Odeme",
        )

    monkeypatch.setattr(finance_extractor, "extract_transaction", fake_extract_transaction)

    settings = SimpleNamespace()
    result = finance.finance_control(action="sync", settings=settings)

    assert "1 işlem kaydedildi" in result, result
    # BUG-15: before the fix, msg_ids was always [] and neither of these
    # assertions below would ever be reached -- sync short-circuited at
    # "Burgan bildirimi bulunamadı" instead.
    assert captured["subject"] == _SUBJECT
    assert _BODY_TEXT in captured["body"]
    assert "•" not in captured["body"]  # header/bullet line must not leak into body

    store = finance._get_store(settings)
    saved = store.recent_transactions(limit=5)
    assert len(saved) == 1
    assert saved[0]["merchant"] == "MIGROS"


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
