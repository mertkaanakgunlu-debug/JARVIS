"""usage.py — provider-aware token/cost tracking (stabilization sprint, F.3/F.4;
billing three-state + unpriced tracking from patch 1.1).

record() takes the CALLER-DECLARED (provider, billing) pair instead of
guessing pricing tier from a "pro" substring in the model name — that old
guess is exactly why a local Ollama turn ("qwen2.5:7b-instruct", no "pro"
substring) used to get priced as Gemini Flash. billing is stamped once at
provider-tier construction time (jarvis/providers/__init__.py: vertex="paid",
ollama="free", aistudio=Settings.ai_studio_billing_mode, default "unknown")
and travels in via jarvis/llm_trace.py's recorder, so usage.py itself never
has to know which providers cost money.

Patch 1.1 additions covered here: billing="unknown" tokens accumulate as
unpriced (never silently asserted $0); billing="paid" on a NON-Vertex
provider is priced but does not pollute flash_turns/pro_turns (those two
counters feed gcp_quota.py's Vertex RPD tracking specifically); zero-token
calls still count in by_provider (with an unreported_calls marker) instead of
vanishing from the ledger.

Also covers the Faz 8 cross-process clobber fix (unchanged) and the
by_provider breakdown.
"""
from __future__ import annotations

import json

from jarvis.usage import UsageTracker


def _record(tracker, *, provider="vertex", model="gemini-2.5-flash",
            tokens_in=100, tokens_out=50, billing="paid"):
    tracker.record(provider=provider, model=model, tokens_in=tokens_in,
                    tokens_out=tokens_out, billing=billing)


def test_record_updates_session_and_total(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker)

    assert tracker.session_cost > 0
    assert tracker.total_cost == tracker.session_cost  # first-ever record


def test_zero_token_call_still_counts_in_by_provider(tmp_path):
    """Patch 1.1: a provider that reports no usage metadata must not make the
    call vanish -- the ledger records it (calls +1, unreported_calls marker),
    cost stays untouched. Previously record() returned before writing
    anything, so by_provider.calls under-counted real invocations."""
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="ollama", model="q", tokens_in=0, tokens_out=0,
            billing="free")

    assert tracker.session_cost == 0.0
    entry = tracker._session["by_provider"]["ollama"]
    assert entry["calls"] == 1
    assert entry["unreported_calls"] == 1
    on_disk = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert on_disk["by_provider"]["ollama"]["calls"] == 1


def test_reported_call_has_no_unreported_marker(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="ollama", model="q", tokens_in=10, tokens_out=5,
            billing="free")
    assert "unreported_calls" not in tracker._session["by_provider"]["ollama"]


def test_pro_tier_costs_more_than_flash_for_same_tokens(tmp_path):
    flash = UsageTracker(tmp_path / "flash.json")
    _record(flash, model="gemini-2.5-flash", tokens_in=1000, tokens_out=1000)
    pro = UsageTracker(tmp_path / "pro.json")
    _record(pro, model="gemini-2.5-pro", tokens_in=1000, tokens_out=1000)
    assert pro.session_cost > flash.session_cost


def test_two_concurrent_trackers_do_not_clobber_each_other(tmp_path):
    """BUG-usage regression: simulates two processes (CLI + `--api`) recording
    against the same usage.json around the same time."""
    usage_path = tmp_path / "usage.json"
    process_a = UsageTracker(usage_path)
    process_b = UsageTracker(usage_path)  # opened "at the same time" as A

    _record(process_a, tokens_in=1000, tokens_out=200)
    _record(process_b, tokens_in=500, tokens_out=100)  # B never saw A's in-process update

    on_disk = json.loads(usage_path.read_text(encoding="utf-8"))
    assert on_disk["tokens_in"] == 1500, "B's write clobbered A's contribution"
    assert on_disk["tokens_out"] == 300
    assert on_disk["flash_turns"] == 2


def test_a_third_write_builds_on_the_other_processes_write(tmp_path):
    usage_path = tmp_path / "usage.json"
    process_a = UsageTracker(usage_path)
    process_b = UsageTracker(usage_path)

    _record(process_a, model="gemini-2.5-flash", tokens_in=1000, tokens_out=0)
    _record(process_b, model="gemini-2.5-flash", tokens_in=500, tokens_out=0)
    _record(process_a, model="gemini-2.5-pro", tokens_in=10, tokens_out=0)  # A again, stale self._total

    on_disk = json.loads(usage_path.read_text(encoding="utf-8"))
    assert on_disk["tokens_in"] == 1510
    assert on_disk["flash_turns"] == 2
    assert on_disk["pro_turns"] == 1


def test_session_counters_stay_process_local(tmp_path):
    """Unlike the all-time total, session_cost must NOT pick up another
    tracker's activity -- it resets per JarvisAgent instantiation by design."""
    usage_path = tmp_path / "usage.json"
    process_a = UsageTracker(usage_path)
    process_b = UsageTracker(usage_path)

    _record(process_a, tokens_in=1000, tokens_out=0)
    _record(process_b, tokens_in=500, tokens_out=0)

    assert process_a.session_cost != process_b.session_cost
    assert process_a.session_cost > process_b.session_cost  # A recorded 2x B's tokens


def test_report_includes_both_session_and_alltime(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker)
    text = tracker.report()
    assert "Session" in text
    assert "All-time" in text


# ── F3: a local (free) turn costs exactly 0.0 ─────────────────────────────────

def test_local_ollama_turn_costs_exactly_zero(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="ollama", model="qwen2.5:7b-instruct",
            tokens_in=5000, tokens_out=3000, billing="free")
    assert tracker.session_cost == 0.0
    assert tracker.total_cost == 0.0
    assert tracker.session_unpriced_tokens == 0, "free is NOT unknown -- $0 is asserted, not dodged"


