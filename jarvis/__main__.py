"""Entry point: python -m jarvis [--voice]"""

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
    args = parser.parse_args()
    run(voice=args.voice)


if __name__ == "__main__":
    main()
