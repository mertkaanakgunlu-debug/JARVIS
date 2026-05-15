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
    def __init__(self, settings: "Settings", scheduler=None, todo_store=None) -> None:
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # IDs we have already notified about — never re-alert for the same item
        self._notified_email_ids: set[str] = set()
        self._notified_event_ids: set[str] = set()
        self._notified_itu_ids: set[str] = set()   # Faz 15: ITU mail UIDs

        self._email_ok = False   # True after first successful Gmail call
        self._cal_ok = False     # True after first successful Calendar call
        self._itu_mail_ok = False  # Faz 15
        self._finance_ok = False   # Faz 16
        self._gcp_ok = False       # Faz 17

        # Faz 13-C: SchedulerStore instance (injected by agent.py or __main__.py)
        self._scheduler = scheduler
        self._sched_ok = False

        # Faz 13-D: TodoStore instance (injected alongside scheduler)
        self._todo_store = todo_store
        self._todo_morning_fired_date: str = ""   # YYYY-MM-DD of last morning summary

        # Faz 19A-0: FCM push (lazily initialised on first notification)
        self._fcm = None
        self._push_store = None

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
        email_interval    = getattr(s, "monitor_email_interval_min", 5) * 60
        cal_interval      = getattr(s, "monitor_calendar_interval_min", 2) * 60
        sched_interval    = getattr(s, "monitor_schedule_interval_sec", 60)
        itu_mail_interval = getattr(s, "monitor_itu_mail_interval_min", 5) * 60
        finance_interval  = getattr(s, "monitor_finance_interval_min", 30) * 60
        gcp_interval      = getattr(s, "monitor_gcp_interval_min", 30) * 60

        # Initialise state silently (avoid startup spam)
        self._init_email_state()
        self._init_calendar_state()
        self._init_itu_mail_state()

        last_email    = 0.0
        last_cal      = 0.0
        last_sched    = 0.0
        last_todo     = 0.0
        last_itu_mail = 0.0
        last_finance  = 0.0
        last_gcp      = 0.0

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
            if now - last_todo >= sched_interval:   # same interval as scheduler
                self._check_todos()
                last_todo = time.monotonic()
            if now - last_itu_mail >= itu_mail_interval:
                self._poll_itu_mail()
                last_itu_mail = time.monotonic()
            if now - last_finance >= finance_interval:
                self._check_finance()
                last_finance = time.monotonic()
            if now - last_gcp >= gcp_interval:
                self._check_gcp_quota()
                last_gcp = time.monotonic()
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
                    self._dispatch_push(
                        "📧 Yeni E-posta",
                        f"Kimden: {sender} — {subject}",
                        {"category": "email", "message_id": mid},
                    )
                except Exception:
                    toast("📧 Yeni E-posta", "Okunmamış bir mesajınız var.")
                    self._dispatch_push(
                        "📧 Yeni E-posta",
                        "Okunmamış bir mesajınız var.",
                        {"category": "email"},
                    )

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
                        self._dispatch_push(
                            "📅 Takvim Hatırlatıcısı",
                            f"{title} — {time_str}",
                            {"category": "calendar", "event_id": eid},
                        )
                    except Exception:
                        toast("📅 Takvim Hatırlatıcısı", title)
                        self._dispatch_push(
                            "📅 Takvim Hatırlatıcısı", title,
                            {"category": "calendar", "event_id": eid},
                        )
                else:
                    toast("📅 Takvim Hatırlatıcısı", title)
                    self._dispatch_push(
                        "📅 Takvim Hatırlatıcısı", title,
                        {"category": "calendar", "event_id": eid},
                    )

                self._notified_event_ids.add(eid)

        except Exception as exc:
            logger.debug("Monitor calendar check error: %s", exc)

    # ── Faz 13-D: To-do reminders ──────────────────────────────────────────────

    def _check_todos(self) -> None:
        """Fire toast for due-soon todos + morning daily summary."""
        if self._todo_store is None:
            return
        try:
            from jarvis.notify import toast

            s = self.settings
            lookahead_min = getattr(s, "todo_reminder_lookahead_min", 120)
            morning_hour  = getattr(s, "todo_reminder_hour", 9)

            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # 1. Morning summary (once per day at the configured hour)
            if (now.hour >= morning_hour
                    and self._todo_morning_fired_date != today_str):
                todos = self._todo_store.top_open(n=3)
                if todos:
                    titles = "\n".join(f"• {t['title']}" for t in todos)
                    toast("📋 Günün Görevleri", titles)
                    self._todo_morning_fired_date = today_str

            # 2. Due-soon alerts
            due = self._todo_store.due_soon(within_min=lookahead_min)
            for t in due:
                # Avoid re-alerting within same lookahead window
                last = t.get("last_reminded_at") or ""
                if last and last >= now.strftime("%Y-%m-%dT%H"):
                    continue
                toast(f"⏰ Görev yaklaşıyor: {t['title']}", f"Bitiş: {t['due_date']}")
                self._dispatch_push(
                    f"⏰ Görev yaklaşıyor: {t['title']}",
                    f"Bitiş: {t['due_date']}",
                    {"category": "todo", "todo_id": t["id"]},
                )
                self._todo_store.update_reminded(t["id"])

        except Exception as exc:
            logger.debug("Monitor todo check error: %s", exc)

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

    # ── Faz 17: GCP quota alerts ──────────────────────────────────────────────

    def _check_gcp_quota(self) -> None:
        """Fire toast notifications for over-threshold GCP quota conditions."""
        try:
            from jarvis.notify import toast
            from jarvis.gcp_quota import quota_alert_check
            alerts = quota_alert_check(self.settings)
            for msg in alerts:
                toast("⚡ GCP Kota Uyarısı", msg)
                self._dispatch_push(
                    "⚡ GCP Kota Uyarısı", msg, {"category": "gcp_quota"}
                )
            self._gcp_ok = True
        except Exception as exc:
            logger.debug("Monitor GCP quota check error: %s", exc)

    # ── Faz 16: Finance sync + budget alerts ──────────────────────────────────

    def _check_finance(self) -> None:
        """Auto-sync Burgan transactions and fire budget-threshold toasts."""
        try:
            from jarvis.notify import toast
            from pathlib import Path
            from datetime import datetime as _dt
            from jarvis.finance_store import FinanceStore

            store = FinanceStore(Path("data/sessions.db"))
            now = _dt.now()

            # Check budget thresholds (fast — no network)
            statuses = store.budget_status(year=now.year, month=now.month)
            for s in statuses:
                if s["over_threshold"]:
                    from jarvis.finance_reporter import CATEGORY_LABELS
                    label = CATEGORY_LABELS.get(s["category"], s["category"].title())
                    pct_str = f"%{s['pct']*100:.0f}"
                    body_txt = f"{pct_str} doldu ({s['spent']:,.0f}/{s['limit']:,.0f} TRY)"
                    toast(f"⚠ Bütçe Uyarısı: {label}", body_txt)
                    self._dispatch_push(
                        f"⚠ Bütçe Uyarısı: {label}",
                        body_txt,
                        {"category": "budget_alert", "budget_category": s["category"]},
                    )

            self._finance_ok = True
        except Exception as exc:
            logger.debug("Monitor finance check error: %s", exc)

    # ── Faz 15: ITU Webmail polling ────────────────────────────────────────────

    def _init_itu_mail_state(self) -> None:
        """Silently load current ITU unread UIDs to avoid startup spam."""
        user = getattr(self.settings, "itu_username", "")
        pwd  = getattr(self.settings, "itu_password", "")
        if not user or not pwd:
            return
        try:
            from imap_tools import MailBox, AND
            host = getattr(self.settings, "itu_imap_host", "imap.itu.edu.tr")
            port = int(getattr(self.settings, "itu_imap_port", 993))
            with MailBox(host, port).login(user, pwd, initial_folder="INBOX") as mb:
                msgs = list(mb.fetch(AND(seen=False), limit=50, bulk=True))
                for m in msgs:
                    self._notified_itu_ids.add(getattr(m, "uid", ""))
            logger.debug("Monitor: ITU mail state initialised (%d seen)", len(self._notified_itu_ids))
        except Exception as exc:
            logger.debug("Monitor ITU mail init failed: %s", exc)

    def _poll_itu_mail(self) -> None:
        """Check ITU inbox for new unread messages and fire toast."""
        user = getattr(self.settings, "itu_username", "")
        pwd  = getattr(self.settings, "itu_password", "")
        if not user or not pwd:
            return
        try:
            from jarvis.notify import toast
            from imap_tools import MailBox, AND
            host = getattr(self.settings, "itu_imap_host", "imap.itu.edu.tr")
            port = int(getattr(self.settings, "itu_imap_port", 993))
            with MailBox(host, port).login(user, pwd, initial_folder="INBOX") as mb:
                msgs = list(mb.fetch(AND(seen=False), limit=20, bulk=True, reverse=True))
            self._itu_mail_ok = True
            for m in msgs:
                uid = getattr(m, "uid", "")
                if not uid or uid in self._notified_itu_ids:
                    continue
                subject = (getattr(m, "subject", "") or "(konu yok)")[:60]
                sender  = (getattr(m, "from_", "") or "?")[:40]
                toast("📬 [ITU] Yeni E-posta", f"Kimden: {sender}\n{subject}")
                self._dispatch_push(
                    "📬 [ITU] Yeni E-posta",
                    f"Kimden: {sender} — {subject}",
                    {"category": "itu_email", "uid": uid},
                )
                self._notified_itu_ids.add(uid)
        except Exception as exc:
            logger.debug("Monitor ITU mail poll error: %s", exc)

    # ── Google service helpers ─────────────────────────────────────────────────

    # ── Faz 19A-0: FCM push dispatch ──────────────────────────────────────────

    def _dispatch_push(self, title: str, body: str, data: dict | None = None) -> None:
        """Fire-and-forget FCM push alongside a Windows toast."""
        try:
            if not getattr(self.settings, "push_enabled", True):
                return
            if self._fcm is None:
                from pathlib import Path
                from jarvis.push_store import PushStore
                from jarvis.fcm_sender import FcmSender
                db = Path("data/sessions.db")
                creds = getattr(self.settings, "firebase_credentials_path",
                                Path("data/firebase_admin_credentials.json"))
                self._push_store = PushStore(db)
                self._fcm = FcmSender(self._push_store, creds)
            self._fcm.send_to_all(title=title, body=body, data=data or {})
        except Exception as exc:
            logger.debug("Push dispatch error: %s", exc)

    def _gmail_service(self):
        from jarvis.tools.gmail import _get_service
        return _get_service(self.settings)

    def _calendar_service(self):
        from jarvis.tools.calendar import _get_service
        return _get_service(self.settings)

    # ── status ─────────────────────────────────────────────────────────────────

    def status_line(self) -> str:
        s = self.settings
        email_min    = getattr(s, "monitor_email_interval_min", 5)
        cal_min      = getattr(s, "monitor_calendar_interval_min", 2)
        lookahead    = getattr(s, "monitor_calendar_lookahead_min", 15)
        sched_sec    = getattr(s, "monitor_schedule_interval_sec", 60)
        itu_min      = getattr(s, "monitor_itu_mail_interval_min", 5)
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
        # ITU mail status
        itu_user = getattr(s, "itu_username", "")
        if itu_user:
            itu_state = "✓" if self._itu_mail_ok else "bekleniyor"
            itu_line = (
                f"\n  ITU mail kontrolü:  her {itu_min} dk  [{itu_state}]  "
                f"({len(self._notified_itu_ids)} bildirim gönderildi)"
            )
        else:
            itu_line = "\n  ITU mail:           devre dışı (ITU_USERNAME/.env ayarlı değil)"
        return (
            f"Monitor: [bold]{state}[/bold]\n"
            f"  E-posta kontrolü:   her {email_min} dk  [{email_state}]  "
            f"({len(self._notified_email_ids)} bildirim gönderildi)\n"
            f"  Takvim kontrolü:    her {cal_min} dk  [{cal_state}]  "
            f"(önce {lookahead} dk, {len(self._notified_event_ids)} etkinlik görüldü)\n"
            f"  Planlı görevler:    her {sched_sec} sn  [{sched_state}]  "
            f"({sched_count} aktif görev)"
            f"{itu_line}"
        )
