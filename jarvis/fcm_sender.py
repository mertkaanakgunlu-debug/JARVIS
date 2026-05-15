"""Firebase Cloud Messaging sender (Faz 19A-0).

Sends push notifications to all registered devices via Firebase Admin SDK.
Requires data/firebase_admin_credentials.json and firebase-admin>=6.5.0.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.push_store import PushStore

logger = logging.getLogger(__name__)

_firebase_app = None


def _get_app(credentials_path: Path):
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    try:
        import firebase_admin
        from firebase_admin import credentials as fb_creds
        if not credentials_path.exists():
            logger.warning("Firebase credentials not found at %s — push disabled", credentials_path)
            return None
        cred = fb_creds.Certificate(str(credentials_path))
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialised.")
        return _firebase_app
    except ImportError:
        logger.warning("firebase-admin not installed — push disabled")
        return None
    except Exception as exc:
        logger.warning("Firebase init failed: %s", exc)
        return None


class FcmSender:
    def __init__(self, push_store: "PushStore", credentials_path: Path) -> None:
        self._store = push_store
        self._creds_path = credentials_path

    def send_to_all(
        self,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> int:
        """Send a notification to all registered tokens.  Returns success count."""
        app = _get_app(self._creds_path)
        if app is None:
            return 0
        try:
            from firebase_admin import messaging
        except ImportError:
            return 0

        tokens = self._store.all_tokens()
        if not tokens:
            return 0

        sent = 0
        stale: list[str] = []
        for rec in tokens:
            try:
                msg = messaging.Message(
                    notification=messaging.Notification(title=title, body=body),
                    data={k: str(v) for k, v in (data or {}).items()},
                    token=rec["token"],
                    android=messaging.AndroidConfig(priority="high"),
                )
                messaging.send(msg)
                sent += 1
            except Exception as exc:
                err_str = str(exc)
                if "registration-token-not-registered" in err_str or "invalid-registration-token" in err_str:
                    stale.append(rec["token"])
                else:
                    logger.debug("FCM send failed for %s: %s", rec["device_id"], exc)

        for token in stale:
            self._store.remove_token(token)
            logger.debug("Removed stale FCM token.")

        return sent
