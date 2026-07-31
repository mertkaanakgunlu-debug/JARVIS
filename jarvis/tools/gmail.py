"""Gmail integration via Google Gmail API v1 (Faz 9).

Uses the same OAuth credentials as Google Calendar
(data/calendar_credentials.json), but a separate token cache
at data/.gmail_token.json with Gmail-specific scopes.

First run opens a browser for OAuth consent; subsequent runs use the cache.
"""

from __future__ import annotations

import base64
import json
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _token_file() -> Path:
    # Project-root-anchored so the token is found regardless of cwd — but
    # JARVIS_HOME (isolation profile) overrides it so tests can never touch
    # the real token. See jarvis/paths.py.
    from jarvis import paths
    return paths.project_data_dir() / ".gmail_token.json"


def _fixture_service():
    """A Gmail service stub backed by a JSON fixture -- test profile only.

    Enabled ONLY when JARVIS_TEST_MODE=1 (set exclusively by --profile test's
    pre-scan in jarvis/__main__.py) AND JARVIS_FAKE_GMAIL_FIXTURE names a
    readable file. Both conditions, never one: a stray env var in a normal run
    must not be able to swap the real mailbox for a fake one.

    Why the backend is JSON and the stub lives HERE, in production code:
    the alternative -- importing a test adapter module by path at runtime --
    would mean production honoring an env var that names arbitrary executable
    Python. That is a code-injection surface, added to shipped code, to save
    writing this stub. Data in, no import. tests/support/fake_gmail.py holds
    the richer fake that pytest uses; nothing in jarvis/ imports it.

    The fixture format is scripts/seed_finance_fixture.py's output; this
    reshapes it into the Gmail API's message envelope so the real
    _fmt_message/_extract_text code paths run unmodified -- the point is to
    fake the network, not the parsing under test.
    """
    import base64

    fixture_path = Path(os.environ["JARVIS_FAKE_GMAIL_FIXTURE"])
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    def _envelope(msg: dict) -> dict:
        headers = [
            {"name": "Subject", "value": msg.get("subject", "")},
            {"name": "From", "value": msg.get("from") or fixture.get("sender", "")},
        ]
        # A message with no date is a real fixture case (the extractor must
        # refuse to invent one) -- omit the header entirely rather than
        # emitting an empty one, which is what Gmail would actually do.
        if msg.get("date"):
            headers.append({"name": "Date", "value": msg["date"]})
        body = msg.get("body", "")
        return {
            "id": msg["id"],
            "threadId": msg.get("threadId", msg["id"]),
            "snippet": body[:120],
            "payload": {
                "mimeType": "text/plain",
                "headers": headers,
                "body": {
                    "data": base64.urlsafe_b64encode(
                        body.encode("utf-8")
                    ).decode("ascii").rstrip("=")
                },
            },
        }

    # Preserve fixture order and duplicates: the duplicate-uid entry exists
    # precisely to prove dedup happens downstream, so this layer must not
    # collapse it.
    envelopes = [_envelope(m) for m in fixture.get("messages", [])]
    by_id: dict[str, dict] = {}
    for env in envelopes:
        by_id.setdefault(env["id"], env)

    class _Messages:
        def list(self, userId="me", q="", maxResults=100, pageToken=None, **kw):
            # Only the filtering the fixture needs: an unrecognised operator
            # must not silently behave like "match everything".
            selected = envelopes
            for term in (q or "").split():
                if term.startswith("from:"):
                    needle = term[5:].lower()
                    selected = [
                        e for e in selected
                        if needle in _headers_of(e).get("from", "").lower()
                    ]
                elif term.startswith("after:"):
                    selected = [
                        e for e in selected
                        if _after(_headers_of(e).get("date", ""), term[6:])
                    ]
            payload = {"messages": [{"id": e["id"]} for e in selected[:maxResults]]}
            return _Execute(payload)

        def get(self, userId="me", id="", format="full", **kw):
            return _Execute(by_id.get(id, {}))

        def send(self, **kw):  # pragma: no cover -- writes are blocked in test profile
            raise RuntimeError("fixture Gmail service is read-only")

    class _Execute:
        def __init__(self, payload):
            self._payload = payload

        def execute(self):
            return self._payload

    class _Users:
        def messages(self):
            return _Messages()

    class _Service:
        def users(self):
            return _Users()

    return _Service()


def _headers_of(envelope: dict) -> dict:
    return {
        h["name"].lower(): h["value"]
        for h in envelope.get("payload", {}).get("headers", [])
    }


def _after(date_raw: str, boundary: str) -> bool:
    """Gmail's `after:YYYY/MM/DD` semantics, for the fixture service only."""
    if not date_raw:
        return False
    from email.utils import parsedate_to_datetime
    try:
        when = parsedate_to_datetime(date_raw)
        y, m, d = (int(p) for p in boundary.replace("-", "/").split("/"))
    except (TypeError, ValueError):
        return False
    return (when.year, when.month, when.day) >= (y, m, d)


