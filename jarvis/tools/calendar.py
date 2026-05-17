"""Google Calendar integration via Google Calendar API v3 (Faz 9).

Setup (one-time):
  1. Go to console.cloud.google.com → project elegant-door-495819-k1
  2. Enable "Google Calendar API"
  3. APIs & Services → Credentials → Create OAuth 2.0 Client ID → Desktop app
  4. Download JSON → save as  data/calendar_credentials.json
  5. Add to .env:  GOOGLE_CALENDAR_CREDS_FILE=data/calendar_credentials.json

First run opens a browser for OAuth consent; token cached at data/.calendar_token.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

_SCOPES = ["https://www.googleapis.com/auth/calendar"]
# Absolute path so the token is found regardless of the working directory JARVIS starts from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TOKEN_FILE = _PROJECT_ROOT / "data" / ".calendar_token.json"


def _get_service(settings: "Settings"):
    raw = Path(settings.google_calendar_creds_file)
    creds_path = raw if raw.is_absolute() else _PROJECT_ROOT / raw
    if not creds_path.exists():
        raise RuntimeError(
            f"Google Calendar credentials not found at '{creds_path}'. "
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

    return build("calendar", "v3", credentials=creds)


def _parse_date(date_str: str) -> datetime:
    """Parse a flexible date string into a timezone-aware datetime."""
    date_str = date_str.strip().lower()
    now = datetime.now(timezone.utc)

    if date_str in ("today", "bugün", "bugun"):
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if date_str in ("tomorrow", "yarın", "yarin"):
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    raise ValueError(
        f"Cannot parse date '{date_str}'. "
        "Use YYYY-MM-DD, DD/MM/YYYY, DD.MM.YYYY, 'today', or 'tomorrow'."
    )


def _parse_time(time_str: str) -> tuple[int, int]:
    """Return (hour, minute) from 'HH:MM' or 'HH'."""
    time_str = time_str.strip()
    parts = time_str.replace(".", ":").split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h, m
    except (ValueError, IndexError):
        raise ValueError(f"Cannot parse time '{time_str}'. Use HH:MM format (e.g. '14:30').")


def _fmt_event(event: dict) -> str:
    """Format a single Calendar event for display."""
    summary = event.get("summary", "(no title)")
    start = event.get("start", {})
    end = event.get("end", {})

    start_str = start.get("dateTime") or start.get("date", "")
    end_str = end.get("dateTime") or end.get("date", "")

    if "T" in start_str:
        try:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            start_str = dt.strftime("%d %b %Y %H:%M")
        except ValueError:
            pass
    if "T" in end_str:
        try:
            dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            end_str = dt.strftime("%H:%M")
        except ValueError:
            pass

    location = event.get("location", "")
    desc = event.get("description", "")
    eid = event.get("id", "")
    parts = [f"• {summary}  [{start_str} → {end_str}]"]
    if location:
        parts.append(f"  📍 {location}")
    if desc:
        parts.append(f"  {desc[:120]}")
    parts.append(f"  id: {eid}")
    return "\n".join(parts)


def calendar_control(
    action: str,
    title: str = "",
    date: str = "",
    time: str = "",
    duration_minutes: int = 60,
    description: str = "",
    location: str = "",
    days_ahead: int = 7,
    query: str = "",
    event_id: str = "",
    settings: "Settings" = None,
) -> str:
    """Execute a Google Calendar action.

    Actions:
      list   — list upcoming events (days_ahead window, default 7)
      create — create a new event (title, date, time required)
      delete — delete an event (event_id, or query to find first match)
      search — search events by query keyword
      update — update an event's title/time (event_id required)
    """
    try:
        service = _get_service(settings)
    except RuntimeError as e:
        return f"[Calendar] {e}"

    action = action.lower().strip()

    try:
        if action == "list":
            now = datetime.now(timezone.utc)
            time_min = now.isoformat()
            time_max = (now + timedelta(days=days_ahead)).isoformat()
            result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=20,
                )
                .execute()
            )
            events = result.get("items", [])
            if not events:
                return f"[Calendar] No events in the next {days_ahead} days."
            lines = [f"[Calendar] Upcoming events (next {days_ahead} days):"]
            for ev in events:
                lines.append(_fmt_event(ev))
            return "\n".join(lines)

        elif action == "search":
            if not query:
                return "[Calendar] Provide a 'query' to search for."
            now = datetime.now(timezone.utc)
            result = (
                service.events()
                .list(
                    calendarId="primary",
                    q=query,
                    timeMin=now.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=10,
                )
                .execute()
            )
            events = result.get("items", [])
            if not events:
                return f"[Calendar] No events matching '{query}'."
            lines = [f"[Calendar] Search results for '{query}':"]
            for ev in events:
                lines.append(_fmt_event(ev))
            return "\n".join(lines)

        elif action == "create":
            if not title or not date:
                return "[Calendar] 'title' and 'date' are required to create an event."

            start_dt = _parse_date(date)
            if time:
                h, m = _parse_time(time)
                start_dt = start_dt.replace(hour=h, minute=m)
            end_dt = start_dt + timedelta(minutes=duration_minutes)

            # All-day event if no time given
            if not time:
                event_body = {
                    "summary": title,
                    "description": description,
                    "location": location,
                    "start": {"date": start_dt.strftime("%Y-%m-%d"), "timeZone": "UTC"},
                    "end": {"date": end_dt.strftime("%Y-%m-%d"), "timeZone": "UTC"},
                }
            else:
                event_body = {
                    "summary": title,
                    "description": description,
                    "location": location,
                    "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
                    "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
                }

            created = service.events().insert(calendarId="primary", body=event_body).execute()
            eid = created.get("id", "")
            link = created.get("htmlLink", "")
            return (
                f"[Calendar] Event created: '{title}'\n"
                f"  Date: {date} {time or '(all day)'}\n"
                f"  Duration: {duration_minutes} min\n"
                f"  id: {eid}\n"
                f"  Link: {link}"
            )

        elif action == "delete":
            if not event_id and not query:
                return "[Calendar] Provide 'event_id' or 'query' to find the event to delete."

            if not event_id:
                now = datetime.now(timezone.utc)
                result = (
                    service.events()
                    .list(
                        calendarId="primary",
                        q=query,
                        timeMin=now.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                        maxResults=1,
                    )
                    .execute()
                )
                items = result.get("items", [])
                if not items:
                    return f"[Calendar] No event found matching '{query}'."
                event_id = items[0]["id"]
                found_title = items[0].get("summary", "(no title)")
            else:
                ev = service.events().get(calendarId="primary", eventId=event_id).execute()
                found_title = ev.get("summary", "(no title)")

            service.events().delete(calendarId="primary", eventId=event_id).execute()
            return f"[Calendar] Deleted event: '{found_title}' (id: {event_id})"

        elif action == "update":
            if not event_id:
                return "[Calendar] 'event_id' is required for update. Use search/list to find it."

            ev = service.events().get(calendarId="primary", eventId=event_id).execute()
            old_title = ev.get("summary", "")

            if title:
                ev["summary"] = title
            if description:
                ev["description"] = description
            if location:
                ev["location"] = location
            if date:
                start_dt = _parse_date(date)
                if time:
                    h, m = _parse_time(time)
                    start_dt = start_dt.replace(hour=h, minute=m)
                end_dt = start_dt + timedelta(minutes=duration_minutes)
                if time:
                    ev["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "UTC"}
                    ev["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "UTC"}
                else:
                    ev["start"] = {"date": start_dt.strftime("%Y-%m-%d"), "timeZone": "UTC"}
                    ev["end"] = {"date": end_dt.strftime("%Y-%m-%d"), "timeZone": "UTC"}

            updated = service.events().update(calendarId="primary", eventId=event_id, body=ev).execute()
            new_title = updated.get("summary", "")
            return f"[Calendar] Updated event: '{old_title}' → '{new_title}' (id: {event_id})"

        else:
            return (
                f"[Calendar] Unknown action '{action}'. "
                "Valid: list | create | delete | search | update."
            )

    except Exception as e:
        return f"[Calendar] Error: {e}"
