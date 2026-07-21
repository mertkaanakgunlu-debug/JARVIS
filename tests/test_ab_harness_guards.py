"""Harness integrity guards for the A/B measurement rig (2026-07-21).

These do not test JARVIS. They test the thing that MEASURES JARVIS -- because
that rig silently produced a fully-invalid A/B config and nobody noticed for a
day. `ab_run_config.ps1 -Port` started the server on the requested port while
`manual_test_driver.py` kept resolving its target from JARVIS_TEST_BASE_URL,
whose default is a hardcoded 8132. Every scenario then got ConnectionRefused,
yet the run exited 0 and wrote a full-size results file where every row failed
with "trace tools=none" -- indistinguishable, at a glance, from "the model
failed everything".

The invariant these pin: a harness that cannot measure must exit non-zero and
say why. It must never emit a plausible-looking zero.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DRIVER = REPO / "scripts" / "manual_test_driver.py"
AB_SCRIPT = REPO / "scripts" / "ab_run_config.ps1"

EXIT_PREFLIGHT_FAILED = 3
EXIT_NO_SUCCESSFUL_CHAT = 4


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_driver(base_url: str, tmp_path: Path, scenario: str = "A1") -> subprocess.CompletedProcess:
    env = {
        **_clean_env(),
        "JARVIS_TEST_BASE_URL": base_url,
        "JARVIS_TEST_HOME": str(tmp_path / "home"),
        "JARVIS_TEST_RESULTS": str(tmp_path / "results.jsonl"),
    }
    return subprocess.run(
        [sys.executable, str(DRIVER), scenario],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(REPO / "scripts"),
    )


def _clean_env() -> dict:
    import os
    # Drop any ambient A/B vars so a developer's shell can't mask a failure.
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("JARVIS_TEST_", "EXECUTION_CONTRACT_"))}


class _StubHandler(BaseHTTPRequestHandler):
    """Answers /status so preflight passes; records every path it is asked for."""
    paths: list = []

    def do_GET(self):  # noqa: N802
        type(self).paths.append(("GET", self.path))
        body = json.dumps({"model": "stub", "session_id": "stub"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


# ── the actual -Port regression ──────────────────────────────────────────────

def test_preflight_exits_nonzero_when_no_server_is_listening(tmp_path):
    """THE regression test for the -Port incident.

    Before the guard this exact situation ran all 13 scenarios against nothing,
    took ~75s, exited 0, and wrote a results file that scored 0/13 -- which
    reads as a catastrophic model regression rather than a plumbing mistake.
    """
    dead = f"http://127.0.0.1:{_free_port()}"  # bound then released == closed
    proc = _run_driver(dead, tmp_path)

    assert proc.returncode == EXIT_PREFLIGHT_FAILED, (
        f"expected exit {EXIT_PREFLIGHT_FAILED}, got {proc.returncode}\n"
        f"stdout:\n{proc.stdout[-2000:]}"
    )
    assert "PREFLIGHT FAILED" in proc.stdout
    assert dead in proc.stdout, "the failure must name the URL it actually tried"


def test_preflight_runs_before_any_scenario_is_scored(tmp_path):
    """Fail fast means fail FIRST -- no scenario output, no results file."""
    dead = f"http://127.0.0.1:{_free_port()}"
    proc = _run_driver(dead, tmp_path)

    assert "[A1]" not in proc.stdout, "no scenario should run after a failed preflight"
    assert "ORACLE" not in proc.stdout, "nothing may be scored against a dead server"
    assert not (tmp_path / "results.jsonl").exists(), (
        "a results file at all is the hazard -- it is what made the loss look like data"
    )


# ── non-default port: the driver must follow JARVIS_TEST_BASE_URL ────────────

def test_driver_targets_a_non_default_port(tmp_path):
    """The driver must talk to the port it was told to, not the 8132 default.

    Proven positively: a stub server on an arbitrary port must actually RECEIVE
    the preflight request. If the driver ignored JARVIS_TEST_BASE_URL (the old
    behavior), this stub would never be contacted.
    """
    port = _free_port()
    assert port != 8132
    _StubHandler.paths = []
    srv = HTTPServer(("127.0.0.1", port), _StubHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        proc = _run_driver(f"http://127.0.0.1:{port}", tmp_path)
    finally:
        srv.shutdown()
        srv.server_close()

    assert any(p == "/status" for _, p in _StubHandler.paths), (
        f"stub on :{port} was never asked for /status — driver ignored "
        f"JARVIS_TEST_BASE_URL. paths seen: {_StubHandler.paths}"
    )
    assert "preflight ok" in proc.stdout
    # The stub answers /status but not POST /chat, so no turn can succeed --
    # which must itself be reported as a non-result, not scored as failure.
    assert proc.returncode == EXIT_NO_SUCCESSFUL_CHAT
    assert "NO SUCCESSFUL CHAT" in proc.stdout


# ── the orchestrator half of the same bug ────────────────────────────────────

def test_ab_run_config_exports_base_url_from_port():
    """ab_run_config.ps1 must hand -Port to the DRIVER too, not just the server.

    A text assertion on purpose: the .ps1 cannot be imported here, and the whole
    failure mode was this one line being absent.
    """
    src = AB_SCRIPT.read_text(encoding="utf-8", errors="replace")
    assert "JARVIS_TEST_BASE_URL" in src, (
        "ab_run_config.ps1 no longer sets JARVIS_TEST_BASE_URL -- every run on a "
        "non-default port silently scores 0 again"
    )
    assert '$env:JARVIS_TEST_BASE_URL = "http://127.0.0.1:$Port"' in src, (
        "JARVIS_TEST_BASE_URL must be derived from $Port, or the two can drift apart"
    )


@pytest.mark.parametrize("marker", ["PREFLIGHT FAILED", "NO SUCCESSFUL CHAT"])
def test_driver_declares_its_failure_exit_codes(marker):
    """Both loud-failure paths must stay reachable in the driver source."""
    src = DRIVER.read_text(encoding="utf-8", errors="replace")
    assert marker in src
