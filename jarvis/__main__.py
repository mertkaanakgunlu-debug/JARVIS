"""Entry point: python -m jarvis [--voice] [--api] [--monitor]"""

import argparse
import sys
from pathlib import Path

# Ensure the project root is on the path when run as a module
sys.path.insert(0, str(Path(__file__).parent.parent))

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
    args = parser.parse_args()

    if args.api:
        from jarvis.config import Settings
        from jarvis.api import run_server
        settings = Settings()
        port = args.port or settings.jarvis_api_port
        run_server(settings, port=port)
    elif args.monitor and not args.voice and not args.wakeword:
        # Standalone monitor: no chat interface, just watch + notify
        from jarvis.config import Settings
        from jarvis.monitor import JarvisMonitor
        from rich.console import Console
        settings = Settings()
        console = Console()
        console.print(
            "[bold gold3]JARVIS Monitor[/bold gold3] — "
            f"e-posta: her [bold]{settings.monitor_email_interval_min}[/bold] dk  ·  "
            f"takvim: her [bold]{settings.monitor_calendar_interval_min}[/bold] dk  ·  "
            f"önce [bold]{settings.monitor_calendar_lookahead_min}[/bold] dk uyarı\n"
            "[dim]Durdurmak için Ctrl+C[/dim]"
        )
        monitor = JarvisMonitor(settings)
        try:
            monitor.run_forever()
        except KeyboardInterrupt:
            console.print("\n[dim]Monitor durduruldu.[/dim]")
    else:
        run(voice=args.voice or args.wakeword, wakeword=args.wakeword, monitor=args.monitor)


if __name__ == "__main__":
    main()
