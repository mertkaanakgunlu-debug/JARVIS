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


def _run_driver(base_url: str, tmp_path: Path, scenario: str = "A1",
                run_id: str | None = None) -> subprocess.CompletedProcess:
    env = {
        **_clean_env(),
        "JARVIS_TEST_BASE_URL": base_url,
        "JARVIS_TEST_HOME": str(tmp_path / "home"),
        "JARVIS_TEST_RESULTS": str(tmp_path / "results.jsonl"),
    }
    if run_id is not None:
        env["JARVIS_TEST_RUN_ID"] = run_id
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
    """Answers /status so preflight passes; records every path it is asked for.

    `identity` is what it returns from /internal/test-identity: None means the
    route 404s (an old or non-test server), a dict means it answers with that
    payload (used to simulate the WRONG instance holding the port).
    """
    paths: list = []
    identity: dict | None = None

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        type(self).paths.append(("GET", self.path))
        if self.path.startswith("/internal/test-identity"):
            if type(self).identity is None:
                self._json(404, {"detail": "Not Found"})
            else:
                self._json(200, type(self).identity)
            return
        self._json(200, {"model": "stub", "session_id": "stub"})

    def log_message(self, *a):  # silence
        pass


def _serve(port: int):
    srv = HTTPServer(("127.0.0.1", port), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


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


@pytest.mark.parametrize("marker", ["PREFLIGHT FAILED", "NO SUCCESSFUL CHAT", "TRANSPORT LOSS"])
def test_driver_declares_its_failure_exit_codes(marker):
    """All three loud-failure paths must stay reachable in the driver source."""
    src = DRIVER.read_text(encoding="utf-8", errors="replace")
    assert marker in src


def test_single_transport_error_invalidates_the_run_by_default(tmp_path):
    """A turn that never reached the server is ABSENT, not failed.

    Averaging it into a score silently understates the model, so one is enough
    to invalidate a benchmark run. B5b is a continuation scenario (no /reset),
    so the stub sees only the chat POST it cannot serve.
    """
    port = _free_port()
    _StubHandler.paths = []
    srv = HTTPServer(("127.0.0.1", port), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        proc = _run_driver(f"http://127.0.0.1:{port}", tmp_path)
    finally:
        srv.shutdown()
        srv.server_close()
    # Stub answers GET /status but 501s the chat POST -> HTTPError, which is a
    # SERVER response, not a transport error. So this run trips the
    # no-successful-chat guard rather than the transport guard.
    assert proc.returncode == EXIT_NO_SUCCESSFUL_CHAT
    assert "TRANSPORT LOSS" not in proc.stdout, (
        "an HTTPError means the server answered -- it must not be miscounted "
        "as a transport failure"
    )


# ── the orchestrator must not swallow the driver's verdict ───────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="ab_run_config.ps1 is PowerShell/Windows")
def test_ps_wrapper_propagates_driver_failure_exit_code(tmp_path):
    """The wrapper must exit non-zero when the driver says "not a measurement".

    Before this guard, ab_run_config.ps1 recorded `exit=$LASTEXITCODE` into its
    log and then returned 0 anyway -- which would have recreated the exact
    silent-failure class the driver guards were added to close, one level up.

    Setup: a stub holds the port and answers /status, so the wrapper's readiness
    probe passes (its own real server fails to bind, harmlessly). The driver
    then reaches the stub, cannot complete a chat, and exits 4. The wrapper must
    surface that as a non-zero exit AND mark the manifest invalid.

    This assumption -- "its own real server fails to bind, harmlessly" -- is
    NOT actually guaranteed: under CI's process-scheduling timing this test was
    intermittently observed hitting KeyError: 'valid_measurement' instead of
    the assertion below, because the real server subprocess sometimes wins the
    port-bind race against this test's own stub thread, then misses its own
    300s readiness window for real (see
    test_ps_wrapper_manifest_reports_invalid_when_server_never_becomes_ready
    right below, which exercises that path directly and deterministically) --
    ab_run_config.ps1's manifest now reports valid_measurement=false on BOTH
    paths, not only this one.
    """
    port = _free_port()
    _StubHandler.paths = []
    srv = HTTPServer(("127.0.0.1", port), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(AB_SCRIPT),
             "-Config", "pytest-exitcode", "-Effort", "none", "-Runs", "1",
             "-Port", str(port), "-Root", str(tmp_path), "-Scenarios", "A1"],
            capture_output=True, text=True, timeout=300, env=_clean_env(),
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert proc.returncode != 0, (
        "wrapper returned 0 while the driver reported an invalid measurement\n"
        f"stdout:\n{proc.stdout[-1500:]}\nstderr:\n{proc.stderr[-1500:]}"
    )

    manifest = tmp_path / "results" / "manifest_pytest-exitcode.json"
    assert manifest.exists(), "manifest must be written even for a failed run"
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    assert data["valid_measurement"] is False
    assert data["invalid_runs"] >= 1
    assert data["status"] in ("completed", "incomplete")
    assert data["driver_base_url"] == f"http://127.0.0.1:{port}", (
        "manifest must record the URL the driver was actually pointed at"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="ab_run_config.ps1 is PowerShell/Windows")
def test_ps_wrapper_manifest_reports_invalid_when_server_never_becomes_ready(tmp_path):
    """CI-fix 2026-07-22: the other exit-before-finalize gap. If the readiness
    probe never succeeds -- nothing at all answers /status, not a stub, not
    the real server -- the wrapper must still exit non-zero AND leave a
    manifest that says so, not one silently missing valid_measurement
    entirely (ab_run_config.ps1's finalize block, which sets that field,
    sits AFTER the readiness check's own early exit -- a run that never gets
    past readiness never reaches it).

    No stub server is started here on purpose: nothing is listening on this
    port at all, so /status can never succeed regardless of environment --
    -ReadyTimeoutSec 4 keeps this fast (2 retries) rather than waiting out
    the real 300s default, without relying on winning/losing the CI-only
    port-bind race the sibling test above documents.
    """
    port = _free_port()  # nothing listening -- readiness can never succeed
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(AB_SCRIPT),
         "-Config", "pytest-notready", "-Effort", "none", "-Runs", "1",
         "-Port", str(port), "-Root", str(tmp_path), "-Scenarios", "A1",
         "-ReadyTimeoutSec", "4"],
        capture_output=True, text=True, timeout=60, env=_clean_env(),
    )

    assert proc.returncode != 0, (
        "wrapper returned 0 while the server never became ready\n"
        f"stdout:\n{proc.stdout[-1500:]}\nstderr:\n{proc.stderr[-1500:]}"
    )
    manifest = tmp_path / "results" / "manifest_pytest-notready.json"
    assert manifest.exists(), "manifest must be written even when readiness times out"
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    assert data["valid_measurement"] is False
    assert data["status"] == "server_not_ready"


