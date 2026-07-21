"""Test-only instance handshake (2026-07-21, external review item).

Answers exactly one question: **is this the server the harness started, or some
other JARVIS that happens to be listening?**

The A/B rig lost a full config to that ambiguity. `ab_run_config.ps1 -Port`
started a server on the requested port while the driver kept talking to the
8132 default; on another occasion the driver reached a DIFFERENT, still-running
server from an earlier run and scored 13 scenarios against it (2026-07-20's
"answers as a cloud model, trace tools=none" anomaly). A liveness probe cannot
catch either case -- something always answers. Only a value the harness
generated and the server echoes back can.

So the harness mints a random nonce per run, passes it to the server through
the environment, and the driver refuses to score anything until the server
echoes that exact nonce back.

Deliberately NOT exposed here (this is a diagnostic surface, not a debug dump):
  - test_home / any filesystem path -- the driver already knows its own, and a
    path is the kind of thing that turns a diagnostic endpoint into a
    reconnaissance one.
  - the settings object -- only a fingerprint of the few fields that decide
    what a run MEANS, so a config mismatch is detectable without publishing the
    config.

Registered by jarvis/api.py ONLY when JARVIS_TEST_MODE=1, which
jarvis/__main__.py sets exclusively under `--profile test`. In a normal run the
route does not exist and the path 404s.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from jarvis.config import Settings

router = APIRouter(prefix="/internal", tags=["test-only"])

_settings: "Settings | None" = None
_git_sha: str | None = None  # resolved once, lazily


def init_test_identity(settings: "Settings") -> None:
    global _settings
    _settings = settings


def _resolve_git_sha() -> str:
    """The commit the server is actually running, resolved independently.

    Deliberately not taken from the harness: a value the harness supplies would
    only prove the harness can repeat itself. Best-effort -- an installed copy
    outside a git checkout legitimately has none.
    """
    global _git_sha
    if _git_sha is not None:
        return _git_sha
    try:
        import subprocess
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent.parent
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        _git_sha = out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:  # noqa: BLE001
        _git_sha = "unknown"
    return _git_sha


def _config_fingerprint() -> str:
    """Hash of the settings that change what a measurement MEANS.

    Lets the driver detect "right port, right process, wrong configuration"
    (e.g. a shadow-mode run scored against an off-mode server) without the
    endpoint publishing the configuration itself.
    """
    s = _settings
    critical = {
        "execution_contract_mode": getattr(s, "execution_contract_mode", None),
        "local_model": getattr(s, "local_model", None),
        "local_reasoning_effort": getattr(s, "local_reasoning_effort", None),
        "cloud_policy": getattr(s, "cloud_policy", None),
        "external_writes_enabled": getattr(s, "external_writes_enabled", None),
        "confirmation_gate_enabled": getattr(s, "confirmation_gate_enabled", None),
    }
    canonical = json.dumps(critical, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@router.get("/test-identity")
async def test_identity():
    """Echo the harness's own nonce back, plus what this instance is running.

    Auth-free on purpose: under --profile test JARVIS_API_KEY is empty and auth
    is disabled anyway, the server binds to loopback only, and the response
    carries no secret -- run_id is a value the caller already knows (it is
    checking it), not one it learns.
    """
    return {
        "run_id": os.environ.get("JARVIS_TEST_RUN_ID", ""),
        "mode": getattr(_settings, "execution_contract_mode", "unknown"),
        "config_fingerprint": _config_fingerprint(),
        "git_sha": _resolve_git_sha(),
    }
