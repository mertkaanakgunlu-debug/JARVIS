"""Windows toast notification helper (Faz 10).

Uses winotify on Windows 10/11; falls back to console print when unavailable.
"""

from __future__ import annotations


def toast(title: str, message: str, duration: str = "short") -> None:
    """Show a Windows toast notification. Silently falls back to console if unavailable."""
    try:
        from winotify import Notification

        notif = Notification(
            app_id="JARVIS",
            title=title,
            msg=message,
            duration=duration,
        )
        notif.show()
        return
    except Exception:
        pass
    # Console fallback (headless / winotify not installed)
    print(f"\n[JARVIS] {title} — {message}\n")
