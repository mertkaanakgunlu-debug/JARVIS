"""One-time OAuth / credential setup helper for JARVIS.

Run from the repo root (venv active):
    python scripts/auth_setup.py

Checks and authorises:
  1. Google Calendar  → data/.calendar_token.json
  2. Google Drive     → data/.drive_token.json
  3. Spotify          → data/.spotify_cache
  4. ITU Webmail      → IMAP login test (reads ITU_USERNAME / ITU_PASSWORD from .env)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Load .env so Settings picks it up
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}!{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def hdr(msg):  print(f"\n{BOLD}-- {msg} --{RESET}")


# ── 1. Google Calendar ────────────────────────────────────────────────────────

def setup_calendar():
    hdr("Google Calendar")
    token = ROOT / "data" / ".calendar_token.json"
    creds_path = ROOT / "data" / "calendar_credentials.json"

    if not creds_path.exists():
        fail(f"credentials not found: {creds_path}")
        warn("Download Desktop OAuth credentials from console.cloud.google.com → APIs & Services → Credentials")
        return False

    if token.exists():
        ok("Token already exists — skipping browser flow")
        return True

    print("  Opening browser for Google Calendar consent…")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        SCOPES = ["https://www.googleapis.com/auth/calendar"]
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json())
        ok(f"Token saved → {token}")
        return True
    except Exception as e:
        fail(f"Calendar OAuth failed: {e}")
        return False


# ── 2. Google Drive ────────────────────────────────────────────────────────────

def setup_drive():
    hdr("Google Drive")
    token = ROOT / "data" / ".drive_token.json"
    creds_path = ROOT / "data" / "calendar_credentials.json"

    if not creds_path.exists():
        fail(f"credentials not found: {creds_path}")
        return False

    if token.exists():
        ok("Token already exists — skipping browser flow")
        return True

    print("  Opening browser for Google Drive consent…")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        SCOPES = [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.file",
        ]
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json())
        ok(f"Token saved → {token}")
        return True
    except Exception as e:
        fail(f"Drive OAuth failed: {e}")
        return False


# ── 3. Spotify ────────────────────────────────────────────────────────────────

def setup_spotify():
    hdr("Spotify")
    cache = ROOT / "data" / ".spotify_cache"

    client_id     = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    redirect_uri  = os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")

    if not client_id or not client_secret:
        fail("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set in .env")
        warn("Create an app at developer.spotify.com → Dashboard → add redirect URI http://localhost:8888/callback")
        return False

    if cache.exists():
        ok("Cache already exists — skipping browser flow")
        return True

    # Use a real local HTTP server on port 8888 to capture the callback automatically.
    # redirect_uri MUST be http://127.0.0.1:8888/callback in Spotify Dashboard.
    REDIRECT = "http://127.0.0.1:8888/callback"
    print(f"  redirect_uri: {REDIRECT}")
    print("  Make sure this is saved in your Spotify Dashboard → Settings → Redirect URIs")
    print()
    try:
        import http.server
        import threading
        import time
        import webbrowser
        from spotipy.oauth2 import SpotifyOAuth

        scope = "user-read-playback-state user-modify-playback-state"
        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=REDIRECT,
            scope=scope,
            cache_path=str(cache),
            open_browser=False,
        )

        _code: list = []
        _err: list  = []

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                import urllib.parse
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                if "code" in params:
                    _code.append(params["code"][0])
                    self.wfile.write(b"<h2>Authorised! Close this tab and return to the terminal.</h2>")
                elif "error" in params:
                    err = params["error"][0]
                    _err.append(err)
                    msg = f"<h2>Spotify error: {err}</h2><p>Check terminal for details.</p>".encode()
                    self.wfile.write(msg)
                else:
                    self.wfile.write(b"<h2>Waiting...</h2>")
            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 8888), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        auth_url = auth_manager.get_authorize_url()
        print("  Opening browser for Spotify consent…")
        webbrowser.open(auth_url)
        print("  Waiting for callback on http://127.0.0.1:8888/callback …")

        deadline = time.time() + 120
        while not _code and not _err and time.time() < deadline:
            time.sleep(0.4)
        server.shutdown()

        if _err:
            err = _err[0]
            fail(f"Spotify returned error: {err}")
            if err == "server_error":
                warn("This usually means your Spotify account is not added as a test user.")
                warn("Fix: Dashboard -> Settings -> User Management -> add your Spotify email.")
            return False

        if not _code:
            fail("Timeout (120 s) — no callback received. Check Dashboard redirect URI.")
            return False

        auth_manager.get_access_token(_code[0], as_dict=False, check_cache=False)
        ok(f"Cache saved → {cache}")
        return True
    except Exception as e:
        fail(f"Spotify OAuth failed: {e}")
        return False


# ── 4. ITU Webmail ────────────────────────────────────────────────────────────

def setup_itu():
    hdr("ITU Webmail (IMAP)")
    username = os.environ.get("ITU_USERNAME", "")
    password = os.environ.get("ITU_PASSWORD", "")

    if not username or not password:
        warn("ITU_USERNAME and/or ITU_PASSWORD not set in .env")
        warn("Add to .env:")
        print("      ITU_USERNAME=yourusername@itu.edu.tr")
        print("      ITU_PASSWORD=yourpassword")
        return False

    print(f"  Testing IMAP login as {username}…")
    try:
        from imap_tools import MailBox
        with MailBox("imap.itu.edu.tr", 993).login(username, password):
            ok(f"IMAP login successful ({username})")
        return True
    except Exception as e:
        fail(f"IMAP login failed: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"{BOLD}JARVIS OAuth Setup{RESET}")
    print("=" * 40)

    results = {
        "Google Calendar": setup_calendar(),
        "Google Drive":    setup_drive(),
        "Spotify":         setup_spotify(),
        "ITU Webmail":     setup_itu(),
    }

    print(f"\n{BOLD}-- Summary --{RESET}")
    for name, status in results.items():
        icon = f"{GREEN}✓{RESET}" if status else f"{RED}✗{RESET}"
        print(f"  {icon}  {name}")

    failed = [k for k, v in results.items() if not v]
    if not failed:
        print(f"\n{GREEN}All services authorised!{RESET}")
    else:
        print(f"\n{YELLOW}Pending: {', '.join(failed)}{RESET}")
        sys.exit(1)
