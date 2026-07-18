"""A/B server launcher (Faz 3.3 harness; also the Faz 4 challenger harness).

Sets LOCAL_REASONING_EFFORT through os.environ before starting the API
server, because the Win32 environment block silently DELETES an
empty-string variable — `LOCAL_REASONING_EFFORT=""` (thinking ON) is
impossible to pass through normal shell env on Windows, while Python's
os.environ mapping keeps empty strings and pydantic-settings reads that
mapping. Verified live in the 2026-07-18 A/B.

Usage: python scripts/ab_launch_server.py <effort> [port]
  effort: "none" (thinking off) | "" (thinking on) | any reasoning_effort
  port:   default 8132
"""
import os
import runpy
import sys
from pathlib import Path

# sys.path[0] is scripts/ when run as a file; the jarvis package lives one up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

effort = sys.argv[1] if len(sys.argv) > 1 else "none"
port = sys.argv[2] if len(sys.argv) > 2 else "8132"

os.environ["LOCAL_REASONING_EFFORT"] = effort
sys.argv = ["jarvis", "--api", "--profile", "test", "--port", port]
runpy.run_module("jarvis", run_name="__main__")