def _get_service(settings: "Settings"):
    if os.environ.get("JARVIS_TEST_MODE") == "1" and os.environ.get(
        "JARVIS_FAKE_GMAIL_FIXTURE"
    ):
        return _fixture_service()

    from jarvis import paths
    creds_path = paths.resolve_project(settings.google_calendar_creds_file)
    if not creds_path.exists():
        raise RuntimeError(
            f"Google OAuth credentials not found at '{creds_path}'. "
            "See jarvis/tools/calendar.py docstring for setup instructions."
        )
    try:
        from google.auth.exceptions import RefreshError
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as _ie:
        raise RuntimeError(
            f"Google API library import error: {_ie}. "
            "Run: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )

    creds = None
    token_file = _token_file()
    token_file.parent.mkdir(parents=True, exist_ok=True)

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # A revoked/expired refresh token raises RefreshError, which is NOT
            # a RuntimeError -- it used to escape gmail_control()'s
            # `except RuntimeError` entirely and surface as a raw traceback
            # (found live 2026-07-30: 'invalid_grant: Bad Request'). Converted
            # to the same actionable RuntimeError channel every other auth
            # failure here uses. Deliberately NOT falling through to
            # run_local_server(): silently opening a browser would hang any
            # unattended/server-side run instead of failing honestly.
            try:
                creds.refresh(Request())
            except RefreshError as _re:
                raise RuntimeError(
                    f"Gmail authorization is no longer valid ({_re}). "
                    "The stored token cannot be refreshed -- re-authorize with: "
                    "python scripts/auth_setup.py"
                )
        else:
            # google_auth_oauthlib is imported HERE, not with the block above:
            # it is needed only for the interactive first-run consent flow.
            # Hoisting it made a missing/partial install fail every call --
            # including ones a valid, refreshable token could have served
            # entirely offline (2026-07-30: the package was absent from the
            # venv AND from requirements.txt, so gmail/calendar/drive were
            # dead despite live tokens sitting on disk).
            try:
                from google_auth_oauthlib.flow import InstalledAppFlow
            except ImportError as _ie:
                raise RuntimeError(
                    f"Interactive Google OAuth flow unavailable: {_ie}. "
                    "Run: pip install google-auth-oauthlib"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _decode_body(part) -> str:
    """Decode a MIME part body to string."""
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_text(payload: dict) -> str:
    """Recursively extract plain text from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        return _decode_body(payload)
    if mime_type == "text/html":
        raw = _decode_body(payload)
        return re.sub(r"<[^>]+>", "", raw).strip()
    parts = payload.get("parts", [])
    for part in parts:
        text = _extract_text(part)
        if text:
            return text
    return ""


def message_fields(msg: dict) -> dict:
    """A Gmail API message envelope reduced to the fields callers actually need.

    Added 2026-07-30 (the structured-access layer). Before this, the ONLY way to
    get at a message's parts from another module was to render it with
    _fmt_message() and regex the display string back apart -- which is exactly
    what jarvis/tools/finance.py did, and it went wrong twice (BUG-15: the regex
    expected "[id]" while the formatter emits "• [id]", so sync silently found
    zero messages for a long time; the subject/body split looked for a
    "Konu:"/"---" layout the formatter has never produced).

    Structure in, structure out. `date_raw` is the untouched RFC-2822 `Date`
    header -- callers that need a real datetime parse it themselves with
    email.utils.parsedate_to_datetime rather than receiving a lossy string, and
    `body` is NOT truncated here (the 2000-char cut belongs to the display path,
    not to data access).
    """
    headers = {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "subject": headers.get("subject", ""),
        "from": headers.get("from", ""),
        "date_raw": headers.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "body": _extract_text(msg.get("payload", {})),
    }


# The display path's cap. Kept deliberately separate from the structured
# helpers' limits: 25 messages is a sane amount of text to hand an LLM, and a
# terrible cap for a finance sync that needs a whole month.
DISPLAY_MAX_RESULTS = 25


def search_messages(
    query: str, settings: "Settings", *, limit: int = 200, page_size: int = 100,
) -> list[dict]:
    """Structured Gmail search: returns message_fields() dicts, newest first.

    Two things this fixes for callers like finance sync:

      * No 25-message ceiling. gmail_control's `search` clamps to
        DISPLAY_MAX_RESULTS, so finance asking for 50 silently received 25 --
        a month of notifications quietly truncated with no error anywhere.
      * One API round-trip per message instead of two. gmail_control already
        fetches format="full" for every hit and then throws the payload away;
        finance used to re-fetch each message with a second `read` call to
        recover what had just been discarded.

    Paginates via nextPageToken, so `limit` is a real limit rather than a
    single-page artifact.
    """
    service = _get_service(settings)
    out: list[dict] = []
    page_token = None
    while len(out) < limit:
        req = service.users().messages().list(
            userId="me", q=query,
            maxResults=min(page_size, limit - len(out)),
            pageToken=page_token,
        )
        page = req.execute()
        ids = [m["id"] for m in page.get("messages", [])]
        for mid in ids:
            if len(out) >= limit:
                break
            full = service.users().messages().get(
                userId="me", id=mid, format="full"
            ).execute()
            if full:
                out.append(message_fields(full))
        page_token = page.get("nextPageToken")
        if not page_token or not ids:
            break
    return out


def _fmt_message(msg: dict, full: bool = False) -> str:
    """Format a Gmail message for display.

    Now a pure formatter over message_fields() -- the output is byte-identical to
    before (finance's "• [id]" regex and every existing test depend on it), but
    field extraction lives in exactly one place.
    """
    f = message_fields(msg)
    subject = f["subject"] or "(no subject)"
    sender = f["from"] or "?"

    lines = [f"• [{f['id']}]  {subject}"]
    lines.append(f"  From: {sender}")
    if f["date_raw"]:
        lines.append(f"  Date: {f['date_raw'][:40]}")

    if full:
        body = f["body"]
        if body:
            lines.append(f"\n{body[:2000]}" + ("…" if len(body) > 2000 else ""))
    else:
        if f["snippet"]:
            lines.append(f"  {f['snippet'][:120]}")

    return "\n".join(lines)


def gmail_control(
    action: str,
    query: str = "",
    message_id: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    max_results: int = 10,
    settings: "Settings" = None,
) -> str:
    """Execute a Gmail action.

    Actions:
      list_unread   — list unread emails (max_results, default 10)
      search        — search emails by Gmail query (e.g. "from:boss subject:report")
      read          — read full email by message_id
      send          — send a new email (to, subject, body required)
      reply         — reply to an email (message_id, body required)
      trash         — move email to trash (message_id required)
      mark_read     — mark email as read (message_id required)
    """
    try:
        service = _get_service(settings)
    except RuntimeError as e:
        # [ERROR] (not [Gmail]) -- see calendar.py's identical comment: [Gmail]
        # also prefixes normal success output, so it can't join
        # _FAILURE_PREFIXES without misjudging real successes too.
        return f"[ERROR] {e}"

    action = action.lower().strip()

    try:
        if action == "list_unread":
            results = (
                service.users()
                .messages()
                .list(userId="me", q="is:unread", maxResults=min(max_results, DISPLAY_MAX_RESULTS))
                .execute()
            )
            messages = results.get("messages", [])
            if not messages:
                return "[Gmail] No unread messages."
            lines = [f"[Gmail] Unread messages ({len(messages)}):"]
            for m in messages:
                full = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
                lines.append(_fmt_message(full))
            return "\n".join(lines)

        elif action == "search":
            if not query:
                return "[ERROR] Provide a 'query' for search (Gmail search syntax)."
            results = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=min(max_results, DISPLAY_MAX_RESULTS))
                .execute()
            )
            messages = results.get("messages", [])
            if not messages:
                return f"[Gmail] No messages found for: '{query}'"
            lines = [f"[Gmail] Search results for '{query}' ({len(messages)} messages):"]
            for m in messages:
                full = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
                lines.append(_fmt_message(full))
            return "\n".join(lines)

        elif action == "read":
            if not message_id:
                return "[ERROR] Provide 'message_id' to read. Use list_unread or search first."
            full = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            return f"[Gmail] Message:\n{_fmt_message(full, full=True)}"

        elif action == "send":
            if not to or not subject or not body:
                return "[ERROR] 'to', 'subject', and 'body' are required to send an email."
            profile = service.users().getProfile(userId="me").execute()
            sender_email = profile.get("emailAddress", "me")

            msg = MIMEMultipart()
            msg["To"] = to
            msg["From"] = sender_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return f"[Gmail] Email sent to {to}  (id: {sent.get('id', '?')})"

        elif action == "reply":
            if not message_id or not body:
                return "[ERROR] 'message_id' and 'body' are required to reply."
            original = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in original.get("payload", {}).get("headers", [])}
            reply_to = headers.get("reply-to") or headers.get("from", "")
            orig_subject = headers.get("subject", "")
            thread_id = original.get("threadId", "")
            msg_id_header = headers.get("message-id", "")

            reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
            profile = service.users().getProfile(userId="me").execute()
            sender_email = profile.get("emailAddress", "me")

            msg = MIMEMultipart()
            msg["To"] = reply_to
            msg["From"] = sender_email
            msg["Subject"] = reply_subject
            if msg_id_header:
                msg["In-Reply-To"] = msg_id_header
                msg["References"] = msg_id_header
            msg.attach(MIMEText(body, "plain", "utf-8"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            sent = (
                service.users()
                .messages()
                .send(userId="me", body={"raw": raw, "threadId": thread_id})
                .execute()
            )
            return f"[Gmail] Reply sent to {reply_to}  (id: {sent.get('id', '?')})"

        elif action == "trash":
            if not message_id:
                return "[ERROR] Provide 'message_id' to trash."
            service.users().messages().trash(userId="me", id=message_id).execute()
            return f"[Gmail] Message {message_id} moved to trash."

        elif action == "mark_read":
            if not message_id:
                return "[ERROR] Provide 'message_id' to mark as read."
            service.users().messages().modify(
                userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()
            return f"[Gmail] Message {message_id} marked as read."

        else:
            return (
                f"[ERROR] Unknown action '{action}'. "
                "Valid: list_unread | search | read | send | reply | trash | mark_read"
            )

    except Exception as e:
        return f"[ERROR] {e}"
