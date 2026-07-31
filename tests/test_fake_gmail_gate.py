"""The JSON-fixture Gmail backend must be reachable ONLY under the test profile.

The fixture service exists so the MVP chain's "read my mail" step is testable
offline (no token, no network, repeatable). That convenience is also a hazard:
anything that can swap the real mailbox for a file is a supply-chain-shaped
risk if it can be switched on outside a test run.

The contract pinned here:
  * BOTH JARVIS_TEST_MODE=1 and JARVIS_FAKE_GMAIL_FIXTURE are required -- either
    alone falls through to the real OAuth path,
  * the fixture drives the REAL gmail_control/_fmt_message/_extract_text code
    (the network is faked, not the parsing under test),
  * `from:` and `after:` actually filter -- a fixture backend that ignored the
    query would make every finance-sync test vacuous, and
  * duplicate uids survive to the caller, because dedup is downstream's job to
    prove.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from jarvis.tools import gmail
from scripts.seed_finance_fixture import build_fixture


def _settings():
    return SimpleNamespace(google_calendar_creds_file="data/calendar_credentials.json")


@pytest.fixture
def fixture_file(tmp_path):
    path = tmp_path / "burgan_mails.json"
    path.write_text(
        json.dumps(build_fixture(2026, 7), ensure_ascii=False), encoding="utf-8"
    )
    return path


@pytest.fixture
def fake_gmail(monkeypatch, fixture_file):
    monkeypatch.setenv("JARVIS_TEST_MODE", "1")
    monkeypatch.setenv("JARVIS_FAKE_GMAIL_FIXTURE", str(fixture_file))
    return fixture_file


# ── gating ────────────────────────────────────────────────────────────────────

def test_fixture_ignored_without_test_mode(monkeypatch, fixture_file, jarvis_home):
    """The fixture env var alone must NOT redirect a production run."""
    monkeypatch.delenv("JARVIS_TEST_MODE", raising=False)
    monkeypatch.setenv("JARVIS_FAKE_GMAIL_FIXTURE", str(fixture_file))

    # No credentials exist under the isolated home, so the real path is proven
    # to have been taken by the credentials error it raises.
    with pytest.raises(RuntimeError, match="credentials not found"):
        gmail._get_service(_settings())


def test_fixture_ignored_without_fixture_path(monkeypatch, jarvis_home):
    monkeypatch.setenv("JARVIS_TEST_MODE", "1")
    monkeypatch.delenv("JARVIS_FAKE_GMAIL_FIXTURE", raising=False)

    with pytest.raises(RuntimeError, match="credentials not found"):
        gmail._get_service(_settings())


# ── behavior ──────────────────────────────────────────────────────────────────

def test_search_runs_the_real_formatting_path(fake_gmail):
    out = gmail.gmail_control(
        action="search", query="from:burgan", max_results=25, settings=_settings()
    )

    assert not out.startswith("[ERROR]")
    ids = re.findall(r"^•\s*\[([A-Za-z0-9-]+)\]", out, re.MULTILINE)
    # 12 fixture entries, but two share an id -- the service must not collapse
    # them, so the caller still sees every delivered message.
    assert len(ids) == 12
    # Subjects come from the real _fmt_message() header path.
    assert "Kartli Islem Bilgilendirmesi" in out


def test_from_filter_actually_filters(fake_gmail):
    out = gmail.gmail_control(
        action="search", query="from:nosuchbank", max_results=25, settings=_settings()
    )

    assert "No messages found" in out


def test_after_filter_excludes_previous_month(fake_gmail):
    """burgan-011 is the previous-month entry; `after:` must drop it."""
    out = gmail.gmail_control(
        action="search", query="from:burgan after:2026/07/01",
        max_results=25, settings=_settings(),
    )

    ids = re.findall(r"^•\s*\[([A-Za-z0-9-]+)\]", out, re.MULTILINE)
    assert "burgan-011" not in ids
    # burgan-010 has no Date header at all -- it cannot satisfy `after:`.
    assert "burgan-010" not in ids
    assert "burgan-001" in ids


def test_read_returns_the_real_decoded_body(fake_gmail):
    out = gmail.gmail_control(
        action="read", message_id="burgan-001", settings=_settings()
    )

    assert not out.startswith("[ERROR]")
    # Proves base64 decode + _extract_text ran, not a canned string.
    assert "250,75 TL" in out
    assert "MIGROS TICARET AS" in out
