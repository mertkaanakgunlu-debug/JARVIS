"""Entry point: python -m jarvis [--voice] [--api] [--monitor] [--profile test]"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

# Ensure the project root is on the path when run as a module
sys.path.insert(0, str(Path(__file__).parent.parent))

# --profile test (stabilization sprint): an isolated E2E profile that must
# never touch the real .env, the real data/ or vault/ directories, real OAuth
# tokens, or make a cloud LLM call. This decision has to happen BEFORE the
# load_dotenv() call below -- but --profile is only known after argparse
# runs inside main(), and argparse can't run until after this module finishes
# loading. A lightweight argv pre-scan breaks that ordering deadlock; the
# real, validating parse still happens in main() via argparse's own
# --profile flag below (this pre-scan only decides load_dotenv/env seeding).
def _prescan_test_profile() -> bool:
    if "--profile" not in sys.argv:
        return False
    idx = sys.argv.index("--profile")
    return idx + 1 < len(sys.argv) and sys.argv[idx + 1] == "test"


if _prescan_test_profile():
    # Real .env is never read -- neither here (load_dotenv skipped outright)
    # nor by Settings' own independent env_file=".env" reader (config.py
    # honors this same flag). JARVIS_HOME isolates every runtime store
    # (see jarvis/paths.py); CLOUD_POLICY=off + EXTERNAL_WRITES_ENABLED=false
    # give the structural zero-cloud / zero-external-side-effect guarantee.
    os.environ["JARVIS_SKIP_DOTENV"] = "1"
    os.environ.setdefault("JARVIS_HOME", tempfile.mkdtemp(prefix="jarvis-e2e-"))
    os.environ["CLOUD_POLICY"] = "off"
    os.environ["EXTERNAL_WRITES_ENABLED"] = "false"
else:
    # Load .env before any settings are read
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

from jarvis.cli import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. personal assistant")
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Start in voice mode (VAD auto-detect + TTS)",
    )
    parser.add_argument(
        "--wakeword",
        action="store_true",
        help='Enable wake-word mode: say "Hey JARVIS" before each turn (requires --voice)',
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Start as FastAPI REST server (default port 8000)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for --api mode (overrides JARVIS_API_PORT in .env)",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help=(
            "Enable proactive monitoring: polls Gmail + Google Calendar and fires "
            "Windows toast notifications. When used alone (no --voice / --api) runs "
            "as a standalone background watcher. Combine with --voice for monitor + chat."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=["default", "test"],
        default="default",
        help=(
            "test: isolated E2E profile for manual/scripted testing -- real .env "
            "is never read, a fresh temp JARVIS_HOME replaces data/vault (see "
            "jarvis/paths.py), CLOUD_POLICY=off (zero cloud LLM calls), and "
            "EXTERNAL_WRITES_ENABLED=false (email/calendar/Drive sends are hard-"
            "denied before the confirmation gate). Must be decided before argv "
            "parsing even runs -- see _prescan_test_profile() above."
        ),
    )
    args = parser.parse_args()

    if args.profile == "test":
        # Plain ASCII only: Rich's legacy Windows console renderer (used
        # whenever stdout is redirected/piped, e.g. into a log file for a
        # scripted test run) encodes against the raw console codepage
        # (cp1254 etc.), not UTF-8 -- a stray non-ASCII glyph here crashed
        # startup outright under exactly that redirection, confirmed live.
        from rich.console import Console as _Console
        _Console().print(
            "[bold yellow]--profile test[/bold yellow] - isolated E2E run\n"
            f"  [dim]JARVIS_HOME:[/dim]      {os.environ.get('JARVIS_HOME')}\n"
            f"  [dim]CLOUD_POLICY:[/dim]     {os.environ.get('CLOUD_POLICY')}\n"
            "  [dim]external writes:[/dim]  disabled\n"
            "  [dim]real .env:[/dim]        NOT read"
        )

    if args.api:
        from jarvis.config import Settings
        from jarvis.api import run_server
        settings = Settings()
        port = args.port or settings.jarvis_api_port
        run_server(settings, port=port, voice=args.voice, wakeword=args.wakeword, monitor=args.monitor)
    elif args.monitor and not args.voice and not args.wakeword:
        # Standalone monitor: no chat interface, just watch + notify
        from jarvis.config import Settings
        from jarvis.monitor import JarvisMonitor
        from jarvis.scheduler import SchedulerStore
        from jarvis.todo_store import TodoStore
        from jarvis import paths
        from rich.console import Console
        settings = Settings()
        db = paths.data_dir() / "sessions.db"
        scheduler = SchedulerStore(db)
        todo_store = TodoStore(db)
        console = Console()
        console.print(
            "[bold gold3]JARVIS Monitor[/bold gold3] — "
            f"e-posta: her [bold]{settings.monitor_email_interval_min}[/bold] dk  ·  "
            f"takvim: her [bold]{settings.monitor_calendar_interval_min}[/bold] dk  ·  "
            f"önce [bold]{settings.monitor_calendar_lookahead_min}[/bold] dk uyarı  ·  "
            f"zamanlayıcı: her [bold]{settings.monitor_schedule_interval_sec}[/bold] sn\n"
            "[dim]Durdurmak için Ctrl+C[/dim]"
        )
        monitor = JarvisMonitor(settings, scheduler=scheduler, todo_store=todo_store)
        try:
            monitor.run_forever()
        except KeyboardInterrupt:
            console.print("\n[dim]Monitor durduruldu.[/dim]")
    else:
        run(voice=args.voice or args.wakeword, wakeword=args.wakeword, monitor=args.monitor)


if __name__ == "__main__":
    main()
