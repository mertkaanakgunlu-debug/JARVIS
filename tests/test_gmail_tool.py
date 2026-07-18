"""jarvis/tools/gmail.py -- audit-outcome regression (live-found 2026-07-18).

gmail_control used [Gmail] to prefix BOTH success ("Email sent to...") and
failure (missing credentials, validation errors) -- content_is_failure() can't
add "[Gmail]" to _FAILURE_PREFIXES without misjudging real successes too, so
these calls were silently logged ok:true in the audit even after the Faz 1.1
fix (which correctly reads .content, but still needs a real failure-shaped
prefix to recognize). Errors now use [ERROR].

_get_service() requires real Google OAuth credentials, so these tests
monkeypatch it with a fake service, same pattern as test_calendar_tool.py.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.graph.tool_accounting import content_is_failure
from jarvis.tools import gmail


def _settings():
    return SimpleNamespace(google_calendar_creds_file="unused")


def test_missing_credentials_returns_error_prefix(monkeypatch):
    def _raise(settings):
        raise RuntimeError("Google OAuth credentials not found at 'x'.")
    monkeypatch.setattr(gmail, "_get_service", _raise)

    result = gmail.gmail_control(action="list_unread", settings=_settings())

    assert result.startswith("[ERROR]")
    assert content_is_failure(result) is True


def test_send_without_required_fields_returns_error_prefix(monkeypatch):
    monkeypatch.setattr(gmail, "_get_service", lambda settings: SimpleNamespace())

    result = gmail.gmail_control(action="send", settings=_settings())  # no to/subject/body

    assert result.startswith("[ERROR]")
    assert content_is_failure(result) is True


def test_unknown_action_returns_error_prefix(monkeypatch):
    monkeypatch.setattr(gmail, "_get_service", lambda settings: SimpleNamespace())

    result = gmail.gmail_control(action="not_a_real_action", settings=_settings())

    assert result.startswith("[ERROR]")


def test_successful_send_still_uses_gmail_prefix_not_error(monkeypatch):
    """The fix must not turn genuine successes into failures."""
    class _FakeMessages:
        def getProfile(self, userId):
            return SimpleNamespace(execute=lambda: {"emailAddress": "me@example.com"})

        def messages(self):
            return self

        def send(self, userId, body):
            return SimpleNamespace(execute=lambda: {"id": "abc123"})

    class _FakeUsers:
        def users(self):
            return _FakeMessages()

    monkeypatch.setattr(gmail, "_get_service", lambda settings: _FakeUsers())

    result = gmail.gmail_control(
        action="send", to="x@example.com", subject="hi", body="hello",
        settings=_settings(),
    )

    assert result.startswith("[Gmail] Email sent")
    assert content_is_failure(result) is False