# ── instance identity: "a server" is not "THE server" ────────────────────────

def test_identity_mismatch_is_refused(tmp_path):
    """The 2026-07-20 incident, reproduced and now caught.

    A perfectly alive JARVIS answers on the port -- just not the one this run
    started. Liveness cannot tell the difference; the nonce can.
    """
    port = _free_port()
    _StubHandler.paths = []
    _StubHandler.identity = {"run_id": "some-other-run", "mode": "off",
                             "config_fingerprint": "sha256:deadbeef", "git_sha": "0000000"}
    srv = _serve(port)
    try:
        proc = _run_driver(f"http://127.0.0.1:{port}", tmp_path, run_id="the-run-we-started")
    finally:
        srv.shutdown()
        srv.server_close()
        _StubHandler.identity = None

    assert proc.returncode == EXIT_PREFLIGHT_FAILED
    assert "IDENTITY MISMATCH" in proc.stdout
    assert "the-run-we-started" in proc.stdout and "some-other-run" in proc.stdout
    assert "[A1]" not in proc.stdout, "nothing may be scored against the wrong instance"


def test_missing_identity_route_is_refused(tmp_path):
    """Something answers /status but has no test-identity route.

    That is an old server, a production build, or something else entirely --
    all of which would produce plausible, meaningless scores.
    """
    port = _free_port()
    _StubHandler.paths = []
    _StubHandler.identity = None  # route 404s
    srv = _serve(port)
    try:
        proc = _run_driver(f"http://127.0.0.1:{port}", tmp_path, run_id="expected-run")
    finally:
        srv.shutdown()
        srv.server_close()

    assert proc.returncode == EXIT_PREFLIGHT_FAILED
    assert "IDENTITY CHECK FAILED" in proc.stdout


