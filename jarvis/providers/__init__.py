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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from langchain_core.language_models import BaseChatModel

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)

Role = Literal["realtime", "reasoning", "fast", "local"]

_LOCAL_ROLES = ("fast", "local", "realtime")

_DEGRADED: set[str] = set()  # process-local; a feature warns at most once


def _cloud_allowed(settings: "Settings", *, pinned: bool = False) -> bool:
    """Whether ANY cloud tier may be constructed for this call right now.

    off      -> never, pinned or not -- CLOUD_POLICY=off's entire point is a
                structural guarantee, not routing advice a busy model can
                route around.
    explicit -> only when `pinned` (the caller is honoring an explicit user
                selection -- switch_model()/pin_cloud_model), never for
                automatic fallback/escalation.
    auto     -> always -- pre-sprint behavior, unchanged.
    """
    policy = getattr(settings, "cloud_policy", "auto")
    if policy == "off":
        return False
    if policy == "explicit":
        return pinned
    return True


def cloud_extractors_enabled(settings: "Settings") -> bool:
    """Whether a direct-Gemini helper (fact_extractor, session_summarizer,
    entity_extractor, finance_extractor, todo_analyzer, email_triage's LLM
    step, pdf_vision, deep_research's synthesis step) may construct a cloud
    model at all.

    These modules predate the Faz 1 provider router and still build
    ChatGoogleGenerativeAI directly (a known, tracked gap — see the
    stabilization-sprint report's "not migrated" list) rather than requesting
    a role from get_llm(). Until they're migrated onto a shared gateway, they
    get this narrow, explicit gate instead — cloud only under `auto`.
    Patch 1.1: `explicit` now also gates them off, matching its promise
    ("cloud only when the USER explicitly picked a cloud model"): a manual
    /model pin is a statement about the conversation's answering model, not
    consent for background extractors to make their own unprompted Gemini
    calls. There is no per-extractor override concept yet; if one is ever
    needed it should be its own opt-in flag, not a reinterpretation of
    `explicit`.
    """
    return getattr(settings, "cloud_policy", "auto") == "auto"


def note_degraded(feature: str) -> None:
    """Record that `feature` is running in a degraded (no-LLM) mode this
    process -- logs once per feature, not once per call, so a busy session
    doesn't spam the log with the same fact every turn."""
    if feature not in _DEGRADED:
        _DEGRADED.add(feature)
        logger.warning(
            "router: %s disabled by CLOUD_POLICY (off/explicit) -- degraded, "
            "returning its empty/no-op result instead of a silent unlogged skip",
            feature,
        )


def degraded_features() -> list[str]:
    """Every feature that has hit note_degraded() so far this process — the
    list /status.degraded surfaces to the user."""
    return sorted(_DEGRADED)


@dataclass
class _Tier:
    """One provider tier plus the identity metadata stamped onto its runs.

    Stabilization sprint: provider identity is DECLARED at construction and
    travels into every callback via with_config(metadata=...) — never guessed
    from a model-name string after the fact. jarvis/llm_trace.py's recorder
    reads these keys to report which tier actually answered.

    Patch 1.1 — `billing` replaces the old `billable: bool`: an AI Studio
    (Gemini Developer API) key can be free-tier OR paid, and which one is
    not inferable from the provider name — this repo's own key turned out to
    be a paid one with depleted prepaid credits while the code asserted $0.
    "free" = genuinely costs nothing (Ollama; AI Studio when the owner set
    ai_studio_billing_mode=free); "paid" = priced by usage.py's table
    (Vertex; AI Studio in paid mode); "unknown" = tracked as unpriced
    tokens, never silently asserted free (AI Studio's default).
    """
    model: BaseChatModel
    provider: str    # "ollama" | "vertex" | "aistudio"
    model_id: str
    billing: str     # "free" | "paid" | "unknown"


def _compose(tiers: list[_Tier], tools: list | None, role: str) -> BaseChatModel:
    """bind_tools -> with_config(metadata) -> with_fallbacks, in that order.

    Order matters twice over: RunnableWithFallbacks has no bind_tools (the
    pre-Faz-1 latent bug), and bind_tools accessed through a with_config
    RunnableBinding's __getattr__ would rebind the RAW model, silently
    dropping the identity metadata — so tools first, then the tags.
    """
    runnables = []
    for idx, t in enumerate(tiers):
        m = t.model
        if tools:
            m = m.bind_tools(tools)
        m = m.with_config(metadata={
            "jarvis_provider": t.provider,
            "jarvis_model": t.model_id,
            "jarvis_billing": t.billing,
            # Derived bool kept alongside for any reader that only wants
            # "does this cost money for certain" (jarvis_billing is the
            # authoritative three-state field).
            "jarvis_billable": t.billing == "paid",
            "jarvis_tier_index": idx,
            "jarvis_role": role,
        })
        runnables.append(m)
    primary, *rest = runnables
    return primary.with_fallbacks(rest) if rest else primary


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


