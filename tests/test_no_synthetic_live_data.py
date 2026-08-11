"""LIVE DATA INTEGRITY INVARIANT — no readout may invent what it doesn't know.

    While the HUD is connected, no field may show a synthetic, placeholder,
    state-derived or random value. Absent data renders as "—"/"veri yok";
    an absent capability renders as "YAKINDA".

This is a source-level guard, not a behavioural one, and that is the point: the
violations it pins were each invisible at runtime by construction. They looked
exactly like real readings. The owner found the last of them by noticing the
terminal still said "Gemini" while qwen3:8b was answering.

What was actually wrong on 2026-07-31, all of it live and all of it under a
"● LIVE" badge:

  backend   jarvis/ws.py fabricated cpu/ram/gpu/vram with random.uniform() when
            psutil was missing, left gpu/vram at 0.0 when NVML failed (an
            idle-looking zero is a claim), and hardcoded latency=0 — a
            round-trip no request has ever taken.
  frontend  App.jsx swapped in demo data whenever a real stream happened to be
            empty (`connected && X.length ? X : fake`): an animated mic meter
            with no voice session, an activity feed of tool calls that never
            ran, a vault count of 2847, scripted transcript dialogue.
  labels    HudPanels.jsx derived the MODEL NAME from an animation state
            (`state === 'thinking' ? 'Gemini 2.5 Pro' : 'Gemini 2.5 Flash'`),
            captioned 'REASONING · CLOUD' on a local-only install, credited
            speech to EDGE-TTS when the engine is Piper, and printed a literal
            Tailscale IP.
  cli       the banner announced Vertex models while CLOUD_POLICY=off made any
            cloud call structurally impossible, and /status named the retired
            embedding model text-embedding-004.

Each was a separate edit in a separate file. A behavioural test would have to
stand up a server with psutil removed and an NVML failure injected to catch even
the first one. Scanning the source costs nothing and catches the whole class,
including the next one someone adds.

If a check here fails, do not add an exemption: bind the readout to a real value
(jarvis/providers/labels.py and electron/src/renderer/src/lib/display.js exist to
make that cheap), or render NO_VALUE.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RENDERER = REPO / "electron" / "src" / "renderer" / "src"

# Demo data is legal in exactly one place, reachable only when disconnected,
# where the badge reads "○ OFFLINE · DEMO DATA".
FAKE_DATA_HOOK = RENDERER / "hooks" / "useFakeData.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments_js(src: str) -> str:
    """Drop // and /* */ comments.

    Every fix in this area documents the string it replaced, so the banned
    literals legitimately appear in comments. Without this the test would fail
    on its own remediation notes.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", src, flags=re.MULTILINE)


def _strip_comments_py(src: str) -> str:
    """Drop # comments and triple-quoted strings (docstrings)."""
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    return re.sub(r"^\s*#.*$", "", src, flags=re.MULTILINE)


# ── Backend: no fabricated telemetry ─────────────────────────────────────────

def test_ws_does_not_fabricate_system_metrics():
    """psutil/NVML absent must mean None, never a generated number."""
    src = _strip_comments_py(_read(REPO / "jarvis" / "ws.py"))

    assert "random.uniform" not in src, (
        "jarvis/ws.py generates a system metric. An unmeasurable metric is None; "
        "the HUD renders None as 'ölçülemiyor'."
    )
    assert not re.search(r"\bimport random\b", src), (
        "jarvis/ws.py imports random — the only prior use was fabricating telemetry."
    )


def test_metrics_frame_allows_missing_values():
    """The contract itself must permit absence, or callers have to invent one."""
    import inspect

    from jarvis.ws import JarvisEventBus

    sig = inspect.signature(JarvisEventBus.metrics)
    for field in ("cpu", "gpu", "ram", "vram", "latency"):
        annotation = str(sig.parameters[field].annotation)
        assert "None" in annotation, (
            f"EventBus.metrics({field}=...) cannot express 'not measurable', so a "
            f"caller with no reading must supply a fake one."
        )


# ── Labels: bound to real values, never hardcoded ─────────────────────────────

# Concrete model/provider names. A readout naming one of these is asserting a
# routing fact it did not read from the turn trace.
_HARDCODED_MODEL_CLAIMS = [
    r"Gemini\s*2\.5",
    r"GEMINI\s*2\.5",
    r"EDGE-TTS",
    r"CLOUD ROUTE",
    r"REASONING\s*·\s*CLOUD",
]

_RENDERER_FILES = [
    RENDERER / "App.jsx",
    RENDERER / "components" / "HudPanels.jsx",
    RENDERER / "Widget.jsx",
]


