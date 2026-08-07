"""Completion contract A/B — measure `required_outputs_mode` on real qwen3:8b.

    .venv\\Scripts\\python.exe scripts\\completion_contract_ab.py --corpus A --runs 10 --label pilot

The gate this feeds is pre-registered and lives in the repository:
`docs/eval/completion_contract_gate.md`. It was written before any result
existed and is NOT restated here -- the harness reads it, prints each clause
with the measured value beside it, and prints the decision itself, so the
outcome cannot be re-interpreted in prose afterwards.

Reuses, deliberately, rather than building a second eval framework:

  * `jarvis.evals.results.ResultWriter`  -- absolute timestamped path, atomic
    write after every trial, so a killed run keeps what it measured
  * `jarvis.evals.contract_scenarios`    -- the manifest, importable by pytest
  * `scripts/plot_intent_ab.py`'s trial-isolation protocol -- unique
    conversation id, background-task drain, targeted artifact cleanup, both
    fixture paths hashed before and after

**Two warm agents, not one reconfigured between trials.**
`required_outputs_mode` is bound into the graph's node closures at construction
time (`build_graph` decides whether `output_contract` exists at all), so
mutating the setting mid-run changes nothing. Both arms run the same entry
point per corpus -- otherwise a transport difference contaminates object
success, latency and the streaming-duplicate metric at once.

**The canonical artifact source is `ToolMessage.artifact` in
`state["messages"]`**, read off the turn's checkpoint. Not the ledger (no
artifact column, no `tool_call_id`) and not `execution_envelopes` (only written
when `execution_contract_mode != "off"`, so it would silently tie this
measurement to an unrelated flag). Envelopes are recorded as auxiliary
telemetry only. The ledger schema is not extended for this work.

Isolation: its own `JARVIS_HOME`, `CLOUD_POLICY=off`, no real user memory or
session data, no Calendar/Gmail/Contacts, no external writes, no action that
would need a confirmation, a per-trial timeout, and no dependency added.
Absolute local paths are reduced to basenames on the way out.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INVOCATION_CWD = Path.cwd()

# Set BEFORE importing jarvis: paths.py and Settings read these at import time.
SCRATCH = Path(
    os.environ.get("CONTRACT_AB_HOME")
    or (Path(os.environ.get("TEMP", "/tmp")) / "jarvis-contract-ab")
).resolve()
SCRATCH.mkdir(parents=True, exist_ok=True)
os.environ["JARVIS_HOME"] = str(SCRATCH)
os.environ["JARVIS_SKIP_DOTENV"] = "1"
os.environ["CLOUD_POLICY"] = "off"
os.chdir(SCRATCH)

from langchain_core.messages import ToolMessage  # noqa: E402

import jarvis.agent as agent_mod  # noqa: E402
from jarvis.config import Settings  # noqa: E402
from jarvis.evals.contract_metrics import percentile, rate  # noqa: E402
from jarvis.evals.contract_scenarios import Scenario, by_corpus  # noqa: E402
from jarvis.evals.results import ResultWriter, default_path, head_commit, timestamp  # noqa: E402
from jarvis.execution.artifacts import parse_refs  # noqa: E402
from jarvis.voice.session import parse_confirm_marker, parse_progress_marker  # noqa: E402
from jarvis.working_set import KIND_CHART  # noqa: E402

# ── Fixtures ────────────────────────────────────────────────────────────────

GOOD_CSV = "ay,satis,gider\n2026-01,120,80\n2026-02,145,90\n2026-03,132,85\n2026-04,178,101\n"
CORRUPT_CSV = "\x00\x01 not,a;valid\ncsv\x00 at all\n"

FIXTURES: dict[str, str] = {
    "satis.csv": GOOD_CSV,
    "bozuk.csv": CORRUPT_CSV,
}
FIXTURE_PATHS = tuple(
    SCRATCH / sub / name for name in FIXTURES for sub in ("Desktop", ".")
)

ARMS = ("control", "treatment")
ARM_MODE = {"control": "off", "treatment": "enforce"}
DEFAULT_TIMEOUT_S = 300.0


# ── Instrumentation ─────────────────────────────────────────────────────────

TOOL_CALLS: list[dict] = []
TOOL_ROUNDS: list[list[str]] = []
_OrigCB = agent_mod._HudEventCallback


def _tool_args(raw) -> dict:
    """Callback input is not always a dict; a JSON string is common."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"raw": raw}
        except json.JSONDecodeError:
            return {"raw": raw}
    return {"raw": str(raw)}


