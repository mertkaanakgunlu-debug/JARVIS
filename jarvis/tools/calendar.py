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

from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.clock import DEFAULT_TZ, Clock, SystemClock
from jarvis.nlu import event_text, temporal

if TYPE_CHECKING:
    from jarvis.config import Settings

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _clock_for(settings: "Settings | None") -> Clock:
    """The clock this tool resolves dates against — the CONFIGURED calendar
    timezone, not UTC.

    Post-MVP Faz 2. Every "now" in this file used to be
    `datetime.now(timezone.utc)`, including the one that resolved "yarın".
    The result was then handed to Google as a naive wall-clock string with
    `timeZone: Europe/Istanbul`, so during the three hours a day when UTC is
    still on the previous date (Istanbul 00:00–02:59), "yarın" produced an
    event one day early — a real event, on a real wrong day, with no error.
    Verified against the pre-fix `_parse_date` across all 24 local hours:
    wrong in exactly 3.
    """
    return SystemClock(tz_name=getattr(settings, "calendar_timezone", None) or DEFAULT_TZ)


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


# _parse_date()/_parse_time() used to live here. Both are gone rather than
# rewritten as wrappers: create and update now call jarvis.nlu.temporal.resolve()
# directly, which resolves the date and the time TOGETHER (a model that puts the
# whole phrase in one field still lands right), so a pair of separate parsers
# had no remaining caller. Keeping them as thin shims would have left two
# plausible entry points where the point of this phase is that there is one.


