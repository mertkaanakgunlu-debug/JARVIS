from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    tavily_api_key: str = ""
    groq_api_key: str = ""
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    groq_model_fallback: str = "llama-3.3-70b-versatile"
    ollama_base_url: str = "http://localhost:11434"
    local_model: str = "qwen2.5:7b-instruct"
    embed_model: str = "nomic-embed-text"
    cloud_model: str = "gemini-2.5-flash-lite"   # 1000 RPD free tier; stronger models exhausted in testing
    cloud_model_pro: str = "gemini-2.5-pro"     # pro tier default; override via CLOUD_MODEL_PRO
    cloud_model_fallback: str = "gemini-2.5-flash-lite"  # 1000 RPD on free tier (vs 20 for flash)
    cloud_tier: str = "flash"  # "flash" (free) or "pro" (Gemini Pro subscription)

    escalation_word_threshold: int = 50

    vault_dir: Path = Path("vault")
    chroma_dir: Path = Path("data/chroma")

    user_name: str = "Sir"

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
    def effective_cloud_model(self) -> str:
        if self.cloud_tier == "pro":
            return self.cloud_model_pro
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