class _CapturingCB(_OrigCB):
    def on_tool_start(self, serialized, input_str, **kwargs):
        args = _tool_args(input_str)
        TOOL_CALLS.append({
            "tool": serialized.get("name", "?"),
            "path": _safe_path(str(args.get("path", ""))),
        })
        return super().on_tool_start(serialized, input_str, **kwargs)

    def on_llm_end(self, response, **kwargs):
        # A ROUND is one model output carrying tool calls; counting individual
        # calls would report a parallel batch as several rounds.
        try:
            calls = getattr(response.generations[0][0].message, "tool_calls", None)
            if calls:
                TOOL_ROUNDS.append([c.get("name", "?") for c in calls])
        except Exception:  # noqa: BLE001 -- instrumentation never fails a run
            pass
        return super().on_llm_end(response, **kwargs)


agent_mod._HudEventCallback = _CapturingCB


def _safe_path(path: str) -> str:
    """Basename only. Results are committed as a sanitized derivative and must
    carry no absolute local path (and therefore no username)."""
    text = str(path or "").strip()
    return Path(text).name if text else ""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def snapshot_sources() -> dict[str, dict]:
    """Hash AND existence: a model rewriting a fixture with identical content
    leaves the hash equal, and a deletion is a different state from a change."""
    return {
        _safe_path(str(p)) + f"#{i}": {"exists": p.exists(), "sha256": _sha(p)}
        for i, p in enumerate(FIXTURE_PATHS)
    }


def reset_fixtures() -> None:
    for name, body in FIXTURES.items():
        for sub in ("Desktop", "."):
            target = SCRATCH / sub / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")


def clean_artifacts() -> None:
    """Only the known eval OUTPUT directories -- never a broad sweep, which
    could delete a fixture or the evidence of a mutation, and would change what
    `file_list` returns for the next arm."""
    for directory in (SCRATCH / "data" / "runs", SCRATCH / "plots", SCRATCH / "reports"):
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)


#: Eval-only ceiling for drain_background_tasks() below -- see its docstring.
#: Not a jarvis/ setting; this bounds nothing outside this script.
BACKGROUND_DRAIN_TIMEOUT_S = 30.0


async def drain_background_tasks(
    agent, *, timeout_s: float = BACKGROUND_DRAIN_TIMEOUT_S,
) -> dict:
    """`chat()`/`chat_stream()` schedule memory extraction onto `_bg_tasks`.
    Left running it overlaps the next trial -- breaking "a live harness runs
    alone" and polluting the latency numbers. Snapshot first: the
    done-callback mutates the set while it drains.

    Completion-contract TTFB follow-up (2026-08-07): this used to await
    `asyncio.gather(*pending)` with NO bound at all. A live run hit a trial
    that resolved after 4224s against a nominal 300s `asyncio.wait_for`
    timeout -- `elapsed_s` (computed once, after this call, in run_trial())
    could not tell whether that gap was the foreground call's own slow
    cancellation or an unbounded wait here, so the anomaly was reported with
    the two candidate causes unseparated. This is eval-only harness
    instrumentation: it changes what THIS SCRIPT waits for and reports, not
    jarvis/agent.py's own (still unbounded, still correct-for-production)
    `_bg_tasks` handling -- a production turn has no reason to abandon a
    scheduled memory-extraction job, but a benchmark corpus has every reason
    not to let one hung job silently stall 53 remaining trials.

    Returns telemetry, never raises: {count, drained, timed_out, elapsed_s}.
    A task that does not finish within `timeout_s` is cancelled (not left to
    run unobserved) and the drain is reported as timed_out=True rather than
    silently presented as a clean drain.
    """
    pending = list(getattr(agent, "_bg_tasks", ()) or ())
    result: dict = {"count": len(pending), "drained": 0, "timed_out": False, "elapsed_s": 0.0}
    if not pending:
        return result
    drain_started = time.perf_counter()
    try:
        done, not_done = await asyncio.wait(pending, timeout=timeout_s)
        result["drained"] = len(done)
        if not_done:
            result["timed_out"] = True
            for t in not_done:
                t.cancel()
            # Give cancellation a short, ALSO-bounded window to land -- not
            # unbounded either, for the same reason the drain itself is
            # bounded above.
            await asyncio.wait(not_done, timeout=5.0)
    finally:
        result["elapsed_s"] = round(time.perf_counter() - drain_started, 3)
    return result


