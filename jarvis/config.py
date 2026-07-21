import os
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --profile test (stabilization sprint) sets JARVIS_SKIP_DOTENV before
    # this class is ever imported (see jarvis/__main__.py's argv pre-scan) --
    # Settings has its OWN independent cwd-relative .env reader distinct from
    # __main__'s load_dotenv() call, so skipping only the latter would still
    # leak the real .env's secrets into a "clean" test run whenever cwd
    # happens to be the repo root. Checked at class-definition (import) time,
    # not per-instantiation -- consistent with every other test in this repo
    # using the pre-existing Settings(_env_file=None) override directly,
    # which this flag does not change or interact with.
    model_config = SettingsConfigDict(
        env_file=None if os.environ.get("JARVIS_SKIP_DOTENV") else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    tavily_api_key: str = ""
    firecrawl_api_key: str = ""   # Optional — enhances url_read/deep_web_research for JS-heavy sites
    groq_api_key: str = ""

    # Faz 8: Spotify (create app at https://developer.spotify.com — free)
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://localhost:8888/callback"

    # Faz 9: Google Calendar (OAuth 2.0 desktop credentials JSON from console.cloud.google.com)
    google_calendar_creds_file: str = "data/calendar_credentials.json"
    calendar_timezone: str = "Europe/Istanbul"  # IANA timezone for event create/update

    # Faz 9: REST API server (python -m jarvis --api)
    jarvis_api_key: str = ""    # set in .env; empty = auth disabled (local-only)
    jarvis_api_port: int = 8000

    # GPT-5.6 review remediation (2026-07-15), Faz 1 — API secure-by-default.
    # "" (unset) = auto: resolve_api_bind_host() picks 127.0.0.1 when
    # jarvis_api_key is empty, 0.0.0.0 when a key is set (LAN/Tailscale
    # access, the documented real use case). An explicit non-loopback value
    # here with an empty key is a startup fail-fast, not a silent bind — see
    # jarvis/api.py's resolve_api_bind_host().
    api_host: str = ""
    # Explicit CORS allowlist -- replaces the old unconditional "*". JSON
    # array of origins in .env, e.g. ["http://192.168.1.50:3000"] for a LAN
    # web client. The Electron desktop client's file:// origin and any
    # http(s)://localhost|127.0.0.1 dev origin are always allowed regardless
    # of this list -- see jarvis/api.py's resolve_cors_origins().
    api_cors_origins: list[str] = []

    # Faz 4: confirmation gate — interrupt before L3 tool calls. Was opt-in
    # (default False) through Phase 3/Faz 3 while the CLI/voice loops had no
    # code path to actually resume an interrupted call -- enabling it meant
    # gated turns silently hung or spoke raw JSON. Faz 4 wired all three
    # loops (CLI text, CLI/API voice, API) to handle the interrupt, so this
    # now defaults on -- see docs/SAFETY.md.
    confirmation_gate_enabled: bool = True

    # Stabilization sprint (2026-07-16): --profile test's structural
    # zero-external-side-effect guarantee. When False, make_confirmation_node
    # (jarvis/graph/nodes.py) hard-denies any tool call whose ToolSpec is
    # side_effect_type="external_write" (gmail send, calendar create/delete,
    # Drive upload/share/delete, ...) BEFORE it ever reaches the interrupt --
    # same "no prompt, no execution" shape as jarvis/kill_switch.py, applied
    # narrower (external writes only; local writes/shell/python are untouched
    # so tool-calling itself stays testable). True is the default -- normal
    # runs are unaffected; only --profile test flips this.
    external_writes_enabled: bool = True

    # Agent Runtime rev.2, Faz 1 (2026-07-20): execution-contract rollout
    # ladder (plan section B, "Uc ilke" #3) -- off | shadow |
    # enforce_read_only | enforce_reversible | enforce_all. Faz 1 only
    # wires the off vs not-off distinction: tool_result_accounting
    # (jarvis/graph/tool_accounting.py) builds one ExecutionEnvelope per
    # tool call into state["execution_envelopes"] as a pure observer when
    # this is anything but "off" -- no decision changes anywhere. The
    # enforce_* values are accepted here for forward compatibility with
    # Faz 2+ but behave identically to "shadow" until Faz 2 adds real
    # gating on this field. Default "off": no new code path runs at all
    # (Faz 1's own rollback contract).
    execution_contract_mode: Literal[
        "off", "shadow", "enforce_read_only", "enforce_reversible", "enforce_all"
    ] = "off"

    # Agent Runtime rev.2, Faz 2 (2026-07-21): how long a prepare_execution-
    # minted ExecutionRequest's HMAC approval stays valid. Unlike
    # execution_contract_mode above, approval binding itself is NOT part of
    # the off/shadow/enforce_* ladder -- it tightens the existing confirmation
    # gate (closing the TOCTOU gap between "user saw these args" and "these
    # args actually execute"), the same always-on category as the kill switch
    # and the Faz 1B duplicate-fingerprint pre-gate, not a new optional
    # compliance layer. 300s is generous for an interactive approve/deny
    # prompt while still bounding how long a stale, unanswered approval (e.g.
    # sitting in a push notification) can remain executable.
    approval_ttl_sec: int = 300

    # Faz 5: MCP client layer. Dedicated flags for the shipped Playwright
    # (browser automation) server -- flip mcp_playwright_enabled=True in .env,
    # no JSON needed. mcp_servers is the generic escape hatch for any other
    # MCP server (e.g. a future ha-mcp in Faz 6) -- a JSON object in .env,
    # same shape MultiServerMCPClient itself takes: {"name": {"command":,
    # "args":, "transport": "stdio"}} or {"name": {"transport": "http", "url":}}.
    # See .env.example for the exact Windows npx gotcha (must go through
    # `cmd /c`, not bare `npx` -- confirmed live, see jarvis/mcp_integration.py).
    mcp_playwright_enabled: bool = False
    mcp_playwright_headless: bool = True
    mcp_servers: dict[str, dict] = {}

    # Faz 7: proactive self-initiation -- monitor.py gets a real path into
    # agent.chat() (via JarvisAgent.proactive_turn()) instead of only firing a
    # static toast. Off by default, same caution as mcp_playwright_enabled: this
    # is the first place a background trigger (not a user turn) can drive the
    # full tool-calling graph, so it should be an explicit opt-in, not a silent
    # side effect of enabling --monitor. When off, monitor behaves exactly as
    # before (toast/FCM only) even if an agent reference is available.
    monitor_proactive_enabled: bool = False
    # Throttle across ALL proactive sources combined (not per-source) -- e.g. a
    # dozen unread emails surfacing at once after being offline triggers at most
    # one agent turn per this many seconds; the rest keep their normal toast but
    # skip the extra proactive judgement call. Prevents a runaway loop of LLM
    # calls, per ROADMAP.md's Faz 7 constraint.
    monitor_proactive_min_gap_sec: int = 600

    # Faz 4: hard cap on LangGraph super-step recursion per turn (BUG-recursion)
    # -- without this, a model stuck in a tool-call loop (e.g. repeatedly
    # mis-calling a tool and retrying) runs unbounded instead of failing
    # with a clear error.
    graph_recursion_limit: int = 30

    # Patch 1.2 (Faz 1B): deterministic, LLM-independent tool-call limits,
    # enforced in the confirmation node BEFORE policy evaluation and
    # independent of the capability router (defense stays up even if routing
    # misfires). Motivated by live incident A2 (2026-07-16): a local model
    # hallucinated a ~20-call batch (2 email sends included) off a one-line
    # smalltalk turn -- only the external-write gate stood between that and
    # execution. An over-limit batch is rejected WHOLE: executing "just the
    # first N" of a hallucinated batch would be guessing which part of the
    # hallucination was safe.
    max_tool_calls_per_ai_message: int = 4   # batch size cap per AIMessage
    max_tool_calls_per_turn: int = 6         # attempted calls per turn (run/failed/blocked all count)
    max_identical_tool_call: int = 1         # same tool+args fingerprint per turn
    max_tool_rounds_per_turn: int = 2        # tool batches per turn (loop stopper until Faz 2B's router)

    # Patch 1.2 (Faz 1D): conversation-history window in completed TURNS, not
    # messages — with turn compaction (agent.py) one polluted turn can no
    # longer evict the rest of the window (the A2→A3 recall failure).
    max_conversation_turns: int = 10

    # Faz 4: timeout on the agent node's own LLM call (BUG-14) -- distinct
    # from ToolSpec.timeout_seconds, which only bounds tool execution. A
    # wedged provider connection previously hung the whole turn (and, in
    # voice mode, left JARVIS silently listening forever) with no recovery.
    agent_llm_timeout_sec: float = 90.0

    # Faz 1: set by JarvisAgent.switch_model() when the user manually pins a
    # specific cloud model — bypasses the local-first router's Ollama-primary
    # default for the "fast" role (jarvis/providers/get_llm) so the pinned
    # model actually answers instead of being silently overridden. Does not
    # affect the "reasoning" role (critic/planner), same as pre-Faz-1 behavior
    # where switch_model() never touched the Pro/critic model either.
    pin_cloud_model: bool = False

    # Stabilization sprint (2026-07-16): the live manual-test session found
    # AI Studio's key exhausted (429 RESOURCE_EXHAUSTED — prepayment credits
    # depleted) and Vertex actively billing on every non-trivial turn despite
    # the local-first pivot's intent — _is_trivially_simple() still defaults
    # to cloud (sprint 2's fix, not this one). Until that's fixed, the only
    # way to guarantee zero cloud spend during testing is a structural switch.
    #   off      — no role, critic, planner, fallback, or background
    #              extractor may construct/invoke a cloud model. Default —
    #              deliberate, matches the owner's live-tested decision.
    #   explicit — cloud only via a manual pin (switch_model()/pin_cloud_model).
    #   auto     — today's pre-sprint behavior: routing/fallback decide freely.
    # Existing setups: add CLOUD_POLICY=auto to .env to restore prior behavior.
    cloud_policy: Literal["off", "explicit", "auto"] = "off"

    # Patch 1.1: what an AI Studio (Gemini Developer API) call costs. A key
    # can be free-tier OR paid (prepaid credits / pay-as-you-go) and the
    # provider name alone can't tell you which -- this repo's own key turned
    # out to be a PAID one with depleted credits while the code hardcoded
    # aistudio=free. unknown (default) = tokens tracked as "unpriced" and
    # surfaced in /status /budget, never silently asserted $0; free = $0;
    # paid = priced with the same Gemini table usage.py applies to Vertex.
    # Set AI_STUDIO_BILLING_MODE=free in .env once the key's tier is known.
    ai_studio_billing_mode: Literal["free", "paid", "unknown"] = "unknown"

    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    groq_model_fallback: str = "llama-3.3-70b-versatile"
    ollama_base_url: str = "http://localhost:11434"
    # Faz 3 A/B (2026-07-17): default flipped qwen2.5:7b-instruct → qwen3:8b.
    # Under the identical 16-scenario manual suite (temp=0, same scoped-tool
    # subsets, --profile test), qwen2.5:7b produced ZERO real tool calls —
    # every "dosyayı oluşturdum / maili gönderdim" was hallucinated text that
    # never reached the tool layer (no audit rows, no file on disk), which is
    # worse than failing: the safety gates never even engage. qwen3:8b issued
    # real, well-formed calls (file_write actually wrote, shell_run's dir
    # actually ran) so the external-write gate, shell deny-list, SSRF guard
    # and kill switch were exercised end-to-end for the first time. Fits the
    # 8 GB RTX 4070 Laptop VRAM. Slower per turn (more tokens, no thinking
    # channel), acceptable for correctness this much higher.
    local_model: str = "qwen3:8b"
    # Faz 3 (model A/B): deterministic decoding for the local tier — 0.0 is
    # both the reproducible-benchmark condition (the A/B protocol requires
    # identical decoding across candidates) and standard practice for
    # tool-calling agents; ChatOpenAI's implicit 0.7 default was neither.
    local_temperature: float = 0.0
    # Faz 3 (P1): qwen3 ships with thinking ON by default, which the live
    # verification (2026-07-18) showed dominates local latency — "2+2" spent
    # ~172 output tokens and 7s in the reasoning channel for a 1-token answer.
    # Ollama 0.32+ honors the OpenAI-standard reasoning_effort on /v1;
    # "none" disables the channel (verified: 14x faster, and a representative
    # file_write tool call stayed byte-identical, so tool-calling — the reason
    # qwen3 replaced qwen2.5 — did not regress). Applied to the routine local
    # role (fast/local/realtime) only; the reasoning-role local fallback keeps
    # full thinking. Set "" (or "default") to restore thinking for A/B runs.
    # CONFIRMED by the full oracle A/B (2026-07-18, 16 scenarios x 5 runs per
    # config, scripts/ab_run_config.ps1): accuracy IDENTICAL (60/65 both; the
    # only fail is G17b in both configs — offline extractor degradation, not
    # thinking), while thinking-off is 3-6x faster on conversational turns
    # (A1 ~0.9s vs ~4.8s LLM latency) and ~25% faster full-run wall time.
    local_reasoning_effort: str = "none"
    embed_model: str = "nomic-embed-text"
    cloud_model: str = "gemini-2.5-pro"          # primary model for all user-facing responses
    cloud_model_pro: str = "gemini-2.5-pro"     # critic/planner (same tier, kept for Vertex compat)
    cloud_model_fallback: str = "gemini-2.5-flash"  # quota exhaustion fallback
    triage_model: str = "gemini-2.5-flash"      # cheap model for bulk reading/classification pipelines
    cloud_tier: str = "flash"  # "vertex" | "aistudio" | "flash" (legacy alias)

    # Vertex AI (Google Cloud credits — ADC via `gcloud auth application-default login`)
    google_cloud_project: str = ""
    google_cloud_region: str = "europe-west1"
    vertex_model_primary: str = "gemini-2.5-pro"
    vertex_model_fast: str = "gemini-2.5-flash"

    escalation_word_threshold: int = 50

    vault_dir: Path = Path("vault")
    chroma_dir: Path = Path("data/chroma")

    user_name: str = "Sir"

    # Faz 5: Vertex AI credit budget for /budget display (set in .env as VERTEX_CREDIT_USD=350.0)
    vertex_credit_usd: float = 0.0

    # Faz 10: Proactive monitoring (python -m jarvis --monitor)
    monitor_email_interval_min: int = 5       # how often to poll Gmail (minutes)
    monitor_calendar_interval_min: int = 2    # how often to poll Calendar (minutes)
    monitor_calendar_lookahead_min: int = 15  # notify for events starting within N minutes

    # Faz 13-C: Scheduler — how often monitor checks due tasks
    monitor_schedule_interval_sec: int = 60

    # Faz 13-D: To-do reminders
    todo_reminder_hour: int = 9           # morning summary hour (24h)
    todo_reminder_lookahead_min: int = 120  # alert for todos due within N minutes
    todo_analyzer_model: str = "gemini-2.5-flash"  # model for priority analysis

    # Faz 14: Google Drive
    drive_cache_dir: Path = Path("data/drive_cache")   # downloaded Drive files
    drive_default_folder_id: str = ""                  # optional default folder ID

    # Faz 18: Geo-math sub-agent
    wolfram_app_id: str = ""                           # WolframAlpha API key (optional)
    geo_math_output_dir: Path = Path("data/geo_math_outputs")
    geo_math_use_devito: bool = True                   # use Devito for FDM if installed

    # Faz 19A-0: Mobile push + Wake-on-LAN
    firebase_credentials_path: Path = Path("data/firebase_admin_credentials.json")
    push_enabled: bool = True
    system_pc_mac: str = ""              # Ethernet MAC for WoL relay (optional)
    system_wake_timeout_sec: int = 30

    # Faz 17: GCP quota tracking
    monitor_gcp_interval_min: int = 30      # how often to check quotas + fire alerts
    gcp_alert_rpm_pct: float = 0.8          # toast when RPM usage >= this fraction
    gcp_alert_daily_spend_pct: float = 0.9  # toast when daily spend >= this fraction of budget
    gcp_alert_credit_low_tl: float = 1000.0 # toast when remaining credit falls below this TL

    # Faz 16: Finance analytics
    finance_data_dir: Path = Path("data/finance")   # charts + reports output dir
    finance_bank: str = "burgan"                     # primary bank identifier
    finance_sender_filter: str = "burgan"            # Gmail from: filter string
    finance_extractor_model: str = "gemini-2.5-flash"
    monitor_finance_interval_min: int = 30           # finance sync + budget check interval

    # Faz 15: ITU Webmail (IMAP + SMTP)
    itu_username: str = ""                   # e.g. akgunlu22@itu.edu.tr
    itu_password: str = ""                   # ITU LDAP password (or app password)
    itu_imap_host: str = "imap.itu.edu.tr"
    itu_imap_port: int = 993
    itu_smtp_host: str = "smtp.itu.edu.tr"
    itu_smtp_port: int = 587
    monitor_itu_mail_interval_min: int = 5   # poll interval for ITU inbox

    whisper_device: str = "auto"         # "auto" | "cuda" | "cpu"
    whisper_compute_type: str = "auto"   # "auto" | "int8" | "float16" | "int8_float16"

    voice_silence_duration: float = 1.5  # seconds of silence before VAD stops recording
    voice_chunk_ms: int = 50             # vestigial post-Faz-3 (Silero VAD replaced RMS-threshold
                                          # detection) — left in place, unused, rather than deleted

    # Faz 3: real-time local voice — Silero-VAD end-of-turn + Piper local TTS + barge-in.
    tts_engine: str = "piper"            # "piper" | "edge" (edge-tts stays available as a fallback)
    tts_allow_cloud_fallback: bool = True  # use edge-tts when no Piper voice is configured for a language
    vad_speech_threshold: float = 0.5      # Silero VAD speech-probability threshold for normal turn-taking
    vad_barge_in_threshold: float = 0.75   # higher bar while JARVIS is speaking — speaker-bleed mitigation
    vad_barge_in_duration_s: float = 0.4   # sustained speech required to count as a barge-in, not a blip
    audio_input_device: str = ""           # "" = system default sounddevice input
    audio_output_device: str = ""          # "" = system default sounddevice output

    @property
    def ollama_api_url(self) -> str:
        return f"{self.ollama_base_url}/v1"

    @property
    def groq_api_url(self) -> str:
        return "https://api.groq.com/openai/v1"

    @property
    def use_vertex(self) -> bool:
        return self.cloud_tier == "vertex" and bool(self.google_cloud_project)

    @property
    def effective_cloud_model(self) -> str:
        # BUG-24: cloud_tier is only ever "vertex" | "aistudio" | "flash" (see
        # switch_model() in agent.py) — a "pro" branch here was unreachable
        # dead code; cloud_model is always the right answer.
        return self.cloud_model

    @property
    def cloud_model_label(self) -> str:
        m = self.effective_cloud_model
        if "3" in m and "pro" in m:
            return "Gemini 3 Pro (cloud)"
        if "2.5" in m and "pro" in m:
            return "Gemini 2.5 Pro (cloud)"
        if "2.5" in m and "flash-lite" in m:
            return "Gemini 2.5 Flash-Lite (cloud)"
        if "2.5" in m and "flash" in m:
            return "Gemini 2.5 Flash (cloud)"
        if "2.0" in m and "flash" in m:
            return "Gemini 2.0 Flash (cloud)"
        if "flash" in m:
            return "Gemini Flash (cloud)"
        if "pro" in m:
            return "Gemini Pro (cloud)"
        return f"{m} (cloud)"


COMPLEX_KEYWORDS = {
    "design", "architect", "implement", "refactor", "analyze", "analyse",
    "research", "compare", "evaluate", "explain in depth", "step by step",
    "algorithm", "optimize", "debug", "essay", "report", "summarize",
    "summarise", "plan", "write code", "create a script", "build a",
    "develop a", "how does", "why does", "what is the difference",
    "generate a", "create a function", "write a program",
    # Orchestration-specific additions
    "solve", "calculate", "derive", "prove", "presentation", "slides",
    "homework", "latex", "cite", "references", "bibliography",
}

FILE_EXTENSIONS = {
    ".pdf", ".docx", ".tex", ".md", ".txt", ".xlsx",
    ".pptx", ".csv", ".json", ".py",
}


def should_orchestrate(message: str, threshold: int = 50) -> bool:
    """Route to Gemini orchestrator: any complex task, file reference, or /think."""
    stripped = message.strip()
    if stripped.startswith("/think"):
        return True
    lower = stripped.lower()
    if len(stripped.split()) > threshold:
        return True
    if any(ext in lower for ext in FILE_EXTENSIONS):
        return True
    return any(kw in lower for kw in COMPLEX_KEYWORDS)


# Back-compat alias used in Iteration 1 code
should_escalate = should_orchestrate

# Whisper language code → display name (used for per-turn language injection)
LANG_NAMES: dict[str, str] = {
    "tr": "Turkish (Türkçe)",
    "de": "German (Deutsch)",
    "fr": "French (Français)",
    "es": "Spanish (Español)",
    "it": "Italian (Italiano)",
    "pt": "Portuguese (Português)",
    "ru": "Russian (Русский)",
    "zh": "Chinese (中文)",
    "ja": "Japanese (日本語)",
    "ar": "Arabic (العربية)",
    "ko": "Korean (한국어)",
    "nl": "Dutch (Nederlands)",
    "pl": "Polish (Polski)",
    "sv": "Swedish (Svenska)",
}
