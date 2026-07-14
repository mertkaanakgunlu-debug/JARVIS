"""Model provider router — Faz 1 (local-first beyin + model router).

Resolves a *role* to a concrete LangChain chat model, so graph.py and agent.py
no longer construct `ChatGoogleGenerativeAI` directly. Roles:

  fast / local / realtime — the routine executor tier. Ollama (settings.local_model,
      e.g. qwen2.5:7b-instruct) is PRIMARY, via its OpenAI-compatible endpoint
      (settings.ollama_api_url). Falls back to whatever cloud tiers are actually
      configured, in order [Vertex Flash, AI Studio Flash], only on a real
      invocation error (connection refused, model missing, etc.) — a reactive
      `.with_fallbacks()` safety net, not the complexity-based routing below.
      `realtime` and `local` are aliases of `fast` today; Faz 3 (local voice)
      may give `realtime` its own latency-tuned construction later, and `local`
      exists as an explicit "never escalate" role for future callers that need
      to force on-device processing.

  reasoning — the escalation tier for hard reasoning (critic/planner/complex
      queries): whatever cloud tiers are configured, in order [Vertex Pro,
      AI Studio Gemini Flash], with local Ollama appended as the final
      fallback. AI Studio targets cloud_model_fallback (gemini-2.5-flash,
      1500 RPD free) and NOT cloud_model_pro (gemini-2.5-pro, 25 RPD free) —
      the local-first pivot assumes Vertex credits are exhausted, so the
      default escalation path must live within the free tier. See MEMORY.md's
      "Direction: LOCAL-FIRST pivot".

Every cloud tier is CONSTRUCTED DEFENSIVELY: a tier that can't even be built
(e.g. AI Studio with no GEMINI_API_KEY set) is logged and dropped from the
chain rather than raising out of get_llm() / crashing build_graph() at
startup — a pure-local setup (Ollama only, no cloud credentials at all) is a
legitimate, intended configuration for local-first, and building the graph
must succeed even then. This was found live during this phase's own rollout:
a fresh environment with no .env at all raised a pydantic ValidationError
from ChatGoogleGenerativeAI's constructor (missing API key) *before* Ollama
was ever tried — i.e. every role failed at build time, not at the point
where the missing tier was actually needed.

Which role to REQUEST for a given turn is a caller-side decision (e.g.
agent.py's `_is_trivially_simple()` already implements the complexity-threshold
capacity routing described in the roadmap — "route hard tasks to Gemini Flash
free tier, not just on a 429"). This module only resolves an already-chosen
role name to a working model; it does not decide which role a query needs.

Tool binding must happen BEFORE `.with_fallbacks()` — `RunnableWithFallbacks`
has no `bind_tools` (only `BaseChatModel`/`RunnableBinding` do), so binding
after wrapping raises AttributeError. Pass `tools=` to `get_llm()` so it binds
in the right order; this was a latent bug in the pre-Faz-1 `make_llm_fast()`
(`primary.with_fallbacks([...]).bind_tools(tools)` in graph.py), unreachable
only because `use_vertex` has been False in recent runs (missing Vertex ADC).

Also worth knowing (found live, same rollout): LangChain's `RunnableWithFallbacks`
re-raises the FIRST tier's exception if every tier fails, not the last — so if
Ollama is down, the surfaced error will be Ollama's connection error even when
a later cloud tier failed for a different reason (e.g. quota). Check the
router's log line (below) to see every tier that was actually attempted.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from langchain_core.language_models import BaseChatModel

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)

Role = Literal["realtime", "reasoning", "fast", "local"]

_LOCAL_ROLES = ("fast", "local", "realtime")


def _safe_construct(label: str, factory):
    """Build a cloud tier, or return None (logged) if it can't even construct.

    Distinct from `.with_fallbacks()`'s own error handling — that only covers
    INVOKE-time failures. A missing API key fails at CONSTRUCTION time (a
    pydantic validation error), before the fallback chain ever gets to try
    invoking anything, so it needs its own guard.
    """
    try:
        return factory()
    except Exception as exc:
        logger.info("router: %s unavailable, skipping (%s)", label, exc)
        return None


def _make_local(settings: "Settings", max_output_tokens: int) -> BaseChatModel:
    """Ollama, via its OpenAI-compatible endpoint (config.ollama_api_url)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.local_model,
        base_url=settings.ollama_api_url,
        api_key="ollama",  # required by the SDK, ignored by Ollama
        max_tokens=max_output_tokens,
        timeout=120,  # generous — first call after a swap may need to load the model into VRAM
    )