# ── Reading the turn back ───────────────────────────────────────────────────

def _turn_state(agent) -> dict:
    """The finished turn's graph state, off the checkpointer.

    Every entry point builds `thread_id = f"{session_id}-t{turn}"`, so the
    harness can address the turn it just ran without patching the agent to hand
    its config back.
    """
    config = {"configurable": {"thread_id": f"{agent.session_id}-t{agent._turn}"}}
    try:
        tup = agent._checkpointer.get_tuple(config)
        return dict(tup.checkpoint["channel_values"]) if tup else {}
    except Exception:  # noqa: BLE001 -- a missing checkpoint is a result, not a crash
        return {}


def _artifacts_from_messages(messages) -> list[dict]:
    """THE canonical artifact evidence: what each tool declared, out of band, on
    ToolMessage.artifact. Mode-independent -- safe_tools attaches it on every
    call regardless of `execution_contract_mode`."""
    out: list[dict] = []
    for message in messages or ():
        if not isinstance(message, ToolMessage):
            continue
        for ref in parse_refs(getattr(message, "artifact", None)):
            out.append({
                "tool_call_id": str(getattr(message, "tool_call_id", "") or ""),
                "kind": ref.kind,
                "produced_by": ref.produced_by,
                "name": _safe_path(ref.path),
            })
    return out


def _tool_call_ids(messages) -> list[dict]:
    from langchain_core.messages import AIMessage

    out: list[dict] = []
    for message in messages or ():
        if isinstance(message, AIMessage):
            for call in getattr(message, "tool_calls", None) or ():
                out.append({"id": str(call.get("id") or ""),
                            "tool": str(call.get("name") or "")})
    return out


def _classify_delta(delta: str) -> str:
    """"progress" | "confirmation" | "answer" for one chat_stream() delta.

    Completion-contract TTFB follow-up. Reuses the SAME parsers every real
    consumer (API SSE, Electron, mobile, voice) now uses to recognize these
    control frames, rather than re-deriving the shape here -- so the harness
    can never classify a delta differently than a real client would.
    """
    if parse_progress_marker(delta) is not None:
        return "progress"
    if parse_confirm_marker(delta) is not None:
        return "confirmation"
    return "answer"


def failure_class(row: dict) -> str:
    """One label per trial, computed -- never hand-derived in a write-up."""
    if row.get("object_created"):
        return "satisfied"
    status = row.get("contract_status") or ""
    if status:
        return status.lower()
    if not row.get("chart_attempted"):
        return "not_attempted"
    if not row.get("chart_executed"):
        return "attempted_not_executed"
    return "executed_no_object"


# ── One trial ───────────────────────────────────────────────────────────────

