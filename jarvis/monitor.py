"""Background monitoring daemon for proactive notifications (Faz 10 + 13-C).

Runs as a daemon thread alongside any JARVIS mode.  Polls Gmail, Google
Calendar, and scheduled tasks on configurable intervals and fires Windows
toast notifications for:
  - New unread e-mails (appears after monitor start)
  - Calendar events starting within monitor_calendar_lookahead_min minutes
  - Scheduled tasks / reminders that are due (Faz 13-C)

Usage:
    monitor = JarvisMonitor(settings, scheduler=scheduler_store)
    monitor.start()   # non-blocking daemon thread
    ...
    monitor.stop()    # graceful shutdown (waits up to 5 s)

Standalone mode (no chat):
    monitor.run_forever()  # blocks until Ctrl-C
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)


class JarvisMonitor:
    def __init__(self, settings: "Settings", scheduler=None) -> None:
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # IDs we have already notified about — never re-alert for the same item
        self._notified_email_ids: set[str] = set()
        self._notified_event_ids: set[str] = set()

        self._email_ok = False   # True after first successful Gmail call
        self._cal_ok = False     # True after first successful Calendar call

        # Faz 13-C: SchedulerStore instance (injected by agent.py or __main__.py)
        self._scheduler = scheduler
        self._sched_ok = False

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background daemon thread (non-blocking)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="jarvis-monitor"
        )
        self._thread.start()
        logger.info("JarvisMonitor started.")

    def stop(self) -> None:
        """Signal the daemon to stop and wait up to 5 s."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def run_forever(self) -> None:
        """Blocking entry-point for standalone --monitor mode."""
        self._run()

    # ── internal loop ──────────────────────────────────────────────────────────

    def _run(self) -> None:
        s = self.settings
        email_interval = getattr(s, "monitor_email_interval_min", 5) * 60
        cal_interval   = getattr(s, "monitor_calendar_interval_min", 2) * 60
        sched_interval = getattr(s, "monitor_schedule_interval_sec", 60)

        # Initialise state silently (avoid startup spam)
        self._init_email_state()
        self._init_calendar_state()

        last_email = 0.0
        last_cal   = 0.0
        last_sched = 0.0

        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_email >= email_interval:
                self._check_email()
                last_email = time.monotonic()
            if now - last_cal >= cal_interval:
                self._check_calendar()
                last_cal = time.monotonic()
            if now - last_sched >= sched_interval:
                self._check_schedule()
                last_sched = time.monotonic()
            # Sleep in short chunks so stop() is responsive
            self._stop.wait(timeout=30)

    # ── initialisation (silent) ────────────────────────────────────────────────

    def _init_email_state(self) -> None:
        """Load current unread IDs without notifying."""
        try:
            svc = self._gmail_service()
            res = (
                svc.users()
                .messages()
                .list(userId="me", q="is:unread", maxResults=25)
                .execute()
            )
            for m in res.get("messages", []):
                self._notified_email_ids.add(m["id"])
            self._email_ok = True
            logger.debug("Monitor: email state initialised (%d seen)", len(self._notified_email_ids))
        except Exception as exc:
            logger.debug("Monitor email init failed: %s", exc)

    def _init_calendar_state(self) -> None:
        """Load events already inside the lookahead window without notifying."""
        try:
            svc = self._calendar_service()
            lookahead = getattr(self.settings, "monitor_calendar_lookahead_min", 15)
            now = datetime.now(timezone.utc)
            res = (
                svc.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat(),
                    timeMax=(now + timedelta(minutes=lookahead + 1)).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=10,
                )
                .execute()
            )
            for ev in res.get("items", []):
                self._notified_event_ids.add(ev["id"])
            self._cal_ok = True
            logger.debug("Monitor: calendar state initialised (%d seen)", len(self._notified_event_ids))
        except Exception as exc:
            logger.debug("Monitor calendar init failed: %s", exc)

    # ── polling ────────────────────────────────────────────────────────────────

    def _check_email(self) -> None:
        try:
            from jarvis.notify import toast

            svc = self._gmail_service()
            res = (
                svc.users()
                .messages()
                .list(userId="me", q="is:unread", maxResults=10)
                .execute()
            )
            self._email_ok = True
            current_ids = {m["id"] for m in res.get("messages", [])}
            new_ids = current_ids - self._notified_email_ids

            for mid in new_ids:
                try:
                    full = (
                        svc.users()
                        .messages()
                        .get(
                            userId="me",
                            id=mid,
                            format="metadata",
                            metadataHeaders=["Subject", "From"],
                        )
                        .execute()
                    )
                    hdrs = {
                        h["name"].lower(): h["value"]
                        for h in full.get("payload", {}).get("headers", [])
                    }
                    subject = hdrs.get("subject", "(no subject)")[:60]
                    sender  = hdrs.get("from", "?")[:40]
                    toast("📧 Yeni E-posta", f"Kimden: {sender}\n{subject}")
                except Exception:
                    toast("📧 Yeni E-posta", "Okunmamış bir mesajınız var.")

                self._notified_email_ids.add(mid)

        except Exception as exc:
            logger.debug("Monitor email check error: %s", exc)

    def _check_calendar(self) -> None:
        try:
            from jarvis.notify import toast

            svc = self._calendar_service()
            self._cal_ok = True
            lookahead = getattr(self.settings, "monitor_calendar_lookahead_min", 15)
            now = datetime.now(timezone.utc)
            res = (
                svc.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat(),
                    timeMax=(now + timedelta(minutes=lookahead + 1)).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=10,
                )
                .execute()
            )
            self._cal_ok = True

            for ev in res.get("items", []):
                eid = ev["id"]
                if eid in self._notified_event_ids:
                    continue
                title = ev.get("summary", "(başlık yok)")
                start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
                if start:
                    try:
                        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                        mins = int((dt - now).total_seconds() / 60)
                        time_str = f"{mins} dakika sonra" if mins > 0 else "şimdi"
                        toast("📅 Takvim Hatırlatıcısı", f"{title}\nBaşlıyor: {time_str}")
                    except Exception:
                        toast("📅 Takvim Hatırlatıcısı", title)
                else:
                    toast("📅 Takvim Hatırlatıcısı", title)

                self._notified_event_ids.add(eid)

        except Exception as exc:
            logger.debug("Monitor calendar check error: %s", exc)

    # ── Faz 13-C: Scheduler check ──────────────────────────────────────────────

    def _check_schedule(self) -> None:
        """Fire toast notifications for any due scheduled tasks."""
        if self._scheduler is None:
            return
        try:
            from jarvis.notify import toast
            due_tasks = self._scheduler.check_due(window_sec=90)
            for task in due_tasks:
                title = task.get("title", "Hatırlatıcı")
                desc  = task.get("description") or ""
                body  = desc[:80] if desc else "Zamanı geldi!"
                toast(f"⏰ {title}", body)
                self._scheduler.mark_ran(task["id"])
                self._sched_ok = True
                logger.info("Scheduler fired: %s (%s)", title, task["id"])
        except Exception as exc:
            logger.debug("Monitor schedule check error: %s", exc)

    # ── Google service helpers ─────────────────────────────────────────────────

    def _gmail_service(self):
        from jarvis.tools.gmail import _get_service
        return _get_service(self.settings)

    def _calendar_service(self):
        from jarvis.tools.calendar import _get_service
        return _get_service(self.settings)

    # ── status ─────────────────────────────────────────────────────────────────

    def status_line(self) -> str:
        s = self.settings
        email_min  = getattr(s, "monitor_email_interval_min", 5)
        cal_min    = getattr(s, "monitor_calendar_interval_min", 2)
        lookahead  = getattr(s, "monitor_calendar_lookahead_min", 15)
        sched_sec  = getattr(s, "monitor_schedule_interval_sec", 60)
        state = "çalışıyor" if self.is_running() else "durdu"
        email_state = "✓" if self._email_ok else "⚠ bağlanamadı"
        cal_state   = "✓" if self._cal_ok   else "⚠ bağlanamadı"
        sched_count = 0
        sched_state = "—"
        if self._scheduler is not None:
            try:
                sched_count = self._scheduler.count_active()
                sched_state = "✓" if self._sched_ok else "bekleniyor"
            except Exception:
                sched_state = "⚠"
        return (
            f"Monitor: [bold]{state}[/bold]\n"
            f"  E-posta kontrolü:   her {email_min} dk  [{email_state}]  "
            f"({len(self._notified_email_ids)} bildirim gönderildi)\n"
            f"  Takvim kontrolü:    her {cal_min} dk  [{cal_state}]  "
            f"(önce {lookahead} dk, {len(self._notified_event_ids)} etkinlik görüldü)\n"
            f"  Planlı görevler:    her {sched_sec} sn  [{sched_state}]  "
            f"({sched_count} aktif görev)"
        )
