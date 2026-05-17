"""Gmail integration via Google Gmail API v1 (Faz 9).

Uses the same OAuth credentials as Google Calendar
(data/calendar_credentials.json), but a separate token cache
at data/.gmail_token.json with Gmail-specific scopes.

First run opens a browser for OAuth consent; subsequent runs use the cache.
"""

from __future__ import annotations

import base64
import email as email_lib
import re
from datetime import datetime, timezone
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
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TOKEN_FILE = _PROJECT_ROOT / "data" / ".gmail_token.json"


def _get_service(settings: "Settings"):
    raw = Path(settings.google_calendar_creds_file)
    creds_path = raw if raw.is_absolute() else _PROJECT_ROOT / raw
    if not creds_path.exists():
        raise RuntimeError(
            f"Google OAuth credentials not found at '{creds_path}'. "
            "See jarvis/tools/calendar.py docstring for setup instructions."
        )
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as _ie:
        raise RuntimeError(
            f"Google API library import error: {_ie}. "
            "Run: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )

    creds = None
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
            creds = flow.run_local_server(port=0)
        _TOKEN_FILE.write_text(creds.to_json())

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


def _fmt_message(msg: dict, full: bool = False) -> str:
    """Format a Gmail message for display."""
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "(no subject)")
    sender = headers.get("from", "?")
    date_raw = headers.get("date", "")
    msg_id = msg.get("id", "")
    snippet = msg.get("snippet", "")

    lines = [f"• [{msg_id}]  {subject}"]
    lines.append(f"  From: {sender}")
    if date_raw:
        lines.append(f"  Date: {date_raw[:40]}")

    if full:
        body = _extract_text(msg.get("payload", {}))
        if body:
            lines.append(f"\n{body[:2000]}" + ("…" if len(body) > 2000 else ""))
    else:
        if snippet:
            lines.append(f"  {snippet[:120]}")

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
        return f"[Gmail] {e}"

    action = action.lower().strip()

    try:
        if action == "list_unread":
            results = (
                service.users()
                .messages()
                .list(userId="me", q="is:unread", maxResults=min(max_results, 25))
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
                return "[Gmail] Provide a 'query' for search (Gmail search syntax)."
            results = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=min(max_results, 25))
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
                return "[Gmail] Provide 'message_id' to read. Use list_unread or search first."
            full = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            return f"[Gmail] Message:\n{_fmt_message(full, full=True)}"

        elif action == "send":
            if not to or not subject or not body:
                return "[Gmail] 'to', 'subject', and 'body' are required to send an email."
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
                return "[Gmail] 'message_id' and 'body' are required to reply."
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
                return "[Gmail] Provide 'message_id' to trash."
            service.users().messages().trash(userId="me", id=message_id).execute()
            return f"[Gmail] Message {message_id} moved to trash."

        elif action == "mark_read":
            if not message_id:
                return "[Gmail] Provide 'message_id' to mark as read."
            service.users().messages().modify(
                userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()
            return f"[Gmail] Message {message_id} marked as read."

        else:
            return (
                f"[Gmail] Unknown action '{action}'. "
                "Valid: list_unread | search | read | send | reply | trash | mark_read"
            )

    except Exception as e:
        return f"[Gmail] Error: {e}"