def _cloud_tiers(settings: "Settings", max_output_tokens: int, *, pro: bool) -> list[BaseChatModel]:
    """Configured cloud tiers in priority order: [Vertex (if configured), AI Studio (if keyed)].

    pro=True picks the Vertex *primary*/reasoning model; pro=False picks the
    Vertex *fast* model. AI Studio always targets cloud_model_fallback
    (gemini-2.5-flash) for both — see module docstring for why.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    tiers: list[BaseChatModel] = []
    if settings.use_vertex:
        vertex_model = settings.vertex_model_primary if pro else settings.vertex_model_fast
        m = _safe_construct(f"vertex:{vertex_model}", lambda: ChatGoogleGenerativeAI(
            model=vertex_model,
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_region,
            max_output_tokens=max_output_tokens,
        ))
        if m is not None:
            tiers.append(m)
    if settings.gemini_api_key:
        m = _safe_construct(f"aistudio:{settings.cloud_model_fallback}", lambda: ChatGoogleGenerativeAI(
            model=settings.cloud_model_fallback,
            google_api_key=settings.gemini_api_key,
            max_output_tokens=max_output_tokens,
        ))
        if m is not None:
            tiers.append(m)
    return tiers


def _make_pinned_cloud(settings: "Settings", max_output_tokens: int) -> BaseChatModel | None:
    """Explicit switch_model() override for the fast role — bypasses Ollama entirely.

    Reproduces the pre-Faz-1 make_llm_fast() behavior (Vertex Flash with its
    own AI-Studio-Flash fallback, or a bare AI Studio effective_cloud_model) so
    a user's manual model pin is honored exactly as before. Returns None if
    even that can't be constructed (e.g. pinned to aistudio/* with no API key)
    — caller falls back to the local-first default rather than crashing.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    if settings.use_vertex:
        tiers = _cloud_tiers(settings, max_output_tokens, pro=False)
        if not tiers:
            return None
        primary, *rest = tiers
        return primary.with_fallbacks(rest) if rest else primary

    return _safe_construct(f"pinned aistudio:{settings.effective_cloud_model}", lambda: ChatGoogleGenerativeAI(
        model=settings.effective_cloud_model,
        google_api_key=settings.gemini_api_key or None,
        max_output_tokens=max_output_tokens,
    ))


def get_llm(
    role: Role,
    settings: "Settings",
    *,
    tools: list | None = None,
    max_output_tokens: int | None = None,
) -> BaseChatModel:
    """Resolve `role` to a chat model, tool-bound first if `tools` is given.

    fast/local/realtime → Ollama primary, falling back through whichever cloud
    tiers are actually configured (invocation-error triggered only).
    reasoning → configured cloud tiers first, local Ollama as the final
    fallback — local-first means NOTHING is cloud-mandatory, including escalation.
    """
    if role in _LOCAL_ROLES:
        if settings.pin_cloud_model:
            pinned = _make_pinned_cloud(settings, max_output_tokens or 4096)
            if pinned is not None:
                if tools:
                    pinned = pinned.bind_tools(tools)
                logger.info("router: role=%s -> pinned cloud:%s (manual override)",
                            role, settings.effective_cloud_model)
                return pinned
            logger.warning("router: pin_cloud_model set but no cloud tier could be built; "
                            "falling back to local-first default")

        primary = _make_local(settings, max_output_tokens or 4096)
        fallback_chain = _cloud_tiers(settings, max_output_tokens or 4096, pro=False)
        if tools:
            primary = primary.bind_tools(tools)
            fallback_chain = [m.bind_tools(tools) for m in fallback_chain]
        logger.info("router: role=%s -> local:%s (cloud fallback tiers: %d)",
                    role, settings.local_model, len(fallback_chain))
        return primary.with_fallbacks(fallback_chain) if fallback_chain else primary

    if role == "reasoning":
        cloud_tiers = _cloud_tiers(settings, max_output_tokens or 2048, pro=True)
        local = _make_local(settings, max_output_tokens or 2048)
        chain = cloud_tiers + [local]  # local Ollama is always the last resort
        primary, *rest = chain
        if tools:
            primary = primary.bind_tools(tools)
            rest = [m.bind_tools(tools) for m in rest]
        logger.info("router: role=%s -> %d cloud tier(s) then local:%s",
                    role, len(cloud_tiers), settings.local_model)
        return primary.with_fallbacks(rest) if rest else primary

    raise ValueError(f"Unknown provider role: {role!r}")
