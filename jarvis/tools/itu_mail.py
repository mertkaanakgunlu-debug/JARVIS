"""ITU Webmail integration via IMAP + SMTP (Faz 15).

Connects to ITU's mail server using standard IMAP (read) and SMTP (send).
Credentials stored in .env:
    ITU_USERNAME = mertk@itu.edu.tr        (or akgunlu22@itu.edu.tr)
    ITU_PASSWORD = <your-password>

Server defaults (override in .env):
    ITU_IMAP_HOST = imap.itu.edu.tr   port 993 (SSL)
    ITU_SMTP_HOST = smtp.itu.edu.tr   port 587 (STARTTLS)

Uses imap-tools library for clean IMAP parsing/threading.

Supported actions (via itu_mail_control()):
    list_unread — list unread messages in INBOX
    search      — search messages (FROM, SUBJECT, BODY, SINCE keywords)
    read        — read full message body by UID
    send        — send a new email via SMTP
    reply       — reply to an existing message (preserves threading)
    trash       — move message to Trash/Deleted
    mark_read   — mark message as read (\\Seen flag)
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)

# ── IMAP helpers ──────────────────────────────────────────────────────────────

def _get_imap(settings: "Settings"):
    """Return a connected MailBox (imap_tools) using SSL."""
    try:
        from imap_tools import MailBox
    except ImportError:
        raise RuntimeError(
            "imap-tools not installed. Run: pip install imap-tools"
        )
    host = getattr(settings, "itu_imap_host", "imap.itu.edu.tr")
    port = int(getattr(settings, "itu_imap_port", 993))
    user = getattr(settings, "itu_username", "")
    pwd  = getattr(settings, "itu_password", "")
    if not user or not pwd:
        raise RuntimeError(
            "ITU credentials not set. Add to .env:\n"
            "  ITU_USERNAME=your@itu.edu.tr\n"
            "  ITU_PASSWORD=your_password"
        )
    mb = MailBox(host, port)
    try:
        mb.login(user, pwd, initial_folder="INBOX")
    except Exception:
        # MailBox(host, port) already opened the socket/SSL handshake in __init__ —
        # if login fails, that connection is never handed back via `with`'s __exit__
        # (the with-statement never got a chance to start), so close it explicitly
        # or it leaks until GC/server-side timeout.
        try:
            mb.logout()
        except Exception:
            pass
        raise
    return mb


def _msg_summary(msg) -> str:
    """One-line summary for a MailMessage."""
    uid     = getattr(msg, "uid", "?")
    subject = (getattr(msg, "subject", "") or "(no subject)")[:70]
    from_   = (getattr(msg, "from_", "") or "?")[:40]
    date    = getattr(msg, "date", None)
    date_s  = date.strftime("%Y-%m-%d %H:%M") if date else "?"
    return f"[{uid}]  {date_s}  {from_}  /  {subject}"


def _find_trash_folder(mb) -> str:
    """Try to detect the Trash/Deleted folder name."""
    for candidate in ("Trash", "Deleted Items", "Deleted Messages", "[Gmail]/Trash", "INBOX.Trash"):
        try:
            mb.folder.set(candidate)
            return candidate
        except Exception:
            pass
    return "Trash"  # best guess


# ── SMTP helper ───────────────────────────────────────────────────────────────

def _send_smtp(
    to: str,
    subject: str,
    body: str,
    settings: "Settings",
    in_reply_to: str = "",
    references: str = "",
    cc: str = "",
) -> None:
    host = getattr(settings, "itu_smtp_host", "smtp.itu.edu.tr")
    port = int(getattr(settings, "itu_smtp_port", 587))
    user = getattr(settings, "itu_username", "")
    pwd  = getattr(settings, "itu_password", "")
    if not user or not pwd:
        raise RuntimeError("ITU_USERNAME / ITU_PASSWORD not set in .env")

    msg = MIMEMultipart("alternative")
    msg["From"]    = user
    msg["To"]      = to
    msg["Subject"] = subject
    msg["Date"]    = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=user.split("@")[-1] if "@" in user else "itu.edu.tr")
    if cc:
        msg["Cc"] = cc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"]  = references or in_reply_to

    msg.attach(MIMEText(body, "plain", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(user, pwd)
        recipients = [r.strip() for r in to.split(",")]
        if cc:
            recipients += [r.strip() for r in cc.split(",")]
        server.sendmail(user, recipients, msg.as_string())


# ── Main control function ─────────────────────────────────────────────────────

def itu_mail_control(
    action: str,
    *,
    max_results: int = 20,
    query: str = "",
    uid: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    cc: str = "",
    reply_all: bool = False,
    settings: "Settings",
) -> str:
    action = action.strip().lower()

    # ── list_unread ───────────────────────────────────────────────────────────
    if action == "list_unread":
        from imap_tools import AND
        with _get_imap(settings) as mb:
            msgs = list(mb.fetch(AND(seen=False), limit=max_results, reverse=True))
        if not msgs:
            return "📭 ITU inbox'ta okunmamış mesaj yok."
        lines = [f"📬 Okunmamış ({len(msgs)}):"]
        for m in msgs:
            lines.append("  " + _msg_summary(m))
        return "\n".join(lines)

    # ── search ────────────────────────────────────────────────────────────────
    if action == "search":
        if not query:
            return "⚠ query gerekli."
        from imap_tools import AND, A
        # Parse simple key=value pairs or raw string
        # Support: FROM:xxx SUBJECT:xxx BODY:xxx SINCE:YYYY-MM-DD
        criteria = {}
        remaining = query
        for kw in ("FROM", "SUBJECT", "BODY", "SINCE", "TO"):
            import re
            m = re.search(rf"(?:^|[\s,]){kw}:([^\s,]+)", query, re.I)
            if m:
                val = m.group(1)
                criteria[kw.lower()] = val
                remaining = remaining.replace(m.group(0), "").strip()
        if not criteria and remaining:
            # Fall back: search in subject + body
            criteria["subject"] = remaining

        # Build imap_tools query
        kwargs: dict = {}
        if "from" in criteria:
            kwargs["from_"] = criteria["from"]
        if "to" in criteria:
            kwargs["to"] = criteria["to"]
        if "subject" in criteria:
            kwargs["subject"] = criteria["subject"]
        if "body" in criteria:
            kwargs["text"] = criteria["body"]
        if "since" in criteria:
            try:
                since_date = datetime.fromisoformat(criteria["since"]).date()
                kwargs["date_gte"] = since_date
            except ValueError:
                pass

        with _get_imap(settings) as mb:
            msgs = list(mb.fetch(AND(**kwargs) if kwargs else "ALL", limit=max_results, reverse=True))

        if not msgs:
            return f"Sonuç bulunamadı: {query!r}"
        lines = [f"Arama sonuçları ({len(msgs)}):"]
        for m in msgs:
            lines.append("  " + _msg_summary(m))
        return "\n".join(lines)

    # ── read ──────────────────────────────────────────────────────────────────
    if action == "read":
        if not uid:
            return "⚠ uid gerekli."
        from imap_tools import AND, U
        with _get_imap(settings) as mb:
            msgs = list(mb.fetch(U(uid)))
        if not msgs:
            return f"⚠ Mesaj bulunamadı: uid={uid}"
        msg = msgs[0]
        from_   = getattr(msg, "from_", "?")
        date    = getattr(msg, "date", None)
        date_s  = date.strftime("%Y-%m-%d %H:%M") if date else "?"
        subj    = getattr(msg, "subject", "(no subject)")
        text    = getattr(msg, "text", "") or ""
        html_fallback = getattr(msg, "html", "") or ""
        content = text.strip() if text.strip() else _html_to_text(html_fallback)
        preview = content[:6000]
        suffix  = f"\n...[{len(content)} karakter, kesildi]" if len(content) > 6000 else ""
        msg_id  = getattr(msg, "headers", {}).get("message-id", [""])[0]
        return (
            f"[ITU Mail uid={uid}]\n"
            f"Kimden: {from_}\n"
            f"Tarih:  {date_s}\n"
            f"Konu:   {subj}\n"
            f"---\n{preview}{suffix}\n"
            f"[message-id: {msg_id}]"
        )

    # ── send ──────────────────────────────────────────────────────────────────
    if action == "send":
        if not to:
            return "⚠ to gerekli."
        if not subject:
            return "⚠ subject gerekli."
        if not body:
            return "⚠ body gerekli."
        _send_smtp(to, subject, body, settings, cc=cc)
        return f"✅ Mail gönderildi → {to}  |  Konu: {subject}"

    # ── reply ─────────────────────────────────────────────────────────────────
    if action == "reply":
        if not uid:
            return "⚠ uid gerekli."
        if not body:
            return "⚠ body gerekli."
        from imap_tools import U
        with _get_imap(settings) as mb:
            msgs = list(mb.fetch(U(uid)))
        if not msgs:
            return f"⚠ Mesaj bulunamadı: uid={uid}"
        orig = msgs[0]
        orig_subject = getattr(orig, "subject", "") or ""
        orig_from    = getattr(orig, "from_", "") or ""
        orig_to      = getattr(orig, "to", [])
        orig_msg_id  = getattr(orig, "headers", {}).get("message-id", [""])[0]
        reply_subj = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
        if reply_all:
            user = getattr(settings, "itu_username", "")
            all_to = [a for a in ([orig_from] + list(orig_to)) if a and a != user]
            reply_to = ", ".join(dict.fromkeys(all_to))
        else:
            reply_to = orig_from
        _send_smtp(reply_to, reply_subj, body, settings,
                   in_reply_to=orig_msg_id, references=orig_msg_id)
        return f"✅ Yanıt gönderildi → {reply_to}"

    # ── trash ─────────────────────────────────────────────────────────────────
    if action == "trash":
        if not uid:
            return "⚠ uid gerekli."
        with _get_imap(settings) as mb:
            trash = _find_trash_folder(mb)
            mb.folder.set("INBOX")
            mb.move(uid, trash)
        return f"🗑 Mesaj taşındı (Trash): uid={uid}"

    # ── mark_read ─────────────────────────────────────────────────────────────
    if action == "mark_read":
        if not uid:
            return "⚠ uid gerekli."
        from imap_tools import MailMessageFlags
        with _get_imap(settings) as mb:
            mb.flag(uid, MailMessageFlags.SEEN, True)
        return f"✅ Okundu işaretlendi: uid={uid}"

    return (
        f"⚠ Bilinmeyen action: '{action}'. "
        "Geçerli: list_unread, search, read, send, reply, trash, mark_read"
    )


def _html_to_text(html: str) -> str:
    """Strip HTML tags for plain text fallback."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
