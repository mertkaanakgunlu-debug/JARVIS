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


# ── Shared Google OAuth helper ────────────────────────────────────────────────
#
# 2026-07-30: the three Google setups were copy-pasted and shared two flaws that
# together hid a dead credential for months.
#
#   1. Gmail had NO setup function at all -- the one credential that actually
#      died (invalid_grant, found live) was the one with no supported way to
#      re-authorize it.
#   2. "if token.exists(): skip" treats a REVOKED token as healthy. A token file
#      is not authorization; only a successful refresh is. The owner's
#      .gmail_token.json existed the whole time it was dead.
#
# This helper checks real validity and re-runs consent when the token cannot be
# refreshed. Pass --force (or name a service) to re-authorize regardless.

GOOGLE_SERVICES = {
    "calendar": (
        "Google Calendar", ".calendar_token.json",
        ["https://www.googleapis.com/auth/calendar"],
    ),
    "drive": (
        "Google Drive", ".drive_token.json",
        ["https://www.googleapis.com/auth/drive",
         "https://www.googleapis.com/auth/drive.file"],
    ),
    "gmail": (
        "Gmail", ".gmail_token.json",
        ["https://www.googleapis.com/auth/gmail.readonly",
         "https://www.googleapis.com/auth/gmail.send",
         "https://www.googleapis.com/auth/gmail.modify"],
    ),
}


def _token_is_usable(token_path, scopes):
    """True only if the stored token is valid or can actually be refreshed.

    Returns (usable, detail). Refreshing here is deliberate: it is the only way
    to distinguish a live grant from a revoked one, and a successful refresh
    writes the renewed token back, which is exactly what we want anyway.
    """
    if not token_path.exists():
        return False, "no token file"
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        return False, f"google auth libraries missing ({exc})"

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    except Exception as exc:
        return False, f"token unreadable ({exc})"

    if creds.valid:
        return True, "token valid"
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            return False, f"refresh rejected by Google ({exc})"
        except Exception as exc:
            return False, f"refresh failed ({exc})"
        try:
            token_path.write_text(creds.to_json())
        except OSError as exc:
            warn(f"refreshed but could not save token: {exc}")
        return True, "token refreshed"
    return False, "token has no usable refresh_token"


def setup_google(service, force=False):
    label, token_name, scopes = GOOGLE_SERVICES[service]
    hdr(label)
    token = ROOT / "data" / token_name
    creds_path = ROOT / "data" / "calendar_credentials.json"

    if not creds_path.exists():
        fail(f"credentials not found: {creds_path}")
        warn("Download Desktop OAuth credentials from console.cloud.google.com "
             "→ APIs & Services → Credentials")
        return False

    if not force:
        usable, detail = _token_is_usable(token, scopes)
        if usable:
            ok(f"Authorized ({detail})")
            return True
        warn(f"Existing token unusable: {detail}")

    print(f"  Opening browser for {label} consent…")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
        creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json())
        ok(f"Token saved → {token}")
        return True
    except Exception as e:
        fail(f"{label} OAuth failed: {e}")
        return False


def setup_calendar(force=False):
    return setup_google("calendar", force)


def setup_drive(force=False):
    return setup_google("drive", force)


def setup_gmail(force=False):
    return setup_google("gmail", force)


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

ALL_SERVICES = ("calendar", "drive", "gmail", "spotify", "itu")

if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    force = "--force" in args
    wanted = [a.lower() for a in args if not a.startswith("-")]
    unknown = [w for w in wanted if w not in ALL_SERVICES]
    if unknown or "--help" in args or "-h" in args:
        if unknown:
            print(f"Unknown service(s): {', '.join(unknown)}")
        print(f"Usage: python scripts/auth_setup.py [{'|'.join(ALL_SERVICES)}] [--force]")
        print("  no service  -> check/repair all")
        print("  --force     -> re-run consent even if the token still works")
        sys.exit(0 if not unknown else 2)

    selected = wanted or list(ALL_SERVICES)
    # Naming a service implies you want it re-done, not merely inspected.
    force_google = force or bool(wanted)

    print(f"{BOLD}JARVIS OAuth Setup{RESET}")
    print("=" * 40)

    runners = {
        "calendar": ("Google Calendar", lambda: setup_calendar(force_google)),
        "drive":    ("Google Drive",    lambda: setup_drive(force_google)),
        "gmail":    ("Gmail",           lambda: setup_gmail(force_google)),
        "spotify":  ("Spotify",         setup_spotify),
        "itu":      ("ITU Webmail",     setup_itu),
    }
    results = {}
    for key in selected:
        label, runner = runners[key]
        results[label] = runner()

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
