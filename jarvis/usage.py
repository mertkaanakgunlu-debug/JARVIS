"""Session-level token and cost tracker (Faz 5).

Accumulates tokens per model, estimates Vertex AI cost, and persists a running
total across sessions in data/usage.json so /budget shows lifetime spend.

Pricing: Vertex AI Gemini, standard context window (<=200K tokens), May 2026.
Flash input $0.075/1M, output $0.30/1M.
Pro input $1.25/1M, output $10.00/1M.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

# Pricing per token (Vertex AI, <=200K context, May 2026)
_PRICING: dict[str, dict[str, float]] = {
    "pro":   {"in": 1.25e-6,  "out": 10.00e-6},
    "flash": {"in": 0.075e-6, "out": 0.30e-6},
}


def _model_tier(model_id: str) -> str:
    """Return 'pro' or 'flash' pricing tier for a given model id string."""
    return "pro" if "pro" in model_id.lower() else "flash"


def _empty_totals() -> dict:
    return {
        "tokens_in": 0,
        "tokens_out": 0,
        "flash_turns": 0,
        "pro_turns": 0,
        "cost_usd": 0.0,
        "by_provider": {},  # provider -> {"calls", "tokens_in", "tokens_out"}
    }


def _bump_provider(d: dict, provider: str, tokens_in: int, tokens_out: int) -> None:
    entry = d.setdefault("by_provider", {}).setdefault(
        provider, {"calls": 0, "tokens_in": 0, "tokens_out": 0}
    )
    entry["calls"] += 1
    entry["tokens_in"] += tokens_in
    entry["tokens_out"] += tokens_out


class UsageTracker:
    """Tracks token usage and Vertex AI cost across turns and sessions.

    Session counters reset on each JarvisAgent instantiation.
    All-time totals are persisted to ``persist_path`` (data/usage.json).
    """

    def __init__(self, persist_path: Path) -> None:
        self._path = persist_path
        self._lock = threading.Lock()
        self._session: dict = _empty_totals()
        self._total = self._load_total()

    # ── persistence ───────────────────────────────────────────────────────────

    def _load_total(self) -> dict:
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                loaded.setdefault("by_provider", {})
                return loaded
            except Exception:
                pass
        return _empty_totals()

    def _save_total(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(self._total)
        now_iso = datetime.now(timezone.utc).isoformat()
        # BUG-18: last_updated refreshes every save (effectively "now"), so it's
        # useless as a tracking-start anchor for gcp_quota.py's daily-rate
        # forecast. first_seen is set once (setdefault) and round-trips through
        # every subsequent load/save, giving a genuine elapsed-days baseline.
        data.setdefault("first_seen", now_iso)
        data["last_updated"] = now_iso
        try:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass  # non-fatal

    # ── public API ─────────────────────────────────────────────────────────────

    def record(
        self,
        *,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        billable: bool,
    ) -> None:
        """Record one real LLM invocation and update persistent totals.

        Stabilization sprint: cost is now driven by the CALLER-DECLARED
        `billable` flag (stamped at tier construction in
        jarvis/providers/__init__.py — vertex=True, ollama/aistudio=False),
        not guessed from whether "pro" appears in the model name. Before this,
        a local Ollama turn's model id ("qwen2.5:7b-instruct", no "pro"
        substring) was priced as Gemini Flash -- every local-only turn showed
        a nonzero cost. flash_turns/pro_turns, which gcp_quota.py uses
        specifically for Vertex RPD-quota tracking, now count billable Vertex
        calls only (previously any Gemini-tier-looking model id, including
        Ollama's) -- by_provider below gives total call/token visibility
        across all providers without polluting that Vertex-specific counter.

        BUG-usage (pre-existing, unchanged): self._total is re-read fresh from
        disk right before merging this call's delta in, narrowing (not
        eliminating) the cross-process last-write-wins race two long-lived
        processes (CLI + `--api`) would otherwise hit -- see kill_switch.py/
        audit_log.py for the same accepted tradeoff elsewhere in this repo.
        """
        if not (tokens_in or tokens_out):
            return
        cost = 0.0
        turn_key = None
        if billable:
            tier = _model_tier(model)
            rates = _PRICING[tier]
            cost = tokens_in * rates["in"] + tokens_out * rates["out"]
            turn_key = f"{tier}_turns"

        # Session counters are process-local by design (reset per JarvisAgent
        # instantiation) -- no cross-process sharing, safe to mutate directly.
        self._session["tokens_in"] += tokens_in
        self._session["tokens_out"] += tokens_out
        self._session["cost_usd"] += cost
        if turn_key:
            self._session[turn_key] = self._session.get(turn_key, 0) + 1
        _bump_provider(self._session, provider, tokens_in, tokens_out)

        with self._lock:
            self._total = self._load_total()
            self._total["tokens_in"] += tokens_in
            self._total["tokens_out"] += tokens_out
            self._total["cost_usd"] += cost
            if turn_key:
                self._total[turn_key] = self._total.get(turn_key, 0) + 1
            _bump_provider(self._total, provider, tokens_in, tokens_out)
            self._save_total()

    @property
    def session_cost(self) -> float:
        return self._session["cost_usd"]

    @property
    def total_cost(self) -> float:
        return self._total["cost_usd"]

    def report(self, vertex_credit_usd: float = 0.0) -> str:
        """Return a Rich-formatted multi-line usage report."""
        s = self._session
        t = self._total

        def _fmt(label: str, d: dict) -> list[str]:
            return [
                f"  Tokens  in : [cyan]{d['tokens_in']:>10,}[/cyan]",
                f"  Tokens out : [cyan]{d['tokens_out']:>10,}[/cyan]",
                f"  Flash turns: [dim]{d.get('flash_turns',0):>3}[/dim]   Pro turns: [dim]{d.get('pro_turns',0):>3}[/dim]",
                f"  Est. cost  : [yellow]${d['cost_usd']:.5f}[/yellow] [dim](~${d['cost_usd']*100:.3f} cents)[/dim]",
            ]

        def _fmt_providers(d: dict) -> list[str]:
            by_provider = d.get("by_provider") or {}
            if not by_provider:
                return []
            parts = [
                f"{p}: {e['calls']} call(s), {e['tokens_in']}/{e['tokens_out']} tok"
                for p, e in sorted(by_provider.items())
            ]
            return [f"  By provider: [dim]{'  ·  '.join(parts)}[/dim]"]

        lines = [
            "[bold gold3]-- Session --[/bold gold3]",
            *_fmt("session", s),
            *_fmt_providers(s),
            "",
            "[bold gold3]-- All-time --[/bold gold3]",
            *_fmt("total", t),
            *_fmt_providers(t),
        ]

        if vertex_credit_usd > 0:
            remaining = max(0.0, vertex_credit_usd - t["cost_usd"])
            pct_used = min(100.0, t["cost_usd"] / vertex_credit_usd * 100)
            color = "green" if pct_used < 50 else ("yellow" if pct_used < 80 else "red")
            lines += [
                "",
                "[bold gold3]-- Vertex Credits --[/bold gold3]",
                f"  Budget     : [dim]${vertex_credit_usd:.2f}[/dim]",
                f"  Used       : [{color}]${t['cost_usd']:.5f}[/{color}] [dim]({pct_used:.2f}%)[/dim]",
                f"  Remaining  : [bold {color}]~${remaining:.2f}[/bold {color}]",
            ]

        return "\n".join(lines)