async def run_trial(
    agent, scenario: Scenario, *, arm: str, run_id: str, sequence: int,
    timeout_s: float,
) -> dict:
    from jarvis.nlu.output_intent import required_outputs_for

    conversation = f"cc-{run_id}-{sequence:03d}-{arm}"
    agent.working_set.clear(conversation)

    TOOL_CALLS.clear()
    TOOL_ROUNDS.clear()
    before = snapshot_sources()
    reset_fixtures()
    clean_artifacts()

    settings = agent.settings
    row: dict = {
        "repo_sha": head_commit(),
        "run_id": run_id,
        "scenario_id": scenario.id,
        "corpus": scenario.corpus,
        "arm": arm,
        "mode": ARM_MODE[arm],
        "repetition": sequence,
        "entry_point": scenario.entry_point,
        "config": {
            "required_outputs_mode": settings.required_outputs_mode,
            "execution_contract_mode": settings.execution_contract_mode,
            "cloud_policy": settings.cloud_policy,
            "local_model": settings.local_model,
            "local_reasoning_effort": getattr(settings, "local_reasoning_effort", ""),
            "max_tool_rounds_per_turn": settings.max_tool_rounds_per_turn,
            "max_tool_calls_per_turn": settings.max_tool_calls_per_turn,
            "confirmation_gate_enabled": settings.confirmation_gate_enabled,
        },
        "expected_requirement": scenario.expected_requirement,
        "resolved_requirement": (required_outputs_for(scenario.query) or [None])[0],
        "timeout_s": timeout_s,
    }

    started = time.perf_counter()
    first_visible: float | None = None
    # Completion-contract TTFB follow-up (2026-08-07). `time_to_first_visible_s`
    # keeps its ORIGINAL, historical, pre-registered meaning below -- literally
    # whatever delta chat_stream() yields first, unchanged. These two are new,
    # additive fields; they answer a different question and must never be
    # substituted for the first one in a report (see the follow-up eval doc).
    first_visible_kind: str | None = None
    first_answer_token: float | None = None
    answer, timed_out, error = "", False, ""
    try:
        if scenario.entry_point == "chat_stream":
            chunks: list[str] = []

            async def _stream() -> None:
                nonlocal first_visible, first_visible_kind, first_answer_token
                async for delta in agent.chat_stream(
                    scenario.query, detected_language="tr",
                    transport="cli-text", conversation_id=conversation,
                ):
                    now = time.perf_counter() - started
                    kind = _classify_delta(delta)
                    if first_visible is None:
                        first_visible = now
                        first_visible_kind = kind
                    if first_answer_token is None and kind == "answer":
                        first_answer_token = now
                    # Only answer-shaped deltas accumulate into `answer` --
                    # a control frame is not streamed answer text, and letting
                    # it into `chunks` would inflate/skew streamed_len against
                    # every historical run, none of which ever had one.
                    if kind == "answer":
                        chunks.append(delta)

            await asyncio.wait_for(_stream(), timeout=timeout_s)
            answer = "".join(chunks)
        elif scenario.entry_point == "background_turn":
            answer = await asyncio.wait_for(
                agent.background_turn(scenario.query, conversation_id=conversation),
                timeout=timeout_s,
            )
        else:
            answer, _ = await asyncio.wait_for(
                agent.chat(scenario.query, detected_language="tr",
                           transport="cli-text", conversation_id=conversation),
                timeout=timeout_s,
            )
    except asyncio.TimeoutError:
        timed_out = True
    except Exception as exc:  # noqa: BLE001 -- an exception is a RESULT here
        error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        # Completion-contract TTFB follow-up (2026-08-07): phase split, added
        # after a live trial resolved at elapsed_s=4224.3 against a nominal
        # 300s asyncio.wait_for timeout with NO way to tell whether the
        # excess was the foreground call's own slow cancellation or an
        # unbounded background-task wait -- `elapsed_s` alone conflated both.
        # `foreground_elapsed_s` is captured HERE, at the moment the
        # foreground call (success, timeout, or other exception) has
        # actually resolved -- before the background drain below runs.
        # `elapsed_s` below is UNCHANGED: same formula, same call site
        # relative to this finally block, still the pre-registered gate's
        # own field.
        foreground_elapsed = time.perf_counter() - started
        drain_info = await drain_background_tasks(agent)

    elapsed = time.perf_counter() - started
    # timed_out=True means asyncio.wait_for's timer fired at `timeout_s`; the
    # gap between that request and when it actually resolved is what a
    # non-cooperative cancellation (or a call that keeps receiving bytes
    # without ever being silent, e.g. a "thinking" model's long reasoning
    # trace never going idle) costs on top of the nominal ceiling.
    cancellation_cleanup = (
        round(foreground_elapsed - timeout_s, 3) if timed_out else None
    )
    state = _turn_state(agent)
    messages = state.get("messages") or []
    artifacts = _artifacts_from_messages(messages)
    chart_artifacts = [a for a in artifacts if a["kind"] == "chart"]

    obj = None
    try:
        obj = agent.working_set.active(conversation, KIND_CHART)
    except Exception:  # noqa: BLE001
        pass

    tools_used = [c["tool"] for c in TOOL_CALLS]
    after = snapshot_sources()

    row.update({
        "tool_calls": tools_used,
        "tool_call_ids": _tool_call_ids(messages),
        "tool_rounds": list(TOOL_ROUNDS),
        "tool_round_count": len(TOOL_ROUNDS),
        "tool_calls_attempted": int(state.get("tool_calls_attempted") or 0),
        "artifacts": artifacts,
        "artifact_kinds": sorted({a["kind"] for a in artifacts}),
        "working_set_object": bool(obj),
        "working_set_artifacts": [_safe_path(p) for p in (obj.source_artifacts if obj else ())],
        "object_created": bool(obj) and bool(chart_artifacts),
        "chart_attempted": "plot_data" in tools_used,
        "chart_executed": bool(chart_artifacts),
        "contract_status": "",          # filled from telemetry below
        "contract_action": str(state.get("output_contract_action") or ""),
        "response_origin": str(state.get("response_origin") or ""),
        "repair_attempted": int(state.get("repair_attempts_total") or 0) > 0,
        "repair_count": int(state.get("repair_attempts_total") or 0),
        "repair_reason": str(state.get("repair_reason") or ""),
        "final_response": (state.get("response") or answer or "")[:600],
        "streamed_len": len(answer),
        "elapsed_s": round(elapsed, 3),
        "time_to_first_visible_s": round(first_visible, 3) if first_visible else None,
        "first_visible_kind": first_visible_kind,
        "time_to_first_answer_token_s": (
            round(first_answer_token, 3) if first_answer_token else None
        ),
        "timed_out": timed_out,
        "error": error,
        # Completion-contract TTFB follow-up: phase split of what `elapsed_s`
        # alone conflates. `elapsed_s` above is UNCHANGED (still foreground +
        # drain, same formula, same historical/pre-registered field) --
        # these four are additive, so a stall can be attributed to a phase
        # instead of just showing up as one large, unexplained number.
        "foreground_elapsed_s": round(foreground_elapsed, 3),
        "timeout_requested_s": timeout_s,
        "cancellation_cleanup_s": cancellation_cleanup,
        "background_drain_s": drain_info["elapsed_s"],
        "background_drain_task_count": drain_info["count"],
        "background_drain_timed_out": drain_info["timed_out"],
        "source_mutation": _source_mutation(before, after),
        # Auxiliary only -- never the artifact source of truth.
        "execution_envelopes": len(state.get("execution_envelopes") or []),
    })
    row["repair_success"] = bool(row["repair_attempted"] and row["object_created"])
    row["failure_class"] = failure_class(row)
    return row