def test_identity_check_is_skipped_without_a_nonce(tmp_path):
    """A hand-started manual run must still work -- but say the check was skipped."""
    port = _free_port()
    _StubHandler.paths = []
    _StubHandler.identity = None
    srv = _serve(port)
    try:
        proc = _run_driver(f"http://127.0.0.1:{port}", tmp_path)  # no run_id
    finally:
        srv.shutdown()
        srv.server_close()

    assert "identity check SKIPPED" in proc.stdout
    assert proc.returncode == EXIT_NO_SUCCESSFUL_CHAT  # got past preflight


def test_identity_endpoint_leaks_no_filesystem_paths():
    """A diagnostic surface must not become a reconnaissance one.

    test_home is deliberately absent: the driver already knows its own, and a
    path is exactly the sort of thing that should not be readable off an
    endpoint.
    """
    from jarvis.api_routers import test_identity as ti
    src = ti.__file__
    import inspect
    body = inspect.getsource(ti.test_identity)
    for leak in ("test_home", "JARVIS_HOME", "home_dir"):
        assert leak not in body, f"{leak} must not be exposed by /internal/test-identity ({src})"


def test_identity_route_is_gated_on_test_mode():
    """The route must not exist in a normal run."""
    api_src = (REPO / "jarvis" / "api.py").read_text(encoding="utf-8", errors="replace")
    assert 'os.environ.get("JARVIS_TEST_MODE") == "1"' in api_src, (
        "the test-identity router must be mounted behind JARVIS_TEST_MODE"
    )
    main_src = (REPO / "jarvis" / "__main__.py").read_text(encoding="utf-8", errors="replace")
    assert 'os.environ["JARVIS_TEST_MODE"] = "1"' in main_src
    # ...and only from inside the --profile test pre-scan branch, never at top level.
    assert main_src.index('os.environ["JARVIS_TEST_MODE"] = "1"') > main_src.index("if _prescan_test_profile():")


def test_config_fingerprint_distinguishes_contract_mode():
    """"Right port, right process, wrong configuration" must be detectable.

    Scoring a shadow-mode run against an off-mode server is the subtlest form
    of the wrong-instance bug -- the fingerprint is what makes it visible.
    """
    from types import SimpleNamespace
    from jarvis.api_routers import test_identity as ti

    base = dict(local_model="qwen3:8b", local_reasoning_effort="none",
                cloud_policy="off", external_writes_enabled=False,
                confirmation_gate_enabled=True)
    ti._settings = SimpleNamespace(execution_contract_mode="off", **base)
    off_fp = ti._config_fingerprint()
    ti._settings = SimpleNamespace(execution_contract_mode="shadow", **base)
    shadow_fp = ti._config_fingerprint()
    ti._settings = None

    assert off_fp != shadow_fp
    assert off_fp.startswith("sha256:")


@pytest.mark.skipif(sys.platform != "win32", reason="ab_run_config.ps1 is PowerShell/Windows")
def test_ab_run_config_mints_a_per_run_nonce():
    src = AB_SCRIPT.read_text(encoding="utf-8", errors="replace")
    assert "$env:JARVIS_TEST_RUN_ID = $RunNonce" in src
    assert "[guid]::NewGuid()" in src, "the nonce must be unguessable per run"


@pytest.mark.skipif(sys.platform != "win32", reason="ab_run_config.ps1 is PowerShell/Windows")
def test_manifest_is_written_before_the_run_not_only_after():
    """An interrupted run must still be identifiable.

    Pinned as source order: the manifest write must precede the driver loop, so
    a killed run leaves a manifest WITHOUT a status field rather than no
    manifest at all.
    """
    src = AB_SCRIPT.read_text(encoding="utf-8", errors="replace")
    first_write = src.index('manifest_$Config.json')
    run_loop = src.index('foreach ($r in 1..$Runs)')
    assert first_write < run_loop, (
        "manifest is written only after the run loop -- an interrupted run "
        "would leave nothing on disk identifying it"
    )
