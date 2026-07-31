"""google_auth_oauthlib must be needed ONLY for the interactive consent flow.

Live incident (2026-07-30): `google-auth-oauthlib` was absent from the venv AND
undeclared in requirements.txt, while `_get_service()` imported
`InstalledAppFlow` unconditionally at the top of its import block. Result: every
gmail / calendar / drive call raised "Google API library import error" -- even
though valid, refreshable OAuth tokens were sitting in data/. The owner's live
session saw JARVIS claim it had created a calendar event, then admit the
libraries were missing.

These tests pin the contract that fix established:

  * a usable token on disk => _get_service() succeeds with google_auth_oauthlib
    completely unimportable (the offline path never touches it), and
  * no usable token => the error names the interactive flow specifically,
    instead of implying the whole Google API stack is broken.

Covers all three tools that share this code shape, so a future edit to one
can't silently regress the others.
"""
from __future__ import annotations

import builtins
from types import SimpleNamespace

import pytest

from jarvis.tools import calendar as calendar_tool
from jarvis.tools import drive as drive_tool
from jarvis.tools import gmail as gmail_tool

# (module, module-name-for-ids)
TOOL_MODULES = [
    pytest.param(gmail_tool, id="gmail"),
    pytest.param(calendar_tool, id="calendar"),
    pytest.param(drive_tool, id="drive"),
]

_SENTINEL = object()


@pytest.fixture
def block_oauthlib(monkeypatch):
    """Make `google_auth_oauthlib.flow` unimportable, as it was on the owner's
    machine. Patching __import__ (rather than deleting sys.modules entries)
    makes the failure happen at the import statement inside _get_service(),
    which is the code path under test."""
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name.startswith("google_auth_oauthlib"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)


def _prepare(module, monkeypatch, jarvis_home, *, token_valid: bool):
    """Give `module`'s _get_service() a client-secrets file and (optionally) a
    valid token, with the real Google client libraries stubbed out.

    Returns the settings object to pass to _get_service().
    """
    # The client-secrets file must exist -- checked before any import happens.
    creds_file = jarvis_home / "data" / "calendar_credentials.json"
    creds_file.parent.mkdir(parents=True, exist_ok=True)
    creds_file.write_text("{}", encoding="utf-8")

    # _token_file() resolves under JARVIS_HOME (jarvis/paths.py), so writing it
    # here is what "the owner already has a live token" looks like to the code.
    token_file = module._token_file()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("{}", encoding="utf-8")

    fake_creds = SimpleNamespace(
        valid=token_valid,
        expired=not token_valid,
        refresh_token=None,          # forces the InstalledAppFlow branch
        to_json=lambda: "{}",
    )

    import google.oauth2.credentials as gcreds
    import googleapiclient.discovery as gdiscovery

    monkeypatch.setattr(
        gcreds.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, *a, **k: fake_creds),
    )
    monkeypatch.setattr(gdiscovery, "build", lambda *a, **k: _SENTINEL)

    return SimpleNamespace(google_calendar_creds_file="data/calendar_credentials.json")


@pytest.mark.parametrize("module", TOOL_MODULES)
def test_valid_token_works_without_oauthlib(module, monkeypatch, jarvis_home, block_oauthlib):
    """The regression itself: a valid token must not need the consent package."""
    settings = _prepare(module, monkeypatch, jarvis_home, token_valid=True)

    assert module._get_service(settings) is _SENTINEL


@pytest.mark.parametrize("module", TOOL_MODULES)
def test_missing_oauthlib_error_names_the_interactive_flow(
    module, monkeypatch, jarvis_home, block_oauthlib
):
    """With no usable token the flow IS required -- but the message must say so,
    rather than the old blanket "Google API library import error" that sent the
    owner chasing three packages when only one mattered."""
    settings = _prepare(module, monkeypatch, jarvis_home, token_valid=False)

    with pytest.raises(RuntimeError) as excinfo:
        module._get_service(settings)

    message = str(excinfo.value)
    assert "Interactive Google OAuth flow unavailable" in message
    assert "google-auth-oauthlib" in message
