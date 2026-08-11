"""Provider invocation trace — records which provider/model ACTUALLY answered.

Stabilization sprint. Before this module, JarvisAgent derived its model label
from the *requested* role (`_last_turn_used_pro`), so a turn served by the
Ollama fallback was still labeled "Gemini (Vertex, reasoning)" — the live
manual-test session proved the label could be wrong on every single turn.

The fix has two halves:
1. jarvis/providers/get_llm() stamps identity metadata onto every tier via
   with_config(metadata={"jarvis_provider": ..., "jarvis_model": ...,
   "jarvis_billing": ..., "jarvis_tier_index": ...}) — declared at
   construction, never guessed from a model-name string.
2. This LlmTraceRecorder rides the per-turn callbacks list (next to
   _HudEventCallback) and turns each REAL chat-model invocation into one
   LlmCallTrace: metadata + wall-clock latency + token usage. It works for
   both ainvoke and astream paths because LangChain fires the same
   on_chat_model_start/on_llm_end callbacks either way.

Usage recording (Faz 4 of the sprint) plugs a UsageTracker into the recorder
so cost is booked exactly once per real invocation — replacing the old
result["messages"] rescan that double-counted history and the streaming
`len//4` estimate that priced local tokens as Vertex.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


@dataclass
class LlmCallTrace:
    """One real LLM invocation, as observed at the callback layer."""
    provider: str        # "ollama" | "vertex" | "aistudio" | "unknown"
    model: str
    billable: bool       # derived: billing == "paid" (kept for display/back-compat)
    billing: str         # "free" | "paid" | "unknown" — authoritative (patch 1.1)
    tier_index: int      # 0 = the chain's primary; >0 = a fallback tier answered
    node: str            # langgraph node ("agent"/"critic"/"planner") if exposed, else ""
    latency_ms: float
    input_tokens: int
    output_tokens: int
    ok: bool
    # Faz 3.2 — latency diagnostics for the thinking-on/off A/B. ttft_ms is
    # None (not 0) for non-streaming calls (/chat's ainvoke path never fires
    # on_llm_new_token) — an honest "not measured", not a fabricated value
    # equal to total latency. cold_start is a process-lifetime proxy ("first
    # completed call to this provider+model since the process started"), not
    # a guarantee — Ollama can also evict a model from VRAM after its own
    # keep_alive idles out mid-process, which this cannot see without polling
    # /api/ps. Good enough to separate "first turn is slow because of model
    # load" from "first turn is slow because of thinking" without pretending
    # to a precision the API doesn't expose (Ollama's /v1 usage has no
    # reasoning-token breakdown — verified live, 2026-07-18).
    ttft_ms: float | None = None
    cold_start: bool = False
    error_type: str = ""
    rate_limited: bool = False


# Faz 3.2 — process-lifetime "have we completed a call to this (provider,
# model) tier before?" set. Module-level (not per-recorder — a fresh
# LlmTraceRecorder is constructed every turn, cold-start is a process
# concept). Naturally resets per process, which matches the harness: the
# server under test is its own process, so the first scenario's first call
# is genuinely cold. reset_cold_start_tracking() exists for tests.
_seen_tiers: set[tuple[str, str]] = set()


def reset_cold_start_tracking() -> None:
    _seen_tiers.clear()


class LlmTraceRecorder(BaseCallbackHandler):
    """Per-turn callback handler: one LlmCallTrace per real chat-model call.

    Construct one per turn, pass in the graph config's callbacks list, then
    read .traces / .turn_summary() after the turn. Same run_id-keyed timing
    idiom as _HudEventCallback (jarvis/agent.py) — that class is left
    untouched; this one has a single responsibility: runtime truth.
    """

    def __init__(self, usage: Any = None, requested_role: str = "fast",
                 role_reason: str = "") -> None:
        self.requested_role = requested_role
        # Post-MVP Faz 2.5: WHICH rule chose that role. requested_role alone
        # says a turn ran slow; it cannot say whether the router misjudged the
        # request or the request was genuinely hard, and those need opposite
        # fixes. See jarvis/graph/role_router.py.
        self.role_reason = role_reason
        self.traces: list[LlmCallTrace] = []
        self._usage = usage  # UsageTracker or None (trace-only)
        self._pending: dict[str, dict] = {}  # run_id -> tier meta + start time

    # ── start: capture the tier identity stamped by providers.get_llm ────────
    def on_chat_model_start(self, serialized, messages, *, run_id=None,
                            metadata=None, **kwargs) -> None:
        md = metadata or {}
        billing = md.get("jarvis_billing")
        if billing is None:
            # Pre-billing-mode metadata (a stamped bool but no mode) maps
            # paid/free; a call with NO identity metadata at all is
            # "unknown" — its tokens get tracked as unpriced rather than
            # silently asserted free (patch 1.1).
            if "jarvis_billable" in md:
                billing = "paid" if md["jarvis_billable"] else "free"
            else:
                billing = "unknown"
        provider = md.get("jarvis_provider", "unknown")
        model = md.get("jarvis_model", "")
        tier_key = (provider, model)
        cold = tier_key not in _seen_tiers
        _seen_tiers.add(tier_key)
        self._pending[str(run_id)] = {
            "provider": provider,
            "model": model,
            "billing": billing,
            "tier_index": int(md.get("jarvis_tier_index", 0)),
            "node": md.get("langgraph_node", ""),
            "started": time.monotonic(),
            "cold_start": cold,
            "ttft": None,  # Faz 3.2: set by on_llm_new_token if the call streams
        }

    def on_llm_start(self, serialized, prompts, *, run_id=None,
                     metadata=None, **kwargs) -> None:
        # Fallback for callback managers that route chat models through the
        # plain-LLM hook; keyed on run_id so double delivery can't dupe.
        if str(run_id) not in self._pending:
            self.on_chat_model_start(serialized, None, run_id=run_id, metadata=metadata)

    def on_llm_new_token(self, token, *, run_id=None, **kwargs) -> None:
        # Faz 3.2 — TTFT: only fires on the astream() path (voice/streaming
        # chat); /chat's ainvoke() never calls this, so ttft stays None there.
        info = self._pending.get(str(run_id))
        if info is not None and info.get("ttft") is None:
            info["ttft"] = (time.monotonic() - info["started"]) * 1000.0

    # ── end/error: finalize the trace ─────────────────────────────────────────
    def on_llm_end(self, response, *, run_id=None, **kwargs) -> None:
        info = self._pending.pop(str(run_id), None)
        started = info.get("started") if info else None
        tokens_in, tokens_out = self._extract_usage(response)
        billing = (info or {}).get("billing", "unknown")
        trace = LlmCallTrace(
            provider=(info or {}).get("provider", "unknown"),
            model=(info or {}).get("model", ""),
            billable=billing == "paid",
            billing=billing,
            tier_index=int((info or {}).get("tier_index", 0)),
            node=(info or {}).get("node", ""),
            latency_ms=(time.monotonic() - started) * 1000.0 if started else 0.0,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            ok=True,
            ttft_ms=(info or {}).get("ttft"),
            cold_start=bool((info or {}).get("cold_start", False)),
        )
        self.traces.append(trace)
        if self._usage is not None:
            try:
                self._usage.record(
                    provider=trace.provider,
                    model=trace.model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    billing=trace.billing,
                )
            except Exception:
                pass  # accounting must never break the turn

    def on_llm_error(self, error, *, run_id=None, **kwargs) -> None:
        info = self._pending.pop(str(run_id), None)
        if info is None:
            return
        billing = info.get("billing", "unknown")
        error_type = type(error).__name__
        error_folded = str(error).casefold()
        self.traces.append(LlmCallTrace(
            provider=info.get("provider", "unknown"),
            model=info.get("model", ""),
            billable=billing == "paid",
            billing=billing,
            tier_index=int(info.get("tier_index", 0)),
            node=info.get("node", ""),
            latency_ms=(time.monotonic() - info["started"]) * 1000.0,
            input_tokens=0,
            output_tokens=0,
            ok=False,
            ttft_ms=info.get("ttft"),
            cold_start=bool(info.get("cold_start", False)),
            error_type=error_type,
            rate_limited=(
                error_type == "RateLimitError"
                or "429" in error_folded
                or "rate limit" in error_folded
                or "resource_exhausted" in error_folded
            ),
        ))

    # ── extraction ────────────────────────────────────────────────────────────
    @staticmethod
    def _extract_usage(response) -> tuple[int, int]:
        """Token counts from an LLMResult: message.usage_metadata first
        (langchain-core standard for chat models), llm_output token_usage as
        the OpenAI-style fallback. (0, 0) when a provider reports nothing —
        never a guess."""
        try:
            msg = response.generations[0][0].message
            um = getattr(msg, "usage_metadata", None)
            if um:
                return int(um.get("input_tokens", 0) or 0), int(um.get("output_tokens", 0) or 0)
        except Exception:
            pass
        try:
            tu = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
            if tu:
                return int(tu.get("prompt_tokens", 0) or 0), int(tu.get("completion_tokens", 0) or 0)
        except Exception:
            pass
        return 0, 0

    # ── per-turn rollup ────────────────────────────────────────────────────────
    def turn_summary(self) -> dict | None:
        """The turn's headline: which provider/model produced the visible text.

        Prefers, in order: the last successful "compose" call, then the last
        "agent" call, then the last successful call of any node (metadata not
        exposed). Faz 2B topology: on any turn that ran tools (agent → tools →
        accounting → compose) or was revised by the critic, the BARE composer
        authors the text the user actually sees; only no-tool conversational
        turns end critic→END with the agent's own text and no compose call.
        Round 3 fix (2026-07-18): preferring "agent" outright attributed
        tool-turn latency/TTFT/cold-start — and the model label — to the
        tool-SELECTION call instead of the call that wrote the visible answer,
        skewing the thinking-on/off A/B on exactly the tool scenarios it most
        needs to measure. critic/planner calls judge, they don't author, so
        they are never preferred. None if nothing succeeded — callers keep
        their previous label rather than lying.

        Patch 1.1 — fallback is reported at two scopes, because they answer
        different questions:
          response_fallback_used — did a fallback tier author the VISIBLE
            answer (final.tier_index > 0)? This drives the model label; a
            critic call failing over elsewhere in the turn must not relabel
            an answer the local primary actually wrote as "(fallback)".
          turn_had_any_fallback — did ANY call in the turn fail or run on a
            tier > 0? The health view: "something in this turn didn't go
            through its primary."
        `fallback_used` is kept as an alias of response_fallback_used for
        pre-split readers (Electron HUD / Flutter /status consumers).
        """
        ok_calls = [t for t in self.traces if t.ok]
        if not ok_calls:
            return None
        compose_calls = [t for t in ok_calls if t.node == "compose"]
        agent_calls = [t for t in ok_calls if t.node == "agent"]
        final = (compose_calls or agent_calls or ok_calls)[-1]
        response_fallback_used = final.tier_index > 0
        turn_had_any_fallback = (
            any(t.tier_index > 0 for t in ok_calls)
            or any(not t.ok for t in self.traces)
        )
        return {
            "requested_role": self.requested_role,
            "role_reason": self.role_reason,
            "provider": final.provider,
            "model": final.model,
            "billable": final.billable,
            "billing": final.billing,
            "fallback_used": response_fallback_used,
            "response_fallback_used": response_fallback_used,
            "turn_had_any_fallback": turn_had_any_fallback,
            "provider_error_types": [t.error_type for t in self.traces if t.error_type],
            "rate_limit_errors": sum(t.rate_limited for t in self.traces),
            "calls": len(self.traces),
            "input_tokens": sum(t.input_tokens for t in ok_calls),
            "output_tokens": sum(t.output_tokens for t in ok_calls),
            # 2026-07-19 (Faz 4 metrics): every LLM call's latency summed, so
            # a driver's e2e wall-clock minus this ≈ tool + overhead time.
            "total_llm_ms": sum(
                t.latency_ms for t in self.traces if t.latency_ms is not None
            ),
            # Faz 3.2 — the call that authored the visible response: its own
            # latency/TTFT/cold-start, so "first 'merhaba' took 40s" can be read
            # as cold-load-heavy (cold_start=True, latency high, output_tokens
            # low) vs thinking-heavy (output_tokens high) instead of guessed at.
            "latency_ms": final.latency_ms,
            "ttft_ms": final.ttft_ms,
            "cold_start": final.cold_start,
        }
