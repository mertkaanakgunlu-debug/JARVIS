from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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

    # Phase 3: confirmation gate — interrupt before L3 tool calls (opt-in)
    confirmation_gate_enabled: bool = False

    # Faz 1: set by JarvisAgent.switch_model() when the user manually pins a
    # specific cloud model — bypasses the local-first router's Ollama-primary
    # default for the "fast" role (jarvis/providers/get_llm) so the pinned
    # model actually answers instead of being silently overridden. Does not
    # affect the "reasoning" role (critic/planner), same as pre-Faz-1 behavior
    # where switch_model() never touched the Pro/critic model either.
    pin_cloud_model: bool = False
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    groq_model_fallback: str = "llama-3.3-70b-versatile"
    ollama_base_url: str = "http://localhost:11434"
    local_model: str = "qwen2.5:7b-instruct"
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
    voice_chunk_ms: int = 50             # audio chunk size in ms (smaller = snappier)

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