def _source_mutation(before: dict, after: dict) -> dict:
    changed = [k for k in before
               if before[k]["sha256"] != after.get(k, {}).get("sha256", "")]
    deleted = [k for k in before if before[k]["exists"] and not after.get(k, {}).get("exists")]
    return {"changed": changed, "deleted": deleted,
            "any": bool(changed or deleted)}


# ── Reporting ───────────────────────────────────────────────────────────────

_rate = rate
_pct = percentile


def report(rows: list[dict], label: str) -> dict:
    """Print per-arm metrics AND the pre-registered gate, clause by clause."""
    print(f"\n{'=' * 72}\ncompletion contract A/B — {label}\n{'=' * 72}")
    per_arm = {arm: [r for r in rows if r["arm"] == arm] for arm in ARMS}

    for arm, arm_rows in per_arm.items():
        if not arm_rows:
            continue
        created, n = _rate(arm_rows, "object_created")
        # Completion-contract TTFB follow-up (2026-08-07): a timed-out row's
        # elapsed_s is bounded by (timeout + cancellation-cleanup cost), NOT
        # by how long the model would actually have taken to answer -- and
        # the live run that motivated this split had exactly one such row.
        # Neither silently folding it into "the" latency number nor silently
        # dropping it is honest, so both views are printed, clearly labeled.
        completed = [r for r in arm_rows if not r.get("timed_out") and not r.get("error")]
        timed_out_rows = [r for r in arm_rows if r.get("timed_out")]
        errored_rows = [r for r in arm_rows if r.get("error") and not r.get("timed_out")]

        print(f"\n-- {arm} (mode={ARM_MODE[arm]}, n={n}) --")
        print(f"   object_created         {created}/{n}")
        print(f"   chart attempted        {_rate(arm_rows, 'chart_attempted')[0]}/{n}")
        print(f"   chart executed         {_rate(arm_rows, 'chart_executed')[0]}/{n}")
        print(f"   repair triggered       {_rate(arm_rows, 'repair_attempted')[0]}/{n}")
        print(f"   repair success         {_rate(arm_rows, 'repair_success')[0]}/{n}")
        print(f"   source mutation        {_rate(arm_rows, 'source_mutation')[0]}/{n}")
        print(f"   completed/timed-out/errored   {len(completed)}/{len(timed_out_rows)}/{len(errored_rows)}")
        print(f"   tool rounds (mean)     {sum(r['tool_round_count'] for r in arm_rows)/max(n,1):.2f}")

        # [all, incl. timeouts] -- UNCHANGED formula from before this
        # follow-up. Printed because it is exactly what _gate_verdict()
        # below computes from these same rows, so a reader can cross-check
        # the pre-registered clause's own numbers here without re-deriving
        # them from the raw JSON.
        lat = [r["elapsed_s"] for r in arm_rows if r.get("elapsed_s")]
        first = [r["time_to_first_visible_s"] for r in arm_rows if r.get("time_to_first_visible_s")]
        print(f"   [all, incl. timeouts] latency p50/p90        {_pct(lat, .5)}s / {_pct(lat, .9)}s")
        if first:
            print(f"   [all, incl. timeouts] first-visible p50/p90  {_pct(first, .5)}s / {_pct(first, .9)}s")

        # [completed only] -- excludes timed-out/errored rows, so a stalled
        # trial's inflated elapsed_s cannot skew what "the model's latency"
        # is reported as.
        completed_lat = [r["elapsed_s"] for r in completed if r.get("elapsed_s")]
        completed_first = [r["time_to_first_visible_s"] for r in completed if r.get("time_to_first_visible_s")]
        completed_first_answer = [
            r["time_to_first_answer_token_s"] for r in completed if r.get("time_to_first_answer_token_s")
        ]
        if completed_lat:
            print(f"   [completed only] latency p50/p90             {_pct(completed_lat, .5)}s / {_pct(completed_lat, .9)}s")
        if completed_first:
            print(f"   [completed only] first-visible p50/p90       {_pct(completed_first, .5)}s / {_pct(completed_first, .9)}s")
        # first-visible is whatever arrived first (a progress marker
        # counts); first-answer-token is specifically when real answer text
        # started -- see this file's own _classify_delta(). Reporting one in
        # place of the other is exactly what the follow-up doc's honesty
        # section forbids.
        if completed_first_answer:
            print(f"   [completed only] first-answer-token p50/p90  {_pct(completed_first_answer, .5)}s / {_pct(completed_first_answer, .9)}s")

        # [timed-out] -- count plus its OWN wall-clock telemetry, never
        # silently absorbed into either view above.
        if timed_out_rows:
            fg = [r["foreground_elapsed_s"] for r in timed_out_rows if r.get("foreground_elapsed_s")]
            cleanup = [
                r["cancellation_cleanup_s"] for r in timed_out_rows
                if r.get("cancellation_cleanup_s") is not None
            ]
            print(f"   [timed-out] count                            {len(timed_out_rows)}/{n}")
            if fg:
                print(f"   [timed-out] foreground p50/p90               {_pct(fg, .5)}s / {_pct(fg, .9)}s")
            if cleanup:
                print(f"   [timed-out] cancellation-cleanup p50/p90     {_pct(cleanup, .5)}s / {_pct(cleanup, .9)}s")

        drain = [r["background_drain_s"] for r in arm_rows if r.get("background_drain_s")]
        if drain:
            print(f"   background-drain p50/p90 (all trials)        {_pct(drain, .5)}s / {_pct(drain, .9)}s")

        kinds: dict[str, int] = {}
        for r in arm_rows:
            k = r.get("first_visible_kind")
            if k:
                kinds[k] = kinds.get(k, 0) + 1
        if kinds:
            print(f"   first-visible kind     {dict(sorted(kinds.items()))}")
        classes: dict[str, int] = {}
        for r in arm_rows:
            classes[r["failure_class"]] = classes.get(r["failure_class"], 0) + 1
        print(f"   failure classes        {dict(sorted(classes.items()))}")

    return _gate_verdict(per_arm)