@pytest.mark.parametrize("path", _RENDERER_FILES, ids=lambda p: p.name)
def test_renderer_has_no_hardcoded_provider_labels(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    src = _strip_comments_js(_read(path))
    for pattern in _HARDCODED_MODEL_CLAIMS:
        assert not re.search(pattern, src), (
            f"{path.name} hardcodes {pattern!r}. Model/provider text comes from the "
            f"model_status frame via lib/display.js describeModel()."
        )


def test_cli_does_not_hardcode_the_embedding_model_id():
    """/status named text-embedding-004 for two weeks after it was retired."""
    src = _strip_comments_py(_read(REPO / "jarvis" / "cli.py"))

    assert "text-embedding-004" not in src, (
        "jarvis/cli.py names an embedding model literally. Read "
        "jarvis.memory.GEMINI_EMBED_MODEL so the label cannot outlive the model."
    )


def test_provider_labels_have_one_home():
    """Two label dicts drifted apart once already (the runtime vocabulary says
    'ollama', the model menu says 'local'); keep exactly one."""
    from jarvis.providers.labels import NO_VALUE, describe_model, label_for_provider

    assert label_for_provider("ollama") == "Ollama"
    assert label_for_provider("local") == "Ollama"      # both vocabularies
    assert label_for_provider("nvidia") == "NVIDIA NIM"
    assert label_for_provider("") == NO_VALUE
    assert label_for_provider(None) == NO_VALUE
    assert describe_model("ollama", "qwen3:8b") == "qwen3:8b · Ollama"
    assert describe_model("nvidia", "z-ai/glm-5.2") == "z-ai/glm-5.2 · NVIDIA NIM"
    assert describe_model(None, None) == NO_VALUE       # no turn yet -> no claim

    cli_src = _strip_comments_py(_read(REPO / "jarvis" / "cli.py"))
    assert "_PROVIDER_LABELS" not in cli_src, (
        "jarvis/cli.py has its own provider-label dict again; use "
        "jarvis.providers.labels.label_for_provider."
    )


def test_available_models_tracks_configured_local_model():
    """The menu offered 'Qwen2.5 7B Instruct' while LOCAL_MODEL was qwen3:8b."""
    from jarvis.agent import AVAILABLE_MODELS
    from jarvis.config import Settings

    local_rows = [row for row in AVAILABLE_MODELS if row[2] == "local"]
    assert local_rows, "no local tier in the model menu"
    assert Settings().local_model in local_rows[0][1], (
        f"model menu says {local_rows[0][1]!r} but LOCAL_MODEL is "
        f"{Settings().local_model!r} — the menu names a model the router never loads."
    )


# ── Frontend: demo data is unreachable while connected ───────────────────────

def test_connected_branches_never_fall_back_to_demo_data():
    """`connected && X.length ? X : fake` is the shape to keep out.

    It reads as a connection check but is really a truthiness check: a real
    stream that is merely EMPTY silently renders invented content instead.
    """
    src = _strip_comments_js(_read(RENDERER / "App.jsx"))

    offenders = re.findall(r"connected\s*&&\s*[\w.]+\.length\s*\?", src)
    assert not offenders, (
        f"App.jsx has {len(offenders)} length-guarded fallback(s) that swap demo "
        f"data in while connected: {offenders}. Use `connected ? real : demo` and "
        f"let an empty stream render empty."
    )


def test_fake_data_is_only_reachable_when_disconnected():
    """Every useFake* result must sit on the false branch of `connected`."""
    src = _strip_comments_js(_read(RENDERER / "App.jsx"))

    for name in ("fakeMic", "fakeFeed", "fakeMetrics"):
        for line in src.splitlines():
            if re.search(rf"=.*\b{name}\b", line) and "?" in line:
                assert re.search(rf"connected\s*\?[^:]*:\s*{name}", line), (
                    f"App.jsx line uses {name} on the connected branch: {line.strip()!r}"
                )


def test_offline_badge_names_the_demo_data():
    """The badge is the reader's only signal that a panel is invented.

    Comments are stripped first, and that is not incidental: this check first
    shipped reading the raw file and passed a mutation that removed the badge
    text entirely, because a nearby explanatory comment quoted the same string.
    The test was accepting its own documentation as evidence — the exact
    false-clean shape it exists to prevent.
    """
    src = _strip_comments_js(_read(RENDERER / "App.jsx"))

    assert "OFFLINE · DEMO DATA" in src, (
        "the disconnected badge must say the data is demo, not just 'OFFLINE'."
    )


def test_socket_hook_does_not_seed_invented_metrics():
    """A freshly-connected HUD showed a full set of readings before the server
    had sent a single metrics frame."""
    src = _strip_comments_js(_read(RENDERER / "hooks" / "useJarvisSocket.js"))

    seed = re.search(r"useState\(\{\s*cpu:\s*([^,]+),", src)
    assert seed, "metrics initial state not found in useJarvisSocket.js"
    assert seed.group(1).strip() == "null", (
        f"metrics is seeded with cpu={seed.group(1).strip()} — an initial value "
        f"the server never sent. Seed null and render '—'."
    )


def test_display_helper_treats_zero_as_a_real_reading():
    """Guards the helper the panels depend on: 0% CPU is a measurement, null is
    not, and conflating them is what made 'no NVML' look like an idle GPU."""
    src = _read(RENDERER / "lib" / "display.js")

    assert "export function isMissing" in src
    assert re.search(r"v === null \|\| v === undefined", src), (
        "isMissing must test null/undefined explicitly — a falsy check would "
        "classify a real 0 reading as missing."
    )


# ── The demo fixtures themselves stay quarantined ────────────────────────────

def test_demo_fixtures_live_only_in_the_fake_data_hook():
    """The invented feed lines ('Gemini 2.5 Pro escalation', 'ChromaDB: 2,847
    vectors') are fine where they are — a hook whose name says what it is, used
    only when disconnected. They must not spread."""
    assert FAKE_DATA_HOOK.exists()
    src = _read(FAKE_DATA_HOOK)
    assert "placeholder" in src.lower() or "fake" in src.lower(), (
        "useFakeData.js must say in its own header that its contents are not real."
    )
