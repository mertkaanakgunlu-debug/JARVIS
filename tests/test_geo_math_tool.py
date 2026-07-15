"""jarvis/tools/geo_math_tool.py -- Devito-fails-over-to-NumPy wave simulation.

Covers BUG-geomath: the Devito-failure fallback hardcoded duration=0.5
instead of passing through the caller's actual requested duration, so a
Devito runtime error (not just "not installed") silently truncated whatever
simulation length the user asked for.
"""
from __future__ import annotations

from jarvis.tools import geo_math_tool as gm


def test_devito_failure_fallback_preserves_requested_duration(monkeypatch):
    captured = {}

    def fake_numpy_fallback(velocity_grid, source_pos, nz, nx, dz, dx, dt, duration):
        captured["duration"] = duration
        return "ok"

    monkeypatch.setattr(gm, "_wave_simulate_2d_numpy", fake_numpy_fallback)

    # devito isn't installed in this test env, so `from devito import ...`
    # inside _wave_simulate_2d_devito raises ImportError immediately --
    # exercises the exact except-branch BUG-geomath lived in, with no mocking
    # of devito itself needed.
    gm._wave_simulate_2d_devito(
        velocity_grid=None, source_pos=[5, 5], receivers=None,
        duration=2.5, nz=100, nx=100, dz=10.0, dx=10.0, dt=0.001,
    )

    assert captured["duration"] == 2.5


def test_devito_failure_fallback_with_a_different_duration(monkeypatch):
    """Guards against a fix that just swaps one hardcoded constant for
    another -- try a second, distinct value."""
    captured = {}
    monkeypatch.setattr(
        gm, "_wave_simulate_2d_numpy",
        lambda velocity_grid, source_pos, nz, nx, dz, dx, dt, duration: captured.setdefault("duration", duration),
    )

    gm._wave_simulate_2d_devito(
        velocity_grid=None, source_pos=[3, 3], receivers=None,
        duration=0.05, nz=50, nx=50, dz=10.0, dx=10.0, dt=0.001,
    )

    assert captured["duration"] == 0.05


def test_wave_simulate_2d_dispatches_to_numpy_when_devito_unavailable(monkeypatch, tmp_path):
    """The public entry point (_wave_simulate_2d) must reach the same fallback
    with the same duration when devito is unavailable from the start, not
    just when it fails mid-run."""
    monkeypatch.setattr(gm, "_has", lambda lib: False)
    monkeypatch.setattr(gm, "_OUTPUT_DIR", tmp_path)

    captured = {}
    monkeypatch.setattr(
        gm, "_wave_simulate_2d_numpy",
        lambda velocity_grid, source_pos, nz, nx, dz, dx, dt, duration: captured.setdefault("duration", duration),
    )

    gm._wave_simulate_2d(velocity_grid=None, source_pos=[5, 5], receivers=None, duration=1.75, nz=100, nx=100)
    assert captured["duration"] == 1.75