def _gate_verdict(per_arm: dict[str, list[dict]]) -> dict:
    """Evaluate `docs/eval/completion_contract_gate.md`, clause by clause.

    Printed by the harness itself so the decision cannot be re-interpreted in
    prose afterwards. Clauses this run has no data for are reported UNMEASURED
    -- never silently treated as passed.
    """
    control, treatment = per_arm.get("control", []), per_arm.get("treatment", [])
    clauses: list[tuple[str, bool | None, str]] = []

    def add(name: str, ok: bool | None, detail: str) -> None:
        clauses.append((name, ok, detail))

    if control and treatment:
        c_created = _rate(control, "object_created")[0] / max(len(control), 1)
        t_created = _rate(treatment, "object_created")[0] / max(len(treatment), 1)
        delta = (t_created - c_created) * 10
        add("A: object_created delta >= +2/10", delta >= 2.0, f"{delta:+.1f}/10")
    else:
        add("A: object_created delta >= +2/10", None, "both arms required")

    b_rows = [r for r in control + treatment if r["corpus"] == "B"]
    if b_rows:
        fp = sum(1 for r in b_rows if r.get("resolved_requirement"))
        unexpected = sum(1 for r in b_rows if r.get("object_created"))
        add("B: false_positive_contract = 0", fp == 0, str(fp))
        add("B: unexpected_chart_created = 0", unexpected == 0, str(unexpected))
    else:
        add("B: false_positive_contract = 0", None, "corpus B not run")
        add("B: unexpected_chart_created = 0", None, "corpus B not run")

    c_rows = [r for r in treatment if r["corpus"] == "C"]
    eligible = [r for r in c_rows if r.get("chart_attempted") and not r.get("chart_executed")]
    if c_rows:
        retried = sum(1 for r in eligible if r.get("repair_attempted"))
        add("C: honest_failure_retried = 0", retried == 0, str(retried))
        add("C: repair_loop = 0", all(r["repair_count"] <= 1 for r in c_rows),
            f"max repair_count={max((r['repair_count'] for r in c_rows), default=0)}")
        add("C: not INCONCLUSIVE", len(eligible) >= 5,
            f"{len(eligible)} eligible (need >= 5 pilot)")
    else:
        for name in ("C: honest_failure_retried = 0", "C: repair_loop = 0",
                     "C: not INCONCLUSIVE"):
            add(name, None, "corpus C not run")

    all_rows = control + treatment
    muts = sum(1 for r in all_rows if r.get("source_mutation", {}).get("any"))
    add("source mutation = 0", muts == 0, str(muts))

    if control and treatment:
        c_ane = sum(1 for r in control if r["failure_class"] == "attempted_not_executed")
        t_ane = sum(1 for r in treatment if r["failure_class"] == "attempted_not_executed")
        norm = (t_ane / max(len(treatment), 1) - c_ane / max(len(control), 1)) * 10
        add("treatment attempted_not_executed <= control + 1/10", norm <= 1.0,
            f"{norm:+.1f}/10")
        c_lat = _pct([r["elapsed_s"] for r in control], .9)
        t_lat = _pct([r["elapsed_s"] for r in treatment], .9)
        add("treatment total latency p90 <= control p90 x 1.5",
            t_lat <= c_lat * 1.5 if c_lat else None, f"{t_lat}s vs {c_lat}s")
        t_first = _pct([r["time_to_first_visible_s"] for r in treatment
                        if r.get("time_to_first_visible_s")], .9)
        add("treatment first-visible p90 <= 60s (ABSOLUTE)",
            t_first <= 60.0 if t_first else None, f"{t_first}s")
        c_rounds = sum(r["tool_round_count"] for r in control) / max(len(control), 1)
        t_rounds = sum(r["tool_round_count"] for r in treatment) / max(len(treatment), 1)
        add("treatment mean tool rounds <= control + 1.0", t_rounds <= c_rounds + 1.0,
            f"{t_rounds:.2f} vs {c_rounds:.2f}")

    add("streaming duplicate output = 0", None,
        "proven deterministically -- tests/test_output_contract_streaming.py")

    print(f"\n{'-' * 72}\nPRE-REGISTERED GATE  (docs/eval/completion_contract_gate.md)\n{'-' * 72}")
    for name, ok, detail in clauses:
        mark = "PASS" if ok is True else ("FAIL" if ok is False else "UNMEASURED")
        print(f"  [{mark:^10}] {name:<52} {detail}")

    failed = [c for c in clauses if c[1] is False]
    unmeasured = [c for c in clauses if c[1] is None]
    if failed:
        # Neutral on purpose -- earlier phrasing said "stays at `shadow`",
        # which assumes the ambient required_outputs_mode was already at
        # `shadow` before this gate ran. It never was (see
        # docs/eval/completion_contract_pilot_2026-08-05.md's rollout-state
        # clarification): the real starting AND operational default is
        # `off`, and there is no pre-registered off->shadow gate. The
        # harness cannot assume a rollout stage it never measured.
        gate_decision = "NO_PROMOTION"
        decision = "ROLLOUT DECISION: NO PROMOTION"
    elif unmeasured:
        gate_decision = "INCOMPLETE"
        decision = ("ROLLOUT DECISION: INCOMPLETE -- "
                    f"{len(unmeasured)} clause(s) unmeasured; no promotion")
    else:
        gate_decision = "PROMOTION_LICENSED"
        decision = "ROLLOUT DECISION: promotion to `enforce` is licensed"
    print(f"\n{decision}\n")
    return {
        "clauses": [{"clause": n, "pass": o, "detail": d} for n, o, d in clauses],
        "decision": decision,
        "gate_decision": gate_decision,
        # The actual configured default, not a literal -- honest even if a
        # future session ever changes required_outputs_mode's own default in
        # jarvis/config.py. A "promotion licensed" verdict does not itself
        # flip this: nothing in this repo auto-applies a gate result, so the
        # operational mode after ANY pilot is whatever the default already
        # was (see this task's own rule: this harness never changes it).
        "operational_mode_after_pilot": Settings.model_fields["required_outputs_mode"].default,
    }


