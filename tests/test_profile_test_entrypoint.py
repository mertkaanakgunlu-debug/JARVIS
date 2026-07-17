"""jarvis/__main__.py's `--profile test` (stabilization sprint, E section).

The isolation decision (skip load_dotenv, seed JARVIS_HOME/CLOUD_POLICY/
EXTERNAL_WRITES_ENABLED) happens as MODULE-LEVEL code in __main__.py, before
argparse even runs -- see _prescan_test_profile()'s docstring for why. That
side-effect-on-import shape can't be unit-tested by importing the module
in-process (it would permanently mutate this test process's os.environ for
every test that runs after it) -- a subprocess is the correct isolation
boundary here, mirroring the mechanism under test.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE = (
    "import sys, os, json;"
    "sys.argv = ['jarvis', '--profile', 'test', '--api'];"
    "import jarvis.__main__;"
    "from jarvis.config import Settings;"
    "s = Settings();"
    "print(json.dumps({"
    "'skip_dotenv': os.environ.get('JARVIS_SKIP_DOTENV'),"
    "'home': os.environ.get('JARVIS_HOME'),"
    "'cloud_policy_env': os.environ.get('CLOUD_POLICY'),"
    "'external_writes_env': os.environ.get('EXTERNAL_WRITES_ENABLED'),"
    "'settings_cloud_policy': s.cloud_policy,"
    "'settings_external_writes_enabled': s.external_writes_enabled,"
    "'settings_gemini_api_key': s.gemini_api_key,"
    "}))"
)


def _run_probe(extra_env: dict | None = None) -> dict:
    import json
    import os as _os
    env = dict(_os.environ)
    env.pop("JARVIS_HOME", None)  # never inherit the test runner's own value
    env.pop("JARVIS_TEST_HOME", None)
    env.pop("CLOUD_POLICY", None)
    env.pop("JARVIS_SKIP_DOTENV", None)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"probe subprocess failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_profile_test_skips_dotenv_and_seeds_isolation_env():
    data = _run_probe()
    assert data["skip_dotenv"] == "1"
    assert data["cloud_policy_env"] == "off"
    assert data["external_writes_env"] == "false"
    assert data["home"], "JARVIS_HOME must be auto-generated when unset"


def test_profile_test_settings_reflect_isolation_not_real_env():
    """The real .env's secrets must never reach Settings under this profile --
    probed by asserting the key comes back empty (the field's own default),
    which it would NOT if the real .env (which this repo's .env has a real
    key configured in, per HANDOFF.md) had leaked through."""
    data = _run_probe()
    assert data["settings_cloud_policy"] == "off"
    assert data["settings_external_writes_enabled"] is False
    assert data["settings_gemini_api_key"] == ""


def test_profile_test_ignores_pre_set_jarvis_home():
    """P0 (patch 1.1) -- the old setdefault INHERITED a pre-set JARVIS_HOME,
    so an operator whose real installation sets it globally would have aimed
    an "isolated" test run straight at the production stores. --profile test
    must always seed a fresh temp home, no matter what's already in the env."""
    data = _run_probe(extra_env={"JARVIS_HOME": "C:\\Temp\\my-production-home"})
    assert data["home"] != "C:\\Temp\\my-production-home"
    assert "jarvis-e2e-" in (data["home"] or ""), "expected a fresh mkdtemp home"


def test_profile_test_honors_explicit_test_home_only():
    """The deliberate escape hatch for a stable/repeatable test dir (CI) is
    the test-only JARVIS_TEST_HOME variable -- explicit opt-in that cannot be
    confused with the production JARVIS_HOME, which stays ignored even when
    both are set."""
    data = _run_probe(extra_env={
        "JARVIS_HOME": "C:\\Temp\\my-production-home",
        "JARVIS_TEST_HOME": "C:\\Temp\\my-fixed-e2e-home",
    })
    assert data["home"] == "C:\\Temp\\my-fixed-e2e-home"


def test_default_profile_unaffected():
    """Sanity: the default profile (no --profile flag) must NOT set any of
    these isolation env vars -- normal runs are unchanged by this sprint."""
    import json
    import os as _os
    env = dict(_os.environ)
    for k in ("JARVIS_HOME", "CLOUD_POLICY", "JARVIS_SKIP_DOTENV", "EXTERNAL_WRITES_ENABLED"):
        env.pop(k, None)
    probe = _PROBE.replace("'--profile', 'test', '--api'", "'--api'")
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"probe subprocess failed:\n{result.stdout}\n{result.stderr}"
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["skip_dotenv"] is None
    assert data["home"] is None
    assert data["cloud_policy_env"] is None
