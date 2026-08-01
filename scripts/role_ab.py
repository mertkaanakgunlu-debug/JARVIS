"""Faz 2.5 measurement: the SAME query under the fast role and the reasoning role.

Why this harness exists rather than reusing the recorded baseline: the numbers on
record (fast ~0.9 s / 19 tokens, reasoning ~12-33 s / 524-1360 tokens) compare a
CHAT reply against TOOL work. Both were produced by whatever role _route_query
picked -- conversation went fast, everything else went reasoning -- so they
measure request difficulty, not the role. They cannot answer the only question
Faz 2.5 actually needs answered:

    with qwen3:8b's thinking channel OFF, does the model still pick the right
    tool with the right arguments?

That is the risk. The latency win is not in doubt; the accuracy cost is, and it
has exactly one existing datapoint (test_local_thinking.py's docstring: "a
representative file_write tool call stayed byte-identical" -- n=1, one tool).

Everything real is real: real qwen3:8b over Ollama, the real graph, the real
system prompt, the real tool registry. Google Calendar is the one fake -- a
capture object in place of _get_service -- so no live account is touched. Home
and cwd are a scratch directory, so the owner's data/ is not written either.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isolate BEFORE importing jarvis: jarvis/paths.py reads JARVIS_HOME at import
# time, so setting it after the first `import jarvis.*` would be too late.
#
# Deliberately OUTSIDE the repo. A home under scripts/ would put a SQLite
# database, an audit log, checkpoints and any PNG the model draws inside the
# working tree -- and the harness writes real files, that is the point of it
# being live. MEMORY.md's isolate-test-data-paths incident is the same mistake
# one directory over: a throwaway script that resolved a relative path into the
# real project root overwrote the owner's actual conversation history.
SCRATCH = Path(
    os.environ.get("ROLE_AB_HOME")
    or Path(tempfile.gettempdir()) / "jarvis-role-ab"
)
(SCRATCH / "Desktop").mkdir(parents=True, exist_ok=True)
(SCRATCH / "Downloads").mkdir(parents=True, exist_ok=True)
os.environ["JARVIS_HOME"] = str(SCRATCH)
os.environ["JARVIS_SKIP_DOTENV"] = "1"   # no real API keys, no real .env
os.environ["CLOUD_POLICY"] = "off"       # the owner's config: local-only
os.chdir(SCRATCH)

from jarvis.config import Settings          # noqa: E402
import jarvis.agent as agent_mod            # noqa: E402
import jarvis.tools.calendar as calendar_mod  # noqa: E402
from jarvis.graph.role_router import FAST, REASONING, RoleDecision  # noqa: E402
from jarvis.graph.tool_router import classify_query  # noqa: E402


# ── the one fake: Google Calendar ─────────────────────────────────────────────

class _FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeEvents:
    """Records every call; returns a small fixed calendar."""

    def __init__(self, sink: list):
        self._sink = sink

    def list(self, **kw):
        self._sink.append(("list", kw))
        return _FakeExec({"items": [
            {"summary": "Diş hekimi", "start": {"dateTime": "2026-08-01T11:00:00+03:00"},
             "end": {"dateTime": "2026-08-01T11:30:00+03:00"}, "id": "ev1"},
            {"summary": "Proje toplantısı", "start": {"dateTime": "2026-08-01T16:00:00+03:00"},
             "end": {"dateTime": "2026-08-01T17:00:00+03:00"}, "id": "ev2"},
        ]})

    def insert(self, **kw):
        self._sink.append(("insert", kw))
        body = kw.get("body", {})
        return _FakeExec({"id": "created-1", "summary": body.get("summary", ""),
                          "htmlLink": "https://example.invalid/ev"})

    def delete(self, **kw):
        self._sink.append(("delete", kw))
        return _FakeExec({})

    def update(self, **kw):
        self._sink.append(("update", kw))
        return _FakeExec({"id": "updated-1"})

    def get(self, **kw):
        self._sink.append(("get", kw))
        return _FakeExec({"id": kw.get("eventId", "ev1"), "summary": "Proje toplantısı",
                          "start": {"dateTime": "2026-08-01T16:00:00+03:00"},
                          "end": {"dateTime": "2026-08-01T17:00:00+03:00"}})


class _FakeService:
    def __init__(self, sink: list):
        self._events = _FakeEvents(sink)

    def events(self):
        return self._events


GOOGLE_CALLS: list = []


def _fake_get_service(settings=None):
    return _FakeService(GOOGLE_CALLS)


calendar_mod._get_service = _fake_get_service


# ── tool-call capture ─────────────────────────────────────────────────────────

TOOL_CALLS: list = []
_OrigCB = agent_mod._HudEventCallback


class _CapturingCB(_OrigCB):
    def on_tool_start(self, serialized, input_str, **kwargs):
        TOOL_CALLS.append({
            "tool": serialized.get("name", "?"),
            "args": input_str if isinstance(input_str, dict) else str(input_str)[:300],
        })
        return super().on_tool_start(serialized, input_str, **kwargs)


agent_mod._HudEventCallback = _CapturingCB


# ── scenarios ─────────────────────────────────────────────────────────────────
#
# expect_any / expect_all name the tools the request cannot be answered without.
# They are the accuracy axis, and without them latency alone is a trap: routing
# every turn to fast would "win" this measurement outright.
#
# expect_any = [] means zero tools is the correct outcome (conversation).

SCENARIOS = [
    {
        "key": "chat",
        "query": "Merhaba, bugün nasılsın?",
        "expect_any": [],             # conversation: calling any tool is wrong
        "expect_all": [],
        "plan_role": "fast",
    },
    {
        "key": "calendar_list",
        "query": "Bugünkü takvimimi göster",
        "expect_any": ["google_calendar"],
        "expect_all": [],
        "plan_role": "fast",
    },
    {
        "key": "file_list",
        "query": "Masaüstündeki dosyaları listele",
        "expect_any": ["file_list"],
        "expect_all": [],
        "plan_role": "fast",
    },
    {
        "key": "calendar_create",
        "query": "Yarın saat 15:00'te Baran'la toplantı ekle",
        "expect_any": ["google_calendar"],
        "expect_all": [],
        "plan_role": "fast",
    },
    {
        # Two DEPENDENT calls -- the measured limit is "two hold, three don't",
        # so this is deliberately at the edge where thinking should matter most.
        "key": "multi_step",
        "query": ("Masaüstündeki satis.csv dosyasını oku ve aylardaki satışların "
                  "çizgi grafiğini çiz"),
        "expect_any": ["csv_read", "file_read", "data_analyze"],
        "expect_all": ["plot_data"],
        "plan_role": "reasoning",
    },
]


_FIXTURE_CSV = "ay,satis\n2026-01,120\n2026-02,145\n2026-03,132\n2026-04,178\n2026-05,190\n2026-06,165\n"


def _seed_fixtures() -> None:
    """Write multi_step's CSV into BOTH Desktop and Downloads.

    Not redundancy — it removes a variable. The first run of this harness put
    the file only on the Desktop and named it in the query ("Masaüstündeki
    satis.csv"); the model read Downloads in 10/10 runs, the read failed, and
    the scenario measured folder resolution instead of chain completion. With
    the file in both places no folder guess can fail, so what is left to
    measure is whether the second dependent call happens at all.
    """
    for folder in ("Desktop", "Downloads"):
        (SCRATCH / folder / "satis.csv").write_text(_FIXTURE_CSV, encoding="utf-8")


def _score(scenario: dict, names: list[str]) -> bool:
    any_ok = scenario["expect_any"]
    if not any_ok:
        return not names
    if not any(t in names for t in any_ok):
        return False
    return all(t in names for t in scenario["expect_all"])


def _force_role(use_pro: bool):
    """Pin the role for a run, leaving the tool route untouched.

    Only the role is overridden -- the tool subset the model sees comes from
    the real classify_query in both arms, or the comparison measures two
    things at once.
    """
    role = RoleDecision(REASONING if use_pro else FAST, "forced_by_harness")

    def _routed(query: str, needs_planning: bool):
        return classify_query(query), role
    agent_mod._route_query = _routed


async def run_one(agent, scenario: dict) -> dict:
    TOOL_CALLS.clear()
    GOOGLE_CALLS.clear()
    await agent.reset_async()
    t0 = time.monotonic()
    err = ""
    try:
        text, _label = await agent.chat(scenario["query"], detected_language="tr",
                                        transport="cli-text")
    except Exception as e:  # noqa: BLE001 -- an unattended run records, never aborts
        text, err = "", f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - t0
    trace = agent.last_turn_trace or {}
    names = [c["tool"] for c in TOOL_CALLS]
    return {
        "elapsed": round(elapsed, 2),
        "role": trace.get("requested_role", "?"),
        "model": trace.get("model", "?"),
        "llm_calls": trace.get("calls", 0),
        "out_tokens": trace.get("output_tokens", 0),
        "in_tokens": trace.get("input_tokens", 0),
        "tools": names,
        "tool_args": [c["args"] for c in TOOL_CALLS],
        "google": [g[0] for g in GOOGLE_CALLS],
        "google_args": [str(g[1])[:300] for g in GOOGLE_CALLS],
        "correct": _score(scenario, names),
        "error": err,
        "answer": (text or "")[:400],
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--out", default="role_ab_results.json")
    ap.add_argument("--only", default="", help="comma-separated scenario keys")
    ap.add_argument("--arm", default="", help="fast | reasoning (default: both)")
    args = ap.parse_args()

    keys = {k.strip() for k in args.only.split(",") if k.strip()}
    scenarios = [s for s in SCENARIOS if not keys or s["key"] in keys]
    arms = [a for a in (("fast", False), ("reasoning", True))
            if not args.arm or a[0] == args.arm]

    _seed_fixtures()
    settings = Settings()
    print(f"local_model={settings.local_model} cloud_policy={settings.cloud_policy} "
          f"local_reasoning_effort={settings.local_reasoning_effort!r}", flush=True)

    # Warm-up: Ollama loads qwen3:8b into VRAM on the first call, and that
    # one-off cost lands entirely on run #0 of whichever arm goes first. The
    # smoke run measured it at ~7 s -- large enough to invert a p50 comparison.
    _force_role(False)
    warm = agent_mod.JarvisAgent(settings)
    t0 = time.monotonic()
    await warm.chat("Merhaba", detected_language="tr", transport="cli-text")
    print(f"warm-up (model load, discarded): {time.monotonic() - t0:.2f}s", flush=True)

    results: list[dict] = []
    for arm, use_pro in arms:
        _force_role(use_pro)
        for scenario in scenarios:
            for i in range(args.runs):
                agent = agent_mod.JarvisAgent(settings)
                row = await run_one(agent, scenario)
                row["arm"] = arm
                row["scenario"] = scenario["key"]
                row["run"] = i
                results.append(row)
                print(f"  {arm:9s} {scenario['key']:16s} #{i:02d} "
                      f"{row['elapsed']:7.2f}s role={row['role']:9s} "
                      f"out={row['out_tokens']:5d} tools={row['tools']} "
                      f"ok={row['correct']}{' ERR=' + row['error'] if row['error'] else ''}",
                      flush=True)
                Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                          encoding="utf-8")

    # ── summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"{'scenario':17s} {'arm':10s} {'n':>3s} {'p50':>7s} {'p95':>7s} "
          f"{'out_tok':>8s} {'correct':>8s}")
    print("-" * 78)
    for scenario in scenarios:
        for arm, _ in arms:
            rows = [r for r in results if r["scenario"] == scenario["key"] and r["arm"] == arm]
            if not rows:
                continue
            lat = sorted(r["elapsed"] for r in rows)
            p50 = statistics.median(lat)
            p95 = lat[max(0, int(len(lat) * 0.95) - 1)] if len(lat) > 1 else lat[0]
            tok = statistics.median([r["out_tokens"] for r in rows])
            ok = sum(1 for r in rows if r["correct"])
            print(f"{scenario['key']:17s} {arm:10s} {len(rows):3d} {p50:7.2f} {p95:7.2f} "
                  f"{tok:8.0f} {ok:5d}/{len(rows)}")
    print("=" * 78)
    print(f"raw -> {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