def test_aistudio_declared_free_costs_zero_with_no_unpriced(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="aistudio", model="gemini-2.5-flash",
            tokens_in=2000, tokens_out=1000, billing="free")
    assert tracker.session_cost == 0.0
    assert tracker.session_unpriced_tokens == 0


def test_non_paid_call_does_not_increment_flash_or_pro_turns(tmp_path):
    """flash_turns/pro_turns feed gcp_quota.py's Vertex RPD-quota tracking
    specifically -- a free-tier or local call must not pollute that count."""
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="ollama", model="qwen2.5:7b-instruct", billing="free")
    assert tracker._session["flash_turns"] == 0
    assert tracker._session["pro_turns"] == 0


# ── F4: only the paid call is priced ──────────────────────────────────────────

def test_only_paid_vertex_call_is_priced_others_are_free(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="ollama", model="qwen2.5:7b-instruct",
            tokens_in=1000, tokens_out=1000, billing="free")
    cost_after_local = tracker.session_cost
    assert cost_after_local == 0.0

    _record(tracker, provider="vertex", model="gemini-2.5-pro",
            tokens_in=1000, tokens_out=1000, billing="paid")
    assert tracker.session_cost > 0.0, "the paid Vertex call must be priced"

    cost_after_vertex = tracker.session_cost
    _record(tracker, provider="aistudio", model="gemini-2.5-flash",
            tokens_in=1000, tokens_out=1000, billing="free")
    assert tracker.session_cost == cost_after_vertex, "a declared-free aistudio call must add zero cost"


# ── Patch 1.1: billing="unknown" and paid AI Studio ───────────────────────────

def test_unknown_billing_accumulates_unpriced_not_cost(tmp_path):
    """The AI Studio default (ai_studio_billing_mode=unknown): the key might
    be free OR paid, so its tokens are neither priced nor asserted $0 -- they
    show up as unpriced, session AND persisted total."""
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="aistudio", model="gemini-2.5-flash",
            tokens_in=10_000, tokens_out=2_450, billing="unknown")

    assert tracker.session_cost == 0.0, "unknown must not be priced"
    assert tracker.session_unpriced_tokens == 12_450
    on_disk = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert on_disk["unpriced_tokens_in"] == 10_000
    assert on_disk["unpriced_tokens_out"] == 2_450


def test_unknown_billing_shows_up_in_report(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="aistudio", model="gemini-2.5-flash",
            tokens_in=100, tokens_out=50, billing="unknown")
    assert "Unpriced" in tracker.report()


def test_paid_aistudio_is_priced_but_does_not_bump_vertex_turn_counters(tmp_path):
    """ai_studio_billing_mode=paid: real money, so it must be priced -- but
    flash_turns/pro_turns stay Vertex-only (gcp_quota.py's RPD tracking would
    otherwise count AI Studio requests against the Vertex daily quota)."""
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="aistudio", model="gemini-2.5-flash",
            tokens_in=1000, tokens_out=1000, billing="paid")

    assert tracker.session_cost > 0.0
    assert tracker._session["flash_turns"] == 0
    assert tracker._session["pro_turns"] == 0
    assert tracker.session_unpriced_tokens == 0


# ── by_provider breakdown ──────────────────────────────────────────────────────

def test_by_provider_breakdown_tracks_calls_and_tokens_per_provider(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    _record(tracker, provider="ollama", model="qwen2.5:7b-instruct",
            tokens_in=100, tokens_out=50, billing="free")
    _record(tracker, provider="ollama", model="qwen2.5:7b-instruct",
            tokens_in=200, tokens_out=75, billing="free")
    _record(tracker, provider="vertex", model="gemini-2.5-pro",
            tokens_in=10, tokens_out=5, billing="paid")

    bp = tracker._session["by_provider"]
    assert bp["ollama"] == {"calls": 2, "tokens_in": 300, "tokens_out": 125}
    assert bp["vertex"] == {"calls": 1, "tokens_in": 10, "tokens_out": 5}
    assert "aistudio" not in bp


def test_by_provider_persists_and_merges_across_processes(tmp_path):
    usage_path = tmp_path / "usage.json"
    process_a = UsageTracker(usage_path)
    process_b = UsageTracker(usage_path)

    _record(process_a, provider="ollama", model="q", tokens_in=100, tokens_out=0, billing="free")
    _record(process_b, provider="vertex", model="gemini-2.5-flash", tokens_in=50, tokens_out=0, billing="paid")

    on_disk = json.loads(usage_path.read_text(encoding="utf-8"))
    assert on_disk["by_provider"]["ollama"]["calls"] == 1
    assert on_disk["by_provider"]["vertex"]["calls"] == 1


def test_loading_pre_sprint_usage_json_without_new_keys_does_not_crash(tmp_path):
    """Backward compatibility: an on-disk file from before this sprint has no
    "by_provider" (or patch 1.1's "unpriced_tokens_*") keys at all."""
    usage_path = tmp_path / "usage.json"
    usage_path.write_text(json.dumps({
        "tokens_in": 500, "tokens_out": 200,
        "flash_turns": 3, "pro_turns": 1, "cost_usd": 0.001,
    }), encoding="utf-8")

    tracker = UsageTracker(usage_path)
    assert tracker.total_cost == 0.001
    _record(tracker, provider="ollama", model="q", tokens_in=10, tokens_out=5, billing="free")
    _record(tracker, provider="aistudio", model="g", tokens_in=7, tokens_out=3, billing="unknown")
    on_disk = json.loads(usage_path.read_text(encoding="utf-8"))
    assert on_disk["by_provider"]["ollama"]["calls"] == 1
    assert on_disk["tokens_in"] == 517
    assert on_disk["unpriced_tokens_in"] == 7
