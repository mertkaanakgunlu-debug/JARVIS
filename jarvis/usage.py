"""Session-level token and cost tracker (Faz 5).

Accumulates tokens per model, estimates Vertex AI cost, and persists a running
total across sessions in data/usage.json so /budget shows lifetime spend.

Pricing: Vertex AI Gemini, standard context window (<=200K tokens), May 2026.
Flash input $0.075/1M, output $0.30/1M.
Pro input $1.25/1M, output $10.00/1M.
"""

from __future__ import annotations

import json
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


class UsageTracker:
    """Tracks token usage and Vertex AI cost across turns and sessions.

    Session counters reset on each JarvisAgent instantiation.
    All-time totals are persisted to ``persist_path`` (data/usage.json).
    """

    def __init__(self, persist_path: Path) -> None:
        self._path = persist_path
        self._session: dict = {
            "tokens_in": 0,
            "tokens_out": 0,
            "flash_turns": 0,
            "pro_turns": 0,
            "cost_usd": 0.0,
        }
        self._total = self._load_total()

    # ── persistence ───────────────────────────────────────────────────────────

    def _load_total(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "tokens_in": 0,
            "tokens_out": 0,
            "flash_turns": 0,
            "pro_turns": 0,
            "cost_usd": 0.0,
        }

    def _save_total(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(self._total)
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        try:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass  # non-fatal

    # ── public API ─────────────────────────────────────────────────────────────

    def record(self, model_id: str, tokens_in: int, tokens_out: int) -> None:
        """Record one turn's token usage and update persistent totals."""
        if not (tokens_in or tokens_out):
            return
        tier = _model_tier(model_id)
        rates = _PRICING[tier]
        cost = tokens_in * rates["in"] + tokens_out * rates["out"]

        turn_key = f"{tier}_turns"
        for bucket in (self._session, self._total):
            bucket["tokens_in"] += tokens_in
            bucket["tokens_out"] += tokens_out
            bucket["cost_usd"] += cost
            bucket[turn_key] = bucket.get(turn_key, 0) + 1

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

        lines = [
            "[bold gold3]-- Session --[/bold gold3]",
            *_fmt("session", s),
            "",
            "[bold gold3]-- All-time --[/bold gold3]",
            *_fmt("total", t),
        ]

        if vertex_credit_usd > 0:
            remaining = max(0.0, vertex_credit_usd - t["cost_usd"])
            pct_used = min(100.0, t["cost_usd"] / vertex_credit_usd * 100)
            color = "green" if pct_used < 50 else ("yellow" if pct_used < 80 else "red")
            lines += [
                "",
                f"[bold gold3]-- Vertex Credits --[/bold gold3]",
                f"  Budget     : [dim]${vertex_credit_usd:.2f}[/dim]",
                f"  Used       : [{color}]${t['cost_usd']:.5f}[/{color}] [dim]({pct_used:.2f}%)[/dim]",
                f"  Remaining  : [bold {color}]~${remaining:.2f}[/bold {color}]",
            ]

        return "\n".join(lines)
