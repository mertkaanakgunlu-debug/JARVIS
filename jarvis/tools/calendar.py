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

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from jarvis.config import Settings

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _token_file() -> Path:
    # Project-root-anchored so the token is found regardless of the working
    # directory JARVIS starts from; JARVIS_HOME (isolation profile) overrides.
    from jarvis import paths
    return paths.project_data_dir() / ".calendar_token.json"


def _get_service(settings: "Settings"):
    from jarvis import paths
    creds_path = paths.resolve_project(settings.google_calendar_creds_file)
    if not creds_path.exists():
        raise RuntimeError(
            f"Google Calendar credentials not found at '{creds_path}'. "
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
            # See gmail.py's identical block: RefreshError is not a
            # RuntimeError and would otherwise escape calendar_control()'s
            # handler as a raw traceback.
            try:
                creds.refresh(Request())
            except RefreshError as _re:
                raise RuntimeError(
                    f"Google Calendar authorization is no longer valid ({_re}). "
                    "The stored token cannot be refreshed -- re-authorize with: "
                    "python scripts/auth_setup.py"
                )
        else:
            # Interactive-flow-only import -- see the identical comment in
            # jarvis/tools/gmail.py's _get_service() for the incident this
            # guards against (a valid token could not be used because the
            # first-run consent package was missing).
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
    events_json: str = "",
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
        # [ERROR] (not [Calendar]) so the audit's content_is_failure() sees
        # this as a real failure -- [Calendar] also prefixes normal success
        # output ("Event created: ..."), so it can never be added to
        # _FAILURE_PREFIXES without misjudging real successes too (live-found
        # 2026-07-18: a missing-credentials E14 call was logged ok:true).
        return f"[ERROR] {e}"

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
                return "[ERROR] Provide a 'query' to search for."
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

        elif action == "batch_create":
            # ── Batch create: create multiple events in a single tool call ──────
            # events_json: '[{"title":"..","date":"..","time":"..","duration_minutes":120,"description":"..","location":".."},...]'
            if not events_json:
                return "[ERROR] 'events_json' is required for batch_create."
            try:
                import json as _json
                items = _json.loads(events_json)
                if not isinstance(items, list):
                    return "[ERROR] 'events_json' must be a JSON array."
            except Exception as exc:
                return f"[ERROR] Could not parse events_json: {exc}"

            results = []
            for item in items:
                r = calendar_control(
                    action="create",
                    title=item.get("title", ""),
                    date=item.get("date", ""),
                    time=item.get("time", ""),
                    duration_minutes=item.get("duration_minutes", 60),
                    description=item.get("description", ""),
                    location=item.get("location", ""),
                    settings=settings,
                )
                results.append(r)
            return "\n".join(results)

        elif action == "create":
            if not title or not date:
                return "[ERROR] 'title' and 'date' are required to create an event."

            tz_name = getattr(settings, "calendar_timezone", "Europe/Istanbul")
            start_dt = _parse_date(date)
            if time:
                h, m = _parse_time(time)
                start_dt = start_dt.replace(hour=h, minute=m)
            end_dt = start_dt + timedelta(minutes=duration_minutes)

            # ── Deduplication guard ───────────────────────────────────────────
            # Check for an existing event with the same title at the same time.
            # Prevents duplicate creation when the agent loops or retries a call.
            try:
                # start_dt's wall-clock numbers represent local time in tz_name (see the
                # naive-strftime + explicit timeZone trick below) — reinterpret with the
                # user's actual configured zone, not a hardcoded +03:00, so this stays
                # correct if calendar_timezone is ever set to anything but Europe/Istanbul.
                tz = ZoneInfo(tz_name)
                win_start = (start_dt - timedelta(minutes=5)).replace(tzinfo=tz)
                win_end   = (start_dt + timedelta(minutes=5)).replace(tzinfo=tz)
                existing = service.events().list(
                    calendarId="primary",
                    q=title,
                    timeMin=win_start.isoformat(),
                    timeMax=win_end.isoformat(),
                    singleEvents=True,
                    maxResults=5,
                ).execute()
                for ev in existing.get("items", []):
                    if ev.get("summary", "").lower().strip() == title.lower().strip():
                        return (
                            f"[Calendar] SKIPPED — '{title}' already exists at this time "
                            f"(id: {ev['id'][:16]}). No duplicate created."
                        )
            except Exception:
                pass  # dedup is best-effort; never block creation on API error

            # ── Create ────────────────────────────────────────────────────────
            # Naive datetime string (no UTC offset) + explicit timeZone → Google Calendar
            # stores the event in the user's local timezone, not UTC.
            if not time:
                event_body = {
                    "summary": title,
                    "description": description,
                    "location": location,
                    "start": {"date": start_dt.strftime("%Y-%m-%d")},
                    "end": {"date": end_dt.strftime("%Y-%m-%d")},
                }
            else:
                dt_str  = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
                end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
                event_body = {
                    "summary": title,
                    "description": description,
                    "location": location,
                    "start": {"dateTime": dt_str,  "timeZone": tz_name},
                    "end":   {"dateTime": end_str, "timeZone": tz_name},
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
                return "[ERROR] Provide 'event_id' or 'query' to find the event to delete."

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
                return "[ERROR] 'event_id' is required for update. Use search/list to find it."

            ev = service.events().get(calendarId="primary", eventId=event_id).execute()
            old_title = ev.get("summary", "")

            if title:
                ev["summary"] = title
            if description:
                ev["description"] = description
            if location:
                ev["location"] = location
            if date:
                tz_name = getattr(settings, "calendar_timezone", "Europe/Istanbul")
                start_dt = _parse_date(date)
                if time:
                    h, m = _parse_time(time)
                    start_dt = start_dt.replace(hour=h, minute=m)
                end_dt = start_dt + timedelta(minutes=duration_minutes)
                if time:
                    ev["start"] = {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz_name}
                    ev["end"]   = {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),   "timeZone": tz_name}
                else:
                    ev["start"] = {"date": start_dt.strftime("%Y-%m-%d")}
                    ev["end"]   = {"date": end_dt.strftime("%Y-%m-%d")}

            updated = service.events().update(calendarId="primary", eventId=event_id, body=ev).execute()
            new_title = updated.get("summary", "")
            return f"[Calendar] Updated event: '{old_title}' → '{new_title}' (id: {event_id})"

        else:
            return (
                f"[ERROR] Unknown action '{action}'. "
                "Valid: list | create | delete | search | update."
            )

    except Exception as e:
        return f"[ERROR] {e}"