# ── Entry point ─────────────────────────────────────────────────────────────

async def build_agent(mode: str):
    settings = Settings(_env_file=None, required_outputs_mode=mode, cloud_policy="off")
    agent = agent_mod.JarvisAgent(settings)
    # Warm both arms identically before any trial is scored.
    await agent.chat("merhaba", detected_language="tr", transport="cli-text",
                     conversation_id=f"warmup-{mode}")
    await drain_background_tasks(agent)
    return agent


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="A", choices=["A", "B", "C"])
    parser.add_argument("--runs", type=int, default=10, help="repetitions per arm")
    parser.add_argument(
        "--label", default="pilot",
        # "ttfb-followup" added for the completion-contract TTFB follow-up
        # measurement (2026-08-07): a re-run of corpus A that is neither the
        # original pilot, its pre-registered confirmation run, nor a smoke
        # test -- see docs/eval/completion_contract_ttfb_followup_2026-08-07.md.
        # `label` is metadata only (ResultWriter + the printed report header);
        # it is never part of the output filename (default_path() uses a
        # timestamp), so adding a choice has no effect on file naming/collision.
        choices=["pilot", "confirmation", "smoke", "ttfb-followup"],
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    scenarios = [s for s in by_corpus(args.corpus) if s.mechanism == "live"]
    if not scenarios:
        print(f"corpus {args.corpus} has no live scenarios")
        return 2

    run_id = f"{timestamp()}-{uuid.uuid4().hex[:8]}"
    out = Path(args.out) if args.out else default_path("completion-contract")
    if args.out and not Path(args.out).is_absolute():
        out = (INVOCATION_CWD / args.out).resolve()

    writer = ResultWriter(path=out, metadata={
        "gate": "completion-contract",
        "gate_doc": "docs/eval/completion_contract_gate.md",
        "label": args.label, "run_id": run_id, "corpus": args.corpus,
        "runs_per_arm": args.runs, "repo_sha": head_commit(),
        "started_at": timestamp(), "scratch_home": "<redacted>",
        "scenarios": [s.id for s in scenarios],
    })

    reset_fixtures()
    agents = {arm: await build_agent(ARM_MODE[arm]) for arm in ARMS}
    print(f"run {run_id} · corpus {args.corpus} · {args.runs}/arm · out={out.name}")

    seq = 0
    for rep in range(args.runs):
        for scenario in scenarios:
            for arm in ARMS:          # alternating, so drift hits both arms alike
                seq += 1
                row = await run_trial(agents[arm], scenario, arm=arm, run_id=run_id,
                                      sequence=seq, timeout_s=args.timeout)
                writer.append(row)
                print(f"  [{rep + 1}/{args.runs}] {scenario.id:<24} {arm:<9} "
                      f"{row['failure_class']:<24} {row['elapsed_s']:.1f}s")

    writer.set_summary(report(writer.rows, args.label))
    print(f"raw: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
