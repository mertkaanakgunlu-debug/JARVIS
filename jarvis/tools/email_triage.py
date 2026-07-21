"""Two-stage email triage pipeline.

Stage 1 (Flash): Read each email, classify as homework/deadline-related, extract
                 structured fields (course, assignment, deadline, time).
Stage 2 (Pro):   Called by the main agent to create calendar entries from the
                 structured output returned by this tool.

This keeps bulk email reading cheap (Flash) while the main agent (Pro) handles
calendar creation and user-facing communication.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

_TRIAGE_PROMPT = """\
Aşağıdaki e-postayı oku. Bu mail bir ödev, proje teslimi, sınav veya akademik son tarihi içeriyor mu?

Yanıtı YALNIZCA geçerli JSON olarak ver — başka hiçbir şey yazma.

Eğer evet ise:
{{"homework": true, "course": "<ders adı veya gönderen kurum>", "assignment": "<ödev/proje/sınav adı>", "deadline": "<YYYY-MM-DD veya 'bilinmiyor'>", "time": "<HH:MM veya 'bilinmiyor'>", "summary": "<tek cümle özet>"}}

Eğer hayır ise:
{{"homework": false}}

E-POSTA:
Konu: {subject}
Gönderen: {sender}
Tarih: {date}
---
{body}
"""


def _get_gmail_service(settings: "Settings"):
    """Reuse gmail._get_service logic without importing the module's globals."""
    from jarvis import paths
    creds_path = paths.resolve_project(settings.google_calendar_creds_file)

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
    ]
    token_file = paths.project_data_dir() / ".gmail_token.json"

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _extract_text(payload: dict) -> str:
    import base64
    import re
    mime = payload.get("mimeType", "")
    if mime in ("text/plain", "text/html"):
        data = payload.get("body", {}).get("data", "")
        if data:
            text = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            if mime == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
            return text.strip()
    for part in payload.get("parts", []):
        t = _extract_text(part)
        if t:
            return t
    return ""


def _classify_email(msg: dict, llm) -> dict | None:
    """Run Flash classification on a single Gmail message dict. Returns parsed JSON or None."""
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "(no subject)")
    sender  = headers.get("from", "?")
    date    = headers.get("date", "?")[:40]
    body    = _extract_text(msg.get("payload", {}))[:1500]  # cap to keep tokens low

    prompt = _TRIAGE_PROMPT.format(subject=subject, sender=sender, date=date, body=body)

    from langchain_core.messages import HumanMessage
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        if result.get("homework"):
            result["message_id"] = msg.get("id", "")
            result["subject"]    = subject
            result["sender"]     = sender
        return result
    except Exception:
        return None


def triage_emails(
    query: str,
    max_emails: int,
    settings: "Settings",
) -> str:
    """Fetch emails matching `query`, classify each with Flash, return structured list.

    Args:
        query:      Gmail search query (e.g. "ödev OR teslim OR deadline is:unread").
        max_emails: Maximum number of emails to scan (capped at 30).
        settings:   App settings.

    Returns:
        A JSON-formatted string with two keys:
          - "scanned": number of emails processed
          - "homework": list of classified homework emails with fields:
              course, assignment, deadline, time, summary, subject, sender, message_id
    """
    try:
        service = _get_gmail_service(settings)
    except Exception as e:
        return f"[EmailTriage] Gmail auth error: {e}"

    from jarvis.providers import cloud_extractors_enabled, note_degraded
    if not cloud_extractors_enabled(settings):
        note_degraded("email_triage")
        return "[EmailTriage] Cloud LLM disabled (CLOUD_POLICY=off) — classification unavailable."

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        return "[EmailTriage] langchain_google_genai not installed."

    triage_model_id = getattr(settings, "triage_model", "gemini-2.5-flash")
    llm = ChatGoogleGenerativeAI(
        model=triage_model_id,
        google_api_key=settings.gemini_api_key or None,
        max_output_tokens=256,
        temperature=getattr(settings, "email_triage_temperature", 0.0),
    )

    max_emails = min(max_emails, 30)
    try:
        results = (
            service.users().messages()
            .list(userId="me", q=query, maxResults=max_emails)
            .execute()
        )
    except Exception as e:
        return f"[EmailTriage] Gmail list error: {e}"

    messages = results.get("messages", [])
    if not messages:
        return json.dumps({"scanned": 0, "homework": [], "note": f"No emails found for query: {query!r}"})

    homework_items = []
    for m in messages:
        try:
            full = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
        except Exception:
            continue
        result = _classify_email(full, llm)
        if result and result.get("homework"):
            homework_items.append(result)

    return json.dumps({
        "scanned": len(messages),
        "homework": homework_items,
    }, ensure_ascii=False, indent=2)
