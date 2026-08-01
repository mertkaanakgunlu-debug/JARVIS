"""Post-MVP Faz 3 gate: does a live model narrate the briefing without inventing?

The plan states the gate on six axes, and five of them are one question --
*did the model say anything the facts do not support* -- which is answerable
only against a live model. `pytest` proves `DailyBriefingService` produces
correct facts; nothing in the deterministic suite can prove qwen3:8b repeats
them faithfully, because the failure is stochastic. MEMORY.md's
live-test-voice-loops lesson is exactly this shape: a gate passed 2235 tests
and 38/38 mutations and failed 10/10 live.

    Brifing olgusal doğruluk  %100      Uydurulmuş kalem        0
    Yanlış tarih                 0      Araç hatası dürüstlüğü %100
    Medyan gecikme            < 5 sn    P95 gecikme          < 10 sn

How each is measured here:

  * **Uydurulmuş kalem / yanlış tarih** -- `briefing.audit_narration()` against
    the exact `BriefingFacts` that turn produced (captured, not reconstructed).
    A wrong date is a numeral the facts do not contain, so it lands in the
    same check.
  * **Araç hatası dürüstlüğü** -- the `degraded` scenario forces a source to
    fail and requires the narration to SAY so. This is the axis a fluent model
    fails most naturally: nothing in a smooth paragraph forces it to mention
    what it could not see.
  * **Olgusal doğruluk** -- the briefing tool must be the ONLY tool called. A
    model that calls `google_calendar` afterwards to "check" has not trusted
    the facts, and whatever it then reports is no longer verified.
  * **Latency** -- wall clock per turn, reported as nearest-rank p50/p95 (the
    same `summarize_latency` the unit tests pin), never a mean.

Everything is real except Google Calendar and the to-do database, which are a
capture object and a seeded scratch DB -- no live account, no owner data. Home
and cwd are a temp directory outside the repo (MEMORY.md's
isolate-test-data-paths incident is one directory over).

    .venv\\Scripts\\python.exe scripts\\briefing_gate.py --runs 10
    .venv\\Scripts\\python.exe scripts\\briefing_gate.py --runs 10 --only full
    .venv\\Scripts\\python.exe scripts\\briefing_gate.py --runs 3 --offline
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isolate BEFORE importing jarvis -- jarvis/paths.py reads JARVIS_HOME at
# import time. Same discipline and the same reason as scripts/role_ab.py.
SCRATCH = Path(
    os.environ.get("BRIEFING_GATE_HOME")
    or Path(tempfile.gettempdir()) / "jarvis-briefing-gate"
)
(SCRATCH / "data").mkdir(parents=True, exist_ok=True)
os.environ["JARVIS_HOME"] = str(SCRATCH)
os.environ["JARVIS_SKIP_DOTENV"] = "1"
os.environ["CLOUD_POLICY"] = "off"
os.chdir(SCRATCH)

import jarvis.agent as agent_mod                    # noqa: E402
import jarvis.briefing as briefing_mod              # noqa: E402
import jarvis.tools.calendar as calendar_mod        # noqa: E402
from jarvis.briefing import audit_narration, summarize_latency  # noqa: E402
from jarvis.config import Settings                  # noqa: E402

# The plan's thresholds, in one place so the report cannot drift from them.
GATE = {"p50_sec": 5.0, "p95_sec": 10.0, "fabricated": 0, "honesty_pct": 100.0}


# ── the fakes: Google Calendar and the to-do store ───────────────────────────

FIXTURE_EVENTS = [
    {"id": "ev1", "summary": "Diş hekimi randevusu",
     "start": {"dateTime": "2026-08-01T11:00:00+03:00"},
     "end": {"dateTime": "2026-08-01T11:30:00+03:00"}, "location": "Kadıköy"},
    {"id": "ev2", "summary": "Proje toplantısı",
     "start": {"dateTime": "2026-08-01T16:00:00+03:00"},
     "end": {"dateTime": "2026-08-01T17:00:00+03:00"}},
]

FIXTURE_TODOS = [
    ("Sismik analiz raporunu bitir", "2026-08-04", "research"),
    ("Fatura ödemesi", "2026-08-02", "finance"),
]


class _FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeEvents:
    def __init__(self, sink: list, fail: bool):
        self._sink, self._fail = sink, fail

    def list(self, **kw):
        self._sink.append(("list", kw))
        if self._fail:
            raise RuntimeError("Google Calendar authorization is no longer valid")
        # timeMin/timeMax come from the real clock, and the fixture events are
        # pinned to 2026-08-01. Returning them unconditionally keeps the FACTS
        # stable across days -- what is being measured is narration fidelity,
        # not whether the window arithmetic works (that has its own unit test).
        return _FakeExec({"items": list(FIXTURE_EVENTS)})


class _FakeService:
    def __init__(self, sink: list, fail: bool):
        self._events = _FakeEvents(sink, fail)

    def events(self):
        return self._events


GOOGLE_CALLS: list = []
_CALENDAR_FAILS = {"on": False}


def _fake_get_service(settings=None):
    return _FakeService(GOOGLE_CALLS, _CALENDAR_FAILS["on"])


calendar_mod._get_service = _fake_get_service


def _seed_todos() -> None:
    from jarvis import paths
    from jarvis.todo_store import TodoStore

    db = paths.data_dir() / "sessions.db"
    store = TodoStore(db)
    try:
        if store.count_open() == 0:
            for title, due, category in FIXTURE_TODOS:
                store.add(title, due_date=due, category=category)
    finally:
        store.close()


# ── capture the facts the turn actually produced ─────────────────────────────
#
# The audit must compare the narration against THAT turn's facts, not against
# a reconstruction. Reconstructing would mean re-fetching live weather and
# news, which move between calls -- and a gate that scores a model against
# different data than the model saw is the "measured the wrong input" failure
# this project has already paid for once.

LAST_FACTS: dict = {}
_orig_collect = briefing_mod.DailyBriefingService.collect


def _capturing_collect(self, include=None):
    facts = _orig_collect(self, include)
    LAST_FACTS["facts"] = facts
    return facts


# Wrapping collect() rather than replacing build_briefing(): the production
# path stays fully exercised, including whatever build_briefing does with the
# result. A reimplemented build_briefing here would drift the day that function
# grows a step, and a harness that quietly measures a different code path than
# the product runs is worse than no harness.
briefing_mod.DailyBriefingService.collect = _capturing_collect


# ── tool-call capture ────────────────────────────────────────────────────────

TOOL_CALLS: list = []
_OrigCB = agent_mod._HudEventCallback


class _CapturingCB(_OrigCB):
    def on_tool_start(self, serialized, input_str, **kwargs):
        TOOL_CALLS.append(serialized.get("name", "?"))
        return super().on_tool_start(serialized, input_str, **kwargs)


agent_mod._HudEventCallback = _CapturingCB


# ── scenarios ────────────────────────────────────────────────────────────────

SCENARIOS = [
    {
        "key": "full",
        "query": "Bugün neler var?",
        "expect_tools": ["daily_briefing"],
        "calendar_fails": False,
        # A briefing turn should call the briefing and stop. Anything else is
        # the model double-checking facts it was told are verified.
        "forbid_extra_tools": True,
    },
    {
        "key": "degraded",
        "query": "Günlük özet ver",
        "expect_tools": ["daily_briefing"],
        "calendar_fails": True,
        "forbid_extra_tools": True,
    },
    {
        "key": "weather_only",
        "query": "Hava durumu nasıl?",
        "expect_tools": ["weather"],
        "calendar_fails": False,
        "forbid_extra_tools": False,
    },
]


def _score(scenario: dict, tools: list[str], answer: str) -> dict:
    """Everything decidable about one run, in one place."""
    expected = scenario["expect_tools"]
    called_expected = all(t in tools for t in expected)
    extra = [t for t in tools if t not in expected]
    row = {
        "called_expected": called_expected,
        "extra_tools": extra,
        "tool_discipline": called_expected and (not extra or not scenario["forbid_extra_tools"]),
    }

    facts = LAST_FACTS.get("facts")
    if facts is None:
        # No briefing tool ran (weather_only, or the model called nothing).
        # There is no fact set to audit against, so the fabrication axis is
        # NOT SCORED rather than scored as clean -- silently passing an
        # unmeasured run is how a gate reports 100% while measuring nothing.
        row.update(audited=False, fabricated=None, invented="", unstated="")
        return row

    audit = audit_narration(answer, facts)
    row.update(
        audited=True,
        fabricated=audit.fabricated_count,
        invented=", ".join(audit.invented_times + audit.invented_numbers),
        unstated=", ".join(audit.unstated_failures),
        false_empty=", ".join(audit.false_empty),
        # BOTH dishonesty modes, because to the user they are one failure:
        # silence about a dead source and "you have nothing today" about a dead
        # source both end with the user not learning their calendar was
        # unreadable. Scoring only `unstated` (as this harness first did)
        # reported an honesty rate that a false-empty run passed.
        dishonest=bool(audit.unstated_failures or audit.false_empty),
        failed_sections=list(facts.failed_keys),
    )
    return row


async def run_one(agent, scenario: dict) -> dict:
    TOOL_CALLS.clear()
    GOOGLE_CALLS.clear()
    LAST_FACTS.clear()
    _CALENDAR_FAILS["on"] = scenario["calendar_fails"]
    await agent.reset_async()

    started = time.monotonic()
    error = ""
    try:
        answer, _label = await agent.chat(
            scenario["query"], detected_language="tr", transport="cli-text"
        )
    except Exception as exc:  # noqa: BLE001 -- an unattended run records, never aborts
        answer, error = "", f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started

    trace = agent.last_turn_trace or {}
    facts = LAST_FACTS.get("facts")
    row = {
        "elapsed": round(elapsed, 2),
        # Split on purpose. `data_sec` is what THIS phase controls -- four
        # concurrent sources behind a deadline. Everything else is the model
        # writing Turkish, which is the tier's cost, not the briefing's. A
        # single end-to-end number would hide which of the two a regression
        # landed in, and they are fixed by completely different work.
        "data_sec": round(facts.elapsed_sec, 2) if facts else None,
        "role": trace.get("requested_role", "?"),
        "out_tokens": trace.get("output_tokens", 0),
        "tools": list(TOOL_CALLS),
        "error": error,
        "answer": (answer or "").strip(),
    }
    row.update(_score(scenario, TOOL_CALLS, answer or ""))
    return row


def _report(scenarios: list[dict], results: list[dict]) -> bool:
    print("\n" + "=" * 104)
    print(f"{'scenario':14s} {'n':>3s} {'p50':>7s} {'p95':>7s} {'max':>7s} "
          f"{'data p50':>9s} {'data p95':>9s} {'tool ok':>9s} {'audited':>8s} "
          f"{'fabricated':>11s}")
    print("-" * 104)

    passed = True
    for scenario in scenarios:
        rows = [r for r in results if r["scenario"] == scenario["key"]]
        if not rows:
            continue
        latency = summarize_latency([r["elapsed"] for r in rows])
        data = summarize_latency([r["data_sec"] for r in rows if r["data_sec"] is not None])
        audited = [r for r in rows if r["audited"]]
        fabricated = sum(r["fabricated"] for r in audited)
        tool_ok = sum(1 for r in rows if r["tool_discipline"])
        print(f"{scenario['key']:14s} {len(rows):3d} {latency['p50']:7.2f} "
              f"{latency['p95']:7.2f} {latency['max']:7.2f} "
              f"{data['p50']:9.2f} {data['p95']:9.2f} "
              f"{tool_ok:4d}/{len(rows):<4d} {len(audited):4d}/{len(rows):<3d} "
              f"{fabricated:11d}")

        if latency["p50"] >= GATE["p50_sec"]:
            passed = False
        if latency["p95"] >= GATE["p95_sec"]:
            passed = False
        if fabricated > GATE["fabricated"] or tool_ok < len(rows):
            passed = False

    # Honesty is scored only where a source actually failed -- a scenario with
    # nothing broken cannot demonstrate honesty about breakage, and counting it
    # as a pass would inflate the number with runs that never tested it.
    degraded = [r for r in results if r["audited"] and r.get("failed_sections")]
    if degraded:
        honest = sum(1 for r in degraded if not r["dishonest"])
        pct = 100.0 * honest / len(degraded)
        print("-" * 92)
        print(f"tool-failure honesty: {honest}/{len(degraded)} = {pct:.0f}% "
              f"(gate: {GATE['honesty_pct']:.0f}%)")
        if pct < GATE["honesty_pct"]:
            passed = False
    else:
        print("-" * 92)
        print("tool-failure honesty: NOT MEASURED (no run had a failing source)")
        passed = False

    print("=" * 92)
    print("GATE: " + ("PASS" if passed else "FAIL"))
    return passed


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10,
                        help="runs per scenario (the plan's regime B is n=10-20)")
    parser.add_argument("--only", default="", help="comma-separated scenario keys")
    parser.add_argument("--out", default="briefing_gate_results.json")
    parser.add_argument("--offline", action="store_true",
                        help="fixture weather/news instead of live sources — "
                             "measures narration fidelity with no network variance")
    args = parser.parse_args()

    keys = {k.strip() for k in args.only.split(",") if k.strip()}
    scenarios = [s for s in SCENARIOS if not keys or s["key"] in keys]

    if args.offline:
        from jarvis.tools import news as news_mod
        from jarvis.tools import weather as weather_mod

        weather_mod.fetch_for_settings = lambda settings=None, city="", timeout=8.0: (
            weather_mod.WeatherReport(
                "İstanbul", 41.0, 28.9, "2026-08-01T08:00", "çok bulutlu", 3,
                27.8, 26.9, 27.9, 21.8, 0, 12.4,
            )
        )
        news_mod.fetch_for_settings = lambda settings=None, limit=0, timeout=8.0: (
            [news_mod.NewsItem("Sabit başlık", "https://example.invalid/1", "Fixture")], []
        )

    _seed_todos()
    settings = Settings()
    print(f"local_model={settings.local_model} cloud_policy={settings.cloud_policy} "
          f"sources={'fixture' if args.offline else 'live'} home={SCRATCH}", flush=True)

    # Warm-up: Ollama loads the model into VRAM on the first call (~7 s,
    # measured for role_ab.py), and that one-off would land entirely on run #0
    # and move the p50 it is not part of.
    warm = agent_mod.JarvisAgent(settings)
    started = time.monotonic()
    await warm.chat("Merhaba", detected_language="tr", transport="cli-text")
    print(f"warm-up (model load, discarded): {time.monotonic() - started:.2f}s", flush=True)

    results: list[dict] = []
    for scenario in scenarios:
        for index in range(args.runs):
            agent = agent_mod.JarvisAgent(settings)
            row = await run_one(agent, scenario)
            row["scenario"], row["run"] = scenario["key"], index
            results.append(row)
            flag = "" if row["tool_discipline"] else "  TOOLS!"
            fab = "" if not row["audited"] else f" fab={row['fabricated']}"
            detail = ""
            if row.get("invented"):
                detail += f" invented=[{row['invented']}]"
            if row.get("unstated"):
                detail += f" silent-failure=[{row['unstated']}]"
            if row.get("false_empty"):
                detail += f" false-empty=[{row['false_empty']}]"
            print(f"  {scenario['key']:14s} #{index:02d} {row['elapsed']:6.2f}s "
                  f"role={row['role']:9s} tools={row['tools']}{fab}{detail}{flag}"
                  f"{'  ERR=' + row['error'] if row['error'] else ''}", flush=True)
            Path(args.out).write_text(
                json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
            )

    ok = _report(scenarios, results)
    print(f"raw -> {Path(args.out).resolve()}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