def _make_local(settings: "Settings", max_output_tokens: int) -> _Tier:
    """Ollama, via its OpenAI-compatible endpoint (config.ollama_api_url)."""
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=settings.local_model,
        base_url=settings.ollama_api_url,
        api_key="ollama",  # required by the SDK, ignored by Ollama
        max_tokens=max_output_tokens,
        timeout=120,  # generous — first call after a swap may need to load the model into VRAM
        stream_usage=True,  # ask for usage in streams (Ollama /v1 include_usage) so traces get real token counts
    )
    return _Tier(model, "ollama", settings.local_model, billing="free")


def _cloud_tiers(
    settings: "Settings", max_output_tokens: int, *, pro: bool, pinned: bool = False,
) -> list[_Tier]:
    """Configured cloud tiers in priority order: [Vertex (if configured), AI Studio (if keyed)].

    pro=True picks the Vertex *primary*/reasoning model; pro=False picks the
    Vertex *fast* model. AI Studio always targets cloud_model_fallback
    (gemini-2.5-flash) for both — see module docstring for why.

    Returns [] under CLOUD_POLICY=off (always) or "explicit" UNLESS pinned=True
    (a user's manual switch_model() pin, threaded through from
    _pinned_cloud_tiers's Vertex-delegation branch below — its own default
    caller, the automatic fallback/escalation path, leaves pinned=False).
    """
    if not _cloud_allowed(settings, pinned=pinned):
        logger.info("router: cloud tier(s) suppressed by cloud_policy=%s",
                    getattr(settings, "cloud_policy", "auto"))
        return []

    from langchain_google_genai import ChatGoogleGenerativeAI

    tiers: list[_Tier] = []
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
            tiers.append(_Tier(m, "vertex", vertex_model, billing="paid"))
    if settings.gemini_api_key:
        m = _safe_construct(f"aistudio:{settings.cloud_model_fallback}", lambda: ChatGoogleGenerativeAI(
            model=settings.cloud_model_fallback,
            google_api_key=settings.gemini_api_key,
            max_output_tokens=max_output_tokens,
        ))
        if m is not None:
            tiers.append(_Tier(m, "aistudio", settings.cloud_model_fallback,
                               billing=getattr(settings, "ai_studio_billing_mode", "unknown")))
    return tiers


def _pinned_cloud_tiers(settings: "Settings", max_output_tokens: int) -> list[_Tier]:
    """Explicit switch_model() override tiers for the fast role — bypasses Ollama.

    Reproduces the pre-Faz-1 make_llm_fast() behavior (Vertex Flash with its
    own AI-Studio-Flash fallback, or a bare AI Studio effective_cloud_model) so
    a user's manual model pin is honored exactly as before. Returns [] if
    nothing can be constructed (e.g. pinned to aistudio/* with no API key) —
    caller falls back to the local-first default rather than crashing.

    Callers only reach this function because settings.pin_cloud_model is
    True — an explicit user selection — so it checks _cloud_allowed itself
    (pinned=True) before either branch, and threads pinned=True into the
    Vertex delegation so CLOUD_POLICY=explicit doesn't re-block it there.
    """
    if not _cloud_allowed(settings, pinned=True):
        logger.info("router: pinned cloud tier suppressed by cloud_policy=%s",
                    getattr(settings, "cloud_policy", "auto"))
        return []

    from langchain_google_genai import ChatGoogleGenerativeAI

    if settings.use_vertex:
        return _cloud_tiers(settings, max_output_tokens, pro=False, pinned=True)

    m = _safe_construct(f"pinned aistudio:{settings.effective_cloud_model}", lambda: ChatGoogleGenerativeAI(
        model=settings.effective_cloud_model,
        google_api_key=settings.gemini_api_key or None,
        max_output_tokens=max_output_tokens,
    ))
    if m is None:
        return []
    return [_Tier(m, "aistudio", settings.effective_cloud_model,
                  billing=getattr(settings, "ai_studio_billing_mode", "unknown"))]


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
            pinned_tiers = _pinned_cloud_tiers(settings, max_output_tokens or 4096)
            if pinned_tiers:
                logger.info("router: role=%s -> pinned cloud:%s (manual override)",
                            role, settings.effective_cloud_model)
                return _compose(pinned_tiers, tools, role)
            logger.warning("router: pin_cloud_model set but no cloud tier could be built; "
                            "falling back to local-first default")

        tiers = [_make_local(settings, max_output_tokens or 4096)]
        tiers += _cloud_tiers(settings, max_output_tokens or 4096, pro=False)
        logger.info("router: role=%s -> local:%s (cloud fallback tiers: %d)",
                    role, settings.local_model, len(tiers) - 1)
        return _compose(tiers, tools, role)

    if role == "reasoning":
        cloud = _cloud_tiers(settings, max_output_tokens or 2048, pro=True)
        tiers = cloud + [_make_local(settings, max_output_tokens or 2048)]  # local Ollama is always the last resort
        logger.info("router: role=%s -> %d cloud tier(s) then local:%s",
                    role, len(cloud), settings.local_model)
        return _compose(tiers, tools, role)

    raise ValueError(f"Unknown provider role: {role!r}")
