"""Spotify playback control via spotipy (Faz 8).

Requires a Spotify Developer app — create one at https://developer.spotify.com.
Set in .env:
    SPOTIFY_CLIENT_ID=...
    SPOTIFY_CLIENT_SECRET=...
    SPOTIFY_REDIRECT_URI=http://localhost:8888/callback

First run opens a browser for OAuth; token is cached at data/.spotify_cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

_SCOPE = (
    "user-read-playback-state "
    "user-modify-playback-state "
    "user-read-currently-playing "
    "streaming"
)


def _get_client(settings: "Settings"):
    """Return an authenticated spotipy.Spotify client or raise RuntimeError."""
    if not (settings.spotify_client_id and settings.spotify_client_secret):
        raise RuntimeError(
            "Spotify not configured. Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env. "
            "Create an app at https://developer.spotify.com (free)."
        )
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        raise RuntimeError("spotipy not installed. Run: pip install spotipy")

    cache_path = Path("data") / ".spotify_cache"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    auth = SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=_SCOPE,
        cache_path=str(cache_path),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)


def _active_device_id(sp) -> str | None:
    """Return the ID of the currently active Spotify device, or None."""
    devices = sp.devices().get("devices", [])
    for d in devices:
        if d.get("is_active"):
            return d["id"]
    return devices[0]["id"] if devices else None


def spotify_control(action: str, query: str = "", settings: "Settings" = None) -> str:
    """Execute a Spotify playback action.

    Actions:
      play <query>  — search and play a track/album/artist
      pause         — pause playback
      resume        — resume paused playback
      next          — skip to next track
      previous      — go back to previous track
      current       — show currently playing track
    """
    try:
        sp = _get_client(settings)
    except RuntimeError as e:
        return f"[Spotify] {e}"

    action = action.lower().strip()
    try:
        if action == "current":
            info = sp.current_playback()
            if not info or not info.get("is_playing"):
                return "[Spotify] Nothing is currently playing."
            item = info["item"]
            track = item["name"]
            artists = ", ".join(a["name"] for a in item["artists"])
            progress_ms = info.get("progress_ms", 0)
            duration_ms = item.get("duration_ms", 1)
            pct = int(progress_ms / duration_ms * 100)
            return f"[Spotify] Now playing: {track} by {artists} ({pct}%)"

        elif action == "pause":
            sp.pause_playback()
            return "[Spotify] Paused."

        elif action in ("resume", "play") and not query:
            sp.start_playback()
            return "[Spotify] Resumed."

        elif action in ("play",) and query:
            results = sp.search(q=query, type="track", limit=1)
            tracks = results.get("tracks", {}).get("items", [])
            if not tracks:
                return f"[Spotify] No results for: {query}"
            track = tracks[0]
            uri = track["uri"]
            name = track["name"]
            artist = track["artists"][0]["name"]
            device_id = _active_device_id(sp)
            sp.start_playback(device_id=device_id, uris=[uri])
            return f"[Spotify] Playing: {name} by {artist}"

        elif action == "next":
            sp.next_track()
            return "[Spotify] Skipped to next track."

        elif action in ("previous", "prev", "back"):
            sp.previous_track()
            return "[Spotify] Went to previous track."

        else:
            return (
                f"[Spotify] Unknown action '{action}'. "
                "Valid: play <query>, pause, resume, next, previous, current."
            )
    except Exception as e:
        return f"[Spotify] Error: {e}"