def _all_day_end(start_dt: datetime, duration_minutes: int) -> str:
    """Exclusive end date for an all-day event.

    Google Calendar treats `end.date` as EXCLUSIVE, so a one-day event ends on
    the FOLLOWING date. The pre-Faz-2 code sent `start + duration_minutes`
    (default 60), which for an all-day event is the same calendar date as the
    start — an empty range. Found while moving this file onto the resolver;
    reasoned from the API contract, not live-verified against Google (no
    credentials available in this environment), so it is deliberately written
    to be safe either way: it can only ever move the end LATER.
    """
    days = max(1, -(-int(duration_minutes) // 1440))  # ceil, min 1
    return (start_dt + timedelta(days=days)).strftime("%Y-%m-%d")


def fetch_events(
    settings: "Settings",
    *,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    days_ahead: int = 7,
    query: str = "",
    max_results: int = 20,
    service=None,
) -> list[dict]:
    """Upcoming events as RAW Google event dicts, ordered by start time.

    Post-MVP Faz 3. `calendar_control("list")` returns display text, and the
    daily briefing needs the events themselves: it decides what is "today",
    formats times in the user's zone, and hands the model a closed fact list.
    Parsing that display text back into events would mean the briefing's
    correctness depended on a bullet-point format nobody thinks of as an
    interface -- and one whose `_fmt_event` output is deliberately lossy
    (descriptions truncated at 120 chars).

    So this is the single API call, and `calendar_control("list")` is now one
    of its two callers rather than the only place the query lives. Raises
    RuntimeError (missing credentials, expired token) and whatever the Google
    client raises; both callers catch, because the briefing reports a failed
    source and the tool renders `[ERROR]`.

    `service` is an already-authenticated client to reuse -- `calendar_control`
    builds one for the whole call and passes it here rather than paying for a
    second `_get_service` on the same request.
    """
    service = service or _get_service(settings)
    clock = _clock_for(settings)
    start = time_min or clock.now()
    end = time_max or (start + timedelta(days=days_ahead))

    params: dict = {
        "calendarId": "primary",
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": max_results,
    }
    if query:
        params["q"] = query
    return (service.events().list(**params).execute()).get("items", [])


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
    clock = _clock_for(settings)

    try:
        if action == "list":
            # Same query the briefing runs (fetch_events above) -- one place,
            # so a window or ordering change cannot land in one and not the
            # other. The already-built `service` is handed straight through.
            events = fetch_events(
                settings, days_ahead=days_ahead, max_results=20, service=service
            )
            if not events:
                return f"[Calendar] No events in the next {days_ahead} days."
            lines = [f"[Calendar] Upcoming events (next {days_ahead} days):"]
            for ev in events:
                lines.append(_fmt_event(ev))
            return "\n".join(lines)

        elif action == "search":
            if not query:
                return "[ERROR] Provide a 'query' to search for."
            now = clock.now()
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

            tz_name = clock.tz_name
            # One resolution for the whole call: date and time are resolved
            # TOGETHER (jarvis/nlu/temporal.resolve) so a model that put the
            # time inside the date field ("yarın öğlen 3") still lands right,
            # and so the instant is built in the user's zone rather than
            # assembled from a UTC midnight.
            resolution = temporal.resolve(date, time, clock=clock)
            if not resolution.ok:
                return f"[ERROR] {resolution.reason}"
            start_dt = resolution.start
            end_dt = start_dt + timedelta(minutes=duration_minutes)
            time = "" if resolution.all_day else f"{start_dt:%H:%M}"

            # Title/description discipline (Post-MVP Faz 2, plan item 4): a
            # calendar holds a record of what is happening, not a copy of the
            # instruction that created it. Applied HERE rather than only in
            # the prompt so it holds whatever the model does -- but strictly
            # subtractive, and never to the point of an empty title.
            cleaning = event_text.clean_title(title)
            title = cleaning.title
            if event_text.description_is_restatement(title, description):
                description = ""

            # ── Deduplication guard ───────────────────────────────────────────
            # Check for an existing event with the same title at the same time.
            # Prevents duplicate creation when the agent loops or retries a call.
            try:
                # start_dt is timezone-aware in the configured zone already (the
                # resolver builds it that way), so the window needs no
                # reinterpretation — the pre-Faz-2 `.replace(tzinfo=tz)` here was
                # patching over the fact that _parse_date returned a UTC-stamped
                # datetime whose wall clock meant local time.
                win_start = start_dt - timedelta(minutes=5)
                win_end   = start_dt + timedelta(minutes=5)
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
                    "end": {"date": _all_day_end(start_dt, duration_minutes)},
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
            # Report the RESOLVED date, not the expression the model passed.
            # Echoing "yarın" back tells the user nothing about which day was
            # actually written, and this string is what the model relays — the
            # Faz 1 honesty kernel can only be as truthful as its inputs.
            lines = [
                f"[Calendar] Event created: '{title}'",
                f"  Date: {temporal.describe(resolution)} ({tz_name})",
                f"  Duration: {duration_minutes} min",
            ]
            if cleaning.changed:
                lines.append(f"  Title normalised — {cleaning.reason}")
            lines += [f"  id: {eid}", f"  Link: {link}"]
            return "\n".join(lines)

        elif action == "delete":
            if not event_id and not query:
                return "[ERROR] Provide 'event_id' or 'query' to find the event to delete."

            if not event_id:
                now = clock.now()
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
                # Same discipline as create -- an update is just as capable of
                # replacing a good title with the sentence that asked for it.
                title = event_text.clean_title(title).title
                ev["summary"] = title
            if description and not event_text.description_is_restatement(title or ev.get("summary", ""), description):
                ev["description"] = description
            if location:
                ev["location"] = location
            if date:
                # Same resolver as create -- an update that moved an event to
                # the wrong day would be the identical bug wearing a different
                # action name.
                tz_name = clock.tz_name
                resolution = temporal.resolve(date, time, clock=clock)
                if not resolution.ok:
                    return f"[ERROR] {resolution.reason}"
                start_dt = resolution.start
                end_dt = start_dt + timedelta(minutes=duration_minutes)
                if resolution.all_day:
                    ev["start"] = {"date": start_dt.strftime("%Y-%m-%d")}
                    ev["end"]   = {"date": _all_day_end(start_dt, duration_minutes)}
                else:
                    ev["start"] = {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz_name}
                    ev["end"]   = {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),   "timeZone": tz_name}

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
