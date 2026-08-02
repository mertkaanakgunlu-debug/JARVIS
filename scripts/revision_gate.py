"""Post-MVP Faz 4 gate: does a multi-turn revision actually land?

The plan's second acceptance milestone is "5+ tur revizyon", and it is not a
question `pytest` can answer. The store, the patch semantics and the prompt
block are deterministic and have unit tests; what is stochastic is whether a
live model, handed an active object's spec, patches it instead of redrawing it
from scratch — or worse, says *"tamam, kırmızı yaptım"* and calls nothing.

That last failure is not hypothetical. Faz 3 measured exactly it one layer
over: asked about the weather, the non-thinking tier answered *"32°C, güneşli"*
in 4 of 5 runs without calling the tool, because a plausible answer existed. A
revision is the same shape — "I made it red" is always a plausible sentence.

So each turn is scored on TWO axes that a text answer cannot fake:

  * **the tool call** — did `chart_revise` actually run,
  * **the stored spec** — does the working set now hold the requested value.

The second is the real gate. A model can call `chart_revise(title=...)` when
asked for a colour and the first axis alone would call that a success.

The chain also carries two turns that are NOT revisions:

  * an unrelated question ("bugün hava nasıl"), which must NOT touch the chart
    — the working-set routing rule fires only on turns that classify as
    `conversation`, and this checks the cost of that decision,
  * a bare "teşekkürler", which classifies as `conversation` WITH an active
    object and therefore does get the chart tools offered. If the model
    revises something there, the rule is too aggressive and the measurement
    says so rather than the design claiming otherwise.

Everything is real: real model, real graph, real renderer, real SQLite. Home
and cwd are a temp directory outside the repo.

    .venv\\Scripts\\python.exe scripts\\revision_gate.py --runs 10
    .venv\\Scripts\\python.exe scripts\\revision_gate.py --runs 3 --arm fast
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

SCRATCH = Path(
    os.environ.get("REVISION_GATE_HOME")
    or Path(tempfile.gettempdir()) / "jarvis-revision-gate"
)
(SCRATCH / "Desktop").mkdir(parents=True, exist_ok=True)
os.environ["JARVIS_HOME"] = str(SCRATCH)
os.environ["JARVIS_SKIP_DOTENV"] = "1"
os.environ["CLOUD_POLICY"] = "off"
os.chdir(SCRATCH)

import jarvis.agent as agent_mod                                   # noqa: E402
from jarvis.briefing import summarize_latency                      # noqa: E402
from jarvis.config import Settings                                 # noqa: E402
from jarvis.graph.role_router import FAST, REASONING, RoleDecision  # noqa: E402
from jarvis.graph.tool_router import classify_query                # noqa: E402

FIXTURE = "ay,satis,gider\n2026-01,120,80\n2026-02,145,90\n2026-03,132,85\n2026-04,178,101\n"

# One conversation, in order. `check(spec, previous)` is the real gate: it reads
# the STORED SPEC after the turn, against the spec before it.
#
# `accept` is a SET of tools, not one, and that is a measured decision rather
# than a loosened standard. A live run changed only the title by calling
# `plot_data` instead of `chart_revise` -- the wrong tool, the right outcome,
# because a redraw of the same chart patches it (see chart_objects._same_chart).
# What Faz 4 claims is that the revision lands and unrelated state survives; WHICH
# tool the model reached for is reported separately, because it is real
# information about the model and no part of the claim. `accept=()` is different
# in kind and stays hard: no chart tool may run at all.
#
# Relative, not absolute, and that is the point. An absolute expectation for
# turn 0 ("kind must be line") scores the model's taste in chart types, not the
# working set -- a live run drew a scatter, which is a defensible reading of the
# request and would have failed a test about something else entirely. What Faz 4
# claims is narrower and checkable: the patch lands, AND everything the user did
# not mention survives it.

CARRIED = ("source", "x", "y")          # never mentioned again after turn 0


def _unchanged(spec, previous, keys=CARRIED):
    for key in keys:
        if str(previous.get(key, "")) != str(spec.get(key, "")):
            return False, f"{key} degisti: {previous.get(key)!r} -> {spec.get(key)!r}"
    return True, ""


def _is_red(spec, previous):
    if str(spec.get("color", "")).casefold() not in ("red", "kırmızı", "kirmizi"):
        return False, f"color={spec.get('color')!r}"
    return _unchanged(spec, previous)


def _titled(spec, previous):
    if "2026" not in str(spec.get("title", "")):
        return False, f"title={spec.get('title')!r}"
    # The colour set one turn earlier must survive a title change. This single
    # assertion is the difference between a working set and a redraw.
    return _unchanged(spec, previous, CARRIED + ("color",))


def _is_bar(spec, previous):
    if str(spec.get("kind", "")).casefold() != "bar":
        return False, f"kind={spec.get('kind')!r}"
    return _unchanged(spec, previous, CARRIED + ("color", "title"))


def _untouched(spec, previous):
    if spec != previous:
        return False, f"spec degisti: {previous} -> {spec}"
    return True, ""


def _undone(spec, previous):
    if str(spec.get("kind", "")).casefold() == "bar":
        return False, "kind hala 'bar' -- geri alinmadi"
    return _unchanged(spec, previous, CARRIED + ("color", "title"))


CHAIN = [
    {
        "say": "Masaüstündeki satis.csv dosyasının aylık satış grafiğini çiz",
        "accept": ("plot_data", "chart_revise"),
        # Only "an editable object now exists". Which chart the model chose is
        # its business; keeping it is this phase's.
        "check": lambda spec, previous: (bool(spec), "" if spec else "nesne olusmadi"),
        "note": "create -> an object exists",
    },
    {
        "say": "Çizgiyi kırmızı yap",
        "accept": ("plot_data", "chart_revise"),
        "check": _is_red,
        "note": "revise-1 colour (source/x/y survive)",
    },
    {
        "say": "Başlığı '2026 Satışları' yap",
        "accept": ("plot_data", "chart_revise"),
        "check": _titled,
        "note": "revise-2 title (colour survives)",
    },
    {
        "say": "Sütun grafiği olsun",
        "accept": ("plot_data", "chart_revise"),
        "check": _is_bar,
        "note": "revise-3 kind (colour+title survive)",
    },
    {
        "say": "Bugün hava nasıl?",
        "accept": (),
        "check": _untouched,
        "note": "unrelated turn must not touch the chart",
    },
    {
        "say": "Teşekkürler",
        "accept": (),
        "check": _untouched,
        "note": "conversation WITH an active object -- rule too eager?",
    },
    {
        "say": "Grafiği bir önceki haline geri al",
        "accept": ("working_set", "chart_revise", "plot_data"),
        "check": _undone,
        "note": "revise-4 undo (kind reverts, rest survives)",
    },
]

CHART_TOOLS = {"plot_data", "chart_revise", "working_set"}

TOOL_CALLS: list[str] = []
_OrigCB = agent_mod._HudEventCallback


TOOL_OUTPUTS: list[str] = []


class _CapturingCB(_OrigCB):
    def on_tool_start(self, serialized, input_str, **kwargs):
        TOOL_CALLS.append(serialized.get("name", "?"))
        return super().on_tool_start(serialized, input_str, **kwargs)

    def on_tool_end(self, output, **kwargs):
        # Recorded because the first version of this harness could not explain
        # its own misses: a turn showed `tools=['plot_data']` and no object, and
        # deciding whether the tool had errored took a separate hand-run. "Did
        # the tool actually succeed" must be answerable from the log -- the same
        # rule tool_router follows for its dropped tools.
        TOOL_OUTPUTS.append(str(getattr(output, "content", output))[:300])
        return super().on_tool_end(output, **kwargs)


agent_mod._HudEventCallback = _CapturingCB


def _force_role(role: str | None):
    """Pin the tier, leaving the tool route untouched.

    Only the role is overridden — the tool subset comes from the real router in
    both arms, or the comparison measures two things at once (role_ab.py's own
    rule, and the reason its numbers mean anything).
    """
    if role is None:
        return
    decision = RoleDecision(role, "forced_by_harness")
    agent_mod._route_query = lambda query, needs_planning: (classify_query(query), decision)


async def run_chain(settings, arm: str | None, index: int) -> list[dict]:
    from jarvis.working_set import KIND_CHART

    agent = agent_mod.JarvisAgent(settings)
    conversation = f"rev-{arm or 'auto'}-{index}"
    agent.working_set.clear(conversation)

    rows = []
    previous: dict = {}
    for step, turn in enumerate(CHAIN):
        TOOL_CALLS.clear()
        TOOL_OUTPUTS.clear()
        started = time.monotonic()
        error = ""
        try:
            answer, _label = await agent.chat(
                turn["say"], detected_language="tr",
                transport="cli-text", conversation_id=conversation,
            )
        except Exception as exc:  # noqa: BLE001 -- an unattended run records, never aborts
            answer, error = "", f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started

        obj = agent.working_set.active(conversation, KIND_CHART)
        spec = dict(obj.spec) if obj else {}
        called = [t for t in TOOL_CALLS if t in CHART_TOOLS]

        if not turn["accept"]:
            tool_ok = not called
        else:
            tool_ok = any(t in called for t in turn["accept"])
        spec_ok, why = turn["check"](spec, previous)
        previous = dict(spec)

        rows.append({
            "run": index, "arm": arm or "auto", "step": step, "note": turn["note"],
            "say": turn["say"], "elapsed": round(elapsed, 2),
            "role": (agent.last_turn_trace or {}).get("requested_role", "?"),
            "tools": list(TOOL_CALLS), "tool_ok": tool_ok,
            "spec_ok": spec_ok, "why": why,
            "version": obj.version if obj else 0,
            "spec": spec, "error": error, "answer": (answer or "")[:200],
            "tool_outputs": list(TOOL_OUTPUTS),
        })
    return rows


def _report(results: list[dict], arms: list[str | None]) -> bool:
    print("\n" + "=" * 100)
    print(f"{'arm':6s} {'step':4s} {'what':44s} {'OUTCOME':>8s} {'tool':>7s} {'p50':>7s}  used")
    print("-" * 100)
    passed = True
    for arm in arms:
        label = arm or "auto"
        for step, turn in enumerate(CHAIN):
            rows = [r for r in results if r["arm"] == label and r["step"] == step]
            if not rows:
                continue
            tool_ok = sum(1 for r in rows if r["tool_ok"])
            spec_ok = sum(1 for r in rows if r["spec_ok"])
            latency = summarize_latency([r["elapsed"] for r in rows])
            used = sorted({t for r in rows for t in r["tools"] if t in CHART_TOOLS})
            print(f"{label:6s} {step:4d} {turn['note'][:44]:44s} "
                  f"{spec_ok:3d}/{len(rows):<4d} {tool_ok:3d}/{len(rows):<3d} "
                  f"{latency['p50']:7.2f}  {','.join(used) or '-'}")
            # The OUTCOME axis is the gate. The tool axis gates only the two
            # turns where "no chart tool ran" IS the outcome being claimed.
            if spec_ok < len(rows):
                passed = False
            if not turn["accept"] and tool_ok < len(rows):
                passed = False

        chains = {}
        for r in results:
            if r["arm"] == label:
                chains.setdefault(r["run"], []).append(r)
        whole = sum(1 for rows in chains.values() if all(x["spec_ok"] for x in rows))
        print(f"{label:6s} {'':4s} {'FULL CHAIN (all 7 turns correct)':46s} {whole:3d}/{len(chains):<3d}")
    print("=" * 100)
    print("GATE: " + ("PASS" if passed else "FAIL"))
    return passed


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--arm", default="", help="fast | reasoning (default: whatever the router picks)")
    parser.add_argument("--out", default="revision_gate_results.json")
    args = parser.parse_args()

    (SCRATCH / "Desktop" / "satis.csv").write_text(FIXTURE, encoding="utf-8")
    (SCRATCH / "satis.csv").write_text(FIXTURE, encoding="utf-8")

    arms: list[str | None] = [args.arm] if args.arm else [None]
    if args.arm == "both":
        arms = [FAST, REASONING]

    settings = Settings()
    print(f"local_model={settings.local_model} cloud_policy={settings.cloud_policy} home={SCRATCH}",
          flush=True)

    warm = agent_mod.JarvisAgent(settings)
    started = time.monotonic()
    await warm.chat("Merhaba", detected_language="tr", transport="cli-text")
    print(f"warm-up (model load, discarded): {time.monotonic() - started:.2f}s", flush=True)

    results: list[dict] = []
    for arm in arms:
        _force_role(arm)
        for index in range(args.runs):
            rows = await run_chain(settings, arm, index)
            results.extend(rows)
            for r in rows:
                flag = "" if (r["tool_ok"] and r["spec_ok"]) else "   <-- MISS"
                print(f"  {r['arm']:6s} #{r['run']:02d}.{r['step']} {r['elapsed']:6.2f}s "
                      f"role={r['role']:9s} tools={r['tools']} v{r['version']} "
                      f"{r['why']}{flag}", flush=True)
            Path(args.out).write_text(
                json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            print("", flush=True)

    ok = _report(results, arms)
    print(f"raw -> {Path(args.out).resolve()}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
