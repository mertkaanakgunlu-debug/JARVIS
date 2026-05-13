"""Entry point: python -m jarvis [--voice] [--api]"""

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
    args = parser.parse_args()

    if args.api:
        from jarvis.config import Settings
        from jarvis.api import run_server
        settings = Settings()
        port = args.port or settings.jarvis_api_port
        run_server(settings, port=port)
    else:
        run(voice=args.voice or args.wakeword, wakeword=args.wakeword)


if __name__ == "__main__":
    main()
