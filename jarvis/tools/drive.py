"""Google Drive integration via Google Drive API v3 (Faz 14).

Uses the same OAuth credentials as Google Calendar / Gmail
(data/calendar_credentials.json), separate token at data/.drive_token.json.

First run opens a browser for OAuth consent; subsequent runs use the cache.

Supported actions (via drive_control()):
    search   — search files by name/type/content
    list     — list folder contents
    read     — export Docs/Sheets/Slides as text; download PDFs to cache
    download — download any file to local path
    upload   — upload a local file to Drive
    share    — add sharing permissions
    delete   — move file to trash

PDF auto-pipeline:
    drive_control("read", file_id=X) for a PDF returns the local cached path.
    The agent can then immediately call pdf_read(path) or pdf_vision(path, ...).
"""

from __future__ import annotations

import io
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]


def _token_file() -> Path:
    # Was a cwd-relative module constant — inconsistent with gmail/calendar's
    # project-root anchoring AND invisible to JARVIS_HOME. Both fixed here.
    from jarvis import paths
    return paths.project_data_dir() / ".drive_token.json"


def _cache_dir() -> Path:
    from jarvis import paths
    return paths.data_dir() / "drive_cache"

# Google Workspace MIME types → export format
_EXPORT_MAP = {
    "application/vnd.google-apps.document":     ("text/plain",                     ".txt"),
    "application/vnd.google-apps.spreadsheet":  ("text/csv",                       ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain",                     ".txt"),
    "application/vnd.google-apps.drawing":      ("image/svg+xml",                  ".svg"),
}

# Human-readable MIME short names for display
_MIME_LABELS = {
    "application/vnd.google-apps.document":     "Google Doc",
    "application/vnd.google-apps.spreadsheet":  "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/pdf":                          "PDF",
    "application/vnd.google-apps.folder":       "Folder",
}


# ── OAuth helper ──────────────────────────────────────────────────────────────

def _get_service(settings: "Settings"):
    from jarvis import paths
    creds_path = paths.resolve_project(settings.google_calendar_creds_file)
    if not creds_path.exists():
        raise RuntimeError(
            f"Google OAuth credentials not found at '{creds_path}'. "
            "Download Desktop app credentials from console.cloud.google.com "
            "→ APIs & Services → Credentials, then save to data/calendar_credentials.json."
        )
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "Google API packages not installed. Run:\n"
            "  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )

    creds = None
    token_file = _token_file()
    token_file.parent.mkdir(parents=True, exist_ok=True)

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


# ── Drive API helpers ─────────────────────────────────────────────────────────

def _file_info(f: dict) -> str:
    """One-line human summary for a Drive file dict."""
    name    = f.get("name", "?")
    fid     = f.get("id", "?")
    mime    = f.get("mimeType", "")
    label   = _MIME_LABELS.get(mime, mime.split("/")[-1] if "/" in mime else mime)
    size    = f.get("size")
    size_s  = f" ({int(size)//1024} KB)" if size else ""
    modified = (f.get("modifiedTime") or "")[:10]
    return f"[{fid}]  {name}  ({label}{size_s})  modified: {modified}"


def _ensure_cache() -> Path:
    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


# ── Main control function ─────────────────────────────────────────────────────

def drive_control(
    action: str,
    *,
    query: str = "",
    folder_id: str = "",
    file_id: str = "",
    dest_path: str = "",
    local_path: str = "",
    name: str = "",
    email: str = "",
    role: str = "reader",
    max_results: int = 20,
    settings: "Settings",
) -> str:
    action = action.strip().lower()

    # ── search ────────────────────────────────────────────────────────────────
    if action == "search":
        if not query:
            return "⚠ query gerekli."
        svc = _get_service(settings)
        # Build Drive query: support raw q syntax or plain keyword
        if any(op in query for op in ("=", "contains", "mimeType", "parent", "and ", "or ")):
            q = query
        else:
            q = f"name contains '{query}' and trashed=false"
        results = svc.files().list(
            q=q,
            pageSize=max_results,
            fields="files(id,name,mimeType,size,modifiedTime,parents)",
        ).execute()
        files = results.get("files", [])
        if not files:
            return f"Sonuç bulunamadı: {query!r}"
        lines = [f"Arama sonuçları ({len(files)} dosya):"]
        for f in files:
            lines.append("  " + _file_info(f))
        return "\n".join(lines)

    # ── list ──────────────────────────────────────────────────────────────────
    if action == "list":
        svc = _get_service(settings)
        parent = folder_id or "root"
        q = f"'{parent}' in parents and trashed=false"
        results = svc.files().list(
            q=q,
            pageSize=min(max_results, 50),
            fields="files(id,name,mimeType,size,modifiedTime)",
            orderBy="folder,name",
        ).execute()
        files = results.get("files", [])
        if not files:
            folder_label = folder_id or "root"
            return f"Klasör boş: {folder_label}"
        lines = [f"Klasör içeriği ({len(files)} öğe):"]
        for f in files:
            lines.append("  " + _file_info(f))
        return "\n".join(lines)

    # ── read ──────────────────────────────────────────────────────────────────
    if action == "read":
        if not file_id:
            return "⚠ file_id gerekli."
        svc = _get_service(settings)
        meta = svc.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size",
        ).execute()
        mime  = meta.get("mimeType", "")
        fname = meta.get("name", file_id)

        # Google Workspace files → export as text
        if mime in _EXPORT_MAP:
            export_mime, ext = _EXPORT_MAP[mime]
            resp = svc.files().export(fileId=file_id, mimeType=export_mime).execute()
            text = resp.decode("utf-8", errors="replace") if isinstance(resp, bytes) else str(resp)
            label = _MIME_LABELS.get(mime, "Google file")
            preview = text[:4000]
            suffix = f"\n...[truncated, {len(text)} chars total]" if len(text) > 4000 else ""
            return f"[{label}: {fname}]\n\n{preview}{suffix}"

        # PDF or other binary → download to cache, return path for pdf_read / pdf_vision
        cache_dir = _ensure_cache()
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in fname)
        dest = cache_dir / safe_name
        request = svc.files().get_media(fileId=file_id)
        from googleapiclient.http import MediaIoBaseDownload
        fh = io.FileIO(str(dest), "wb")
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.close()

        if mime == "application/pdf":
            return (
                f"📄 PDF indirildi: {fname}\n"
                f"Yerel yol: {dest}\n"
                "Okumak için: pdf_read(path) veya pdf_vision(path, question)"
            )
        # Other binary
        return f"📁 Dosya indirildi: {dest}"

    # ── download ──────────────────────────────────────────────────────────────
    if action == "download":
        if not file_id:
            return "⚠ file_id gerekli."
        svc = _get_service(settings)
        meta = svc.files().get(fileId=file_id, fields="id,name,mimeType").execute()
        fname = meta.get("name", file_id)
        mime  = meta.get("mimeType", "")

        if dest_path:
            dest = Path(dest_path)
        else:
            cache_dir = _ensure_cache()
            safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in fname)
            dest = cache_dir / safe_name

        dest.parent.mkdir(parents=True, exist_ok=True)

        # Google Workspace → export
        if mime in _EXPORT_MAP:
            export_mime, ext = _EXPORT_MAP[mime]
            resp = svc.files().export(fileId=file_id, mimeType=export_mime).execute()
            data = resp if isinstance(resp, bytes) else resp.encode("utf-8")
            if not dest.suffix:
                dest = dest.with_suffix(ext)
            dest.write_bytes(data)
        else:
            from googleapiclient.http import MediaIoBaseDownload
            request = svc.files().get_media(fileId=file_id)
            fh = io.FileIO(str(dest), "wb")
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.close()

        return f"✅ İndirildi: {fname} → {dest}"

    # ── upload ────────────────────────────────────────────────────────────────
    if action == "upload":
        if not local_path:
            return "⚠ local_path gerekli."
        src = Path(local_path)
        if not src.exists():
            return f"⚠ Dosya bulunamadı: {local_path}"
        svc = _get_service(settings)
        upload_name = name or src.name
        mime_type = mimetypes.guess_type(str(src))[0] or "application/octet-stream"
        file_meta: dict = {"name": upload_name}
        if folder_id:
            file_meta["parents"] = [folder_id]
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(str(src), mimetype=mime_type, resumable=True)
        result = svc.files().create(
            body=file_meta,
            media_body=media,
            fields="id,name,webViewLink",
        ).execute()
        fid  = result.get("id", "?")
        link = result.get("webViewLink", "")
        return (
            f"✅ Yüklendi: {upload_name}\n"
            f"  ID: {fid}\n"
            f"  Link: {link}"
        )

    # ── share ─────────────────────────────────────────────────────────────────
    if action == "share":
        if not file_id:
            return "⚠ file_id gerekli."
        if not email:
            return "⚠ email gerekli."
        svc = _get_service(settings)
        perm = {"type": "user", "role": role, "emailAddress": email}
        svc.permissions().create(
            fileId=file_id,
            body=perm,
            sendNotificationEmail=False,
        ).execute()
        return f"✅ Paylaşıldı: {file_id} → {email} ({role})"

    # ── delete (trash) ────────────────────────────────────────────────────────
    if action == "delete":
        if not file_id:
            return "⚠ file_id gerekli."
        svc = _get_service(settings)
        svc.files().update(fileId=file_id, body={"trashed": True}).execute()
        return f"🗑 Çöp kutusuna taşındı: {file_id}"

    return (
        f"⚠ Bilinmeyen action: '{action}'. "
        "Geçerli: search, list, read, download, upload, share, delete"
    )
