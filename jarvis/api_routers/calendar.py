"""Calendar REST endpoints (Faz 19A-0) — wraps the Faz 9 Google Calendar tool."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/calendar", tags=["calendar"])

_settings = None


def init_calendar(settings) -> None:
    global _settings
    _settings = settings


@router.get("/today")
async def today():
    """Return today's calendar events from Google Calendar."""
    try:
        from jarvis.tools.calendar import _get_service  # type: ignore
        svc = _get_service(_settings)
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        res = (
            svc.events()
            .list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=20,
            )
            .execute()
        )
        events = []
        for ev in res.get("items", []):
            start_raw = ev.get("start", {})
            events.append({
                "id": ev.get("id"),
                "title": ev.get("summary", ""),
                "location": ev.get("location", ""),
                "start": start_raw.get("dateTime") or start_raw.get("date"),
                "all_day": "date" in start_raw and "dateTime" not in start_raw,
                "description": ev.get("description", ""),
            })
        return {"date": date.today().isoformat(), "events": events}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calendar unavailable: {exc}")
