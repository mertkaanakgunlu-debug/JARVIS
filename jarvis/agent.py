"""Pydantic-AI orchestrator with hybrid routing and sub-agent delegation."""

from __future__ import annotations

import dataclasses
import re
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.gemini import GeminiModel
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.google_gla import GoogleGLAProvider
from pydantic_ai.providers.openai import OpenAIProvider

from jarvis.config import Settings, LANG_NAMES
from jarvis.memory import Memory
from jarvis.utils import run_with_retry, is_daily_quota_error, build_cloud_model
from jarvis.tools import files as file_tools
from jarvis.tools import shell as shell_tools
from jarvis.tools.notes import append_note
from jarvis.tools.pdf import read_pdf
from jarvis.tools.web import tavily_search
from jarvis.tools.latex import latex_write, latex_compile
from jarvis.tools.excel import read_excel
from jarvis.tools import python_exec
from jarvis.subagents.math import run_math
from jarvis.subagents.writer import run_writer
from jarvis.subagents.research import run_research
from jarvis.subagents.coder import run_coder


@dataclass
class AgentDeps:
    settings: Settings
    memory: Memory
    workspace: Path
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    current_query: str = ""
    detected_language: str = "en"


_DATA_REPORT_KEYWORDS = frozenset([
    "pdf", "excel", "xlsx", "xls", "csv", "report", "rapor", "plot", "grafik",
    "chart", "data", "veri", "analiz", "analysis", "hw", "odev", "ödev",
])


def _build_env_block(workspace: Path) -> str:
    """Compute the user-environment block once at startup (paths never change mid-session)."""
    import os
    home = Path(os.path.expanduser("~"))
    desktop_candidates = [home / "OneDrive" / "Desktop", home / "Desktop"]
    desktop = next((p for p in desktop_candidates if p.exists()), desktop_candidates[0])
    return (
        f"\n\n## User environment (Windows)\n"
        f"- Home: `{home}`\n"
        f"- Desktop: `{desktop}`\n"
        f"- Workspace (current working dir): `{workspace}`\n\n"
        "When the user mentions **masaüstü / Desktop / my desktop**, use the Desktop "
        "path above (absolute) with `file_list`, `file_read`, `pdf_read`, or `shell_run`. "
        "Never assume files are inside the Workspace unless the user explicitly said so."
    )


def _load_system_prompt(
    settings: Settings,
    memory_context: str = "",
    detected_language: str = "en",
    env_block: str = "",
    user_query: str = "",
) -> str:
    prompt_path = Path(__file__).parent / "prompts" / "system.md"
    raw = prompt_path.read_text(encoding="utf-8")
    raw = raw.replace("{user_name}", settings.user_name)
    raw = raw.replace("{memory_context}", memory_context or "(no prior context retrieved)")

    # Inject data-report workflow only when the query mentions relevant keywords.
    if any(kw in user_query.lower() for kw in _DATA_REPORT_KEYWORDS):
        workflow_path = Path(__file__).parent / "prompts" / "workflows" / "data_report.md"
        raw += "\n\n" + workflow_path.read_text(encoding="utf-8")

    raw += env_block

    if detected_language != "en":
        lang_name = LANG_NAMES.get(detected_language[:2], detected_language)
        raw += (
            f"\n\nIMPORTANT: The user is speaking {lang_name}. "
            f"You MUST respond entirely in {lang_name}."
        )
    return raw


def _trim_history(messages: list[Any], max_messages: int = 12, payload_cap: int = 500) -> list[Any]:
    """Slice to last max_messages; truncate oversized ToolReturnPart payloads to save tokens."""
    from pydantic_ai.messages import ToolReturnPart, ModelRequest
    trimmed = messages[-max_messages:] if len(messages) > max_messages else messages
    result = []
    for msg in trimmed:
        if isinstance(msg, ModelRequest):
            new_parts = []
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and len(part.content) > payload_cap:
                    new_parts.append(dataclasses.replace(part, content=part.content[:payload_cap] + "...[truncated]"))
                else:
                    new_parts.append(part)
            result.append(dataclasses.replace(msg, parts=new_parts))
        else:
            result.append(msg)
    return result


def build_agent(settings: Settings, env_block: str = "", tool_profile: str = "full") -> Agent[AgentDeps, str]:
    """Build the orchestrator agent with all tools and sub-agent delegates."""
    default_model = OpenAIModel(
        settings.local_model,
        provider=OpenAIProvider(base_url=settings.ollama_api_url, api_key="ollama"),
    )

    agent: Agent[AgentDeps, str] = Agent(
        model=default_model,
        deps_type=AgentDeps,
        output_type=str,
    )

    @agent.system_prompt
    async def _dynamic_system_prompt(ctx: RunContext[AgentDeps]) -> str:
        memory_ctx = ctx.deps.memory.recall(ctx.deps.current_query, n=1)
        return _load_system_prompt(
            ctx.deps.settings,
            memory_ctx,
            ctx.deps.detected_language,
            env_block,  # pre-rendered at startup, captured by closure
            ctx.deps.current_query,
        )

    # ── Direct tools ──────────────────────────────────────────────────────────

    @agent.tool
    async def shell_run(ctx: RunContext[AgentDeps], command: str) -> str:
        """Run a PowerShell command (opening apps, checking state, running scripts — no destructive ops)."""
        safe, reason = shell_tools.is_safe(command)
        if not safe:
            return f"[BLOCKED] {reason}. Please ask the user to run this manually."
        return shell_tools.run(command)

    @agent.tool
    async def file_read(ctx: RunContext[AgentDeps], path: str) -> str:
        """Read a text file. Accepts workspace-relative or absolute paths within home dir."""
        try:
            return file_tools.read(path, ctx.deps.workspace)
        except (FileNotFoundError, PermissionError) as e:
            return f"[ERROR] {e}"

    @agent.tool
    async def file_write(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
        """Write text to a file (creates parent dirs). Path relative to workspace."""
        try:
            return file_tools.write(path, content, ctx.deps.workspace)
        except PermissionError as e:
            return f"[ERROR] {e}"

    @agent.tool
    async def file_list(ctx: RunContext[AgentDeps], path: str = ".") -> str:
        """List files and directories at path (default: workspace root)."""
        try:
            return file_tools.list_dir(path, ctx.deps.workspace)
        except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
            return f"[ERROR] {e}"

    @agent.tool
    async def pdf_read(ctx: RunContext[AgentDeps], path: str) -> str:
        """Extract text from a PDF. Accepts workspace-relative or absolute paths."""
        full_path = ctx.deps.workspace / path if not Path(path).is_absolute() else Path(path)
        return read_pdf(full_path)

    @agent.tool
    async def excel_read(ctx: RunContext[AgentDeps], path: str, sheet: str | None = None) -> str:
        """Read an Excel file (.xlsx/.xls) — returns column list + first 50 rows per sheet."""
        full = ctx.deps.workspace / path if not Path(path).is_absolute() else Path(path)
        return read_excel(full, sheet)

    @agent.tool
    async def python_run(ctx: RunContext[AgentDeps], script_path: str) -> str:
        """Execute a Python script (use after generate_code produces a plot/analysis script)."""
        full = (
            ctx.deps.workspace / script_path
            if not Path(script_path).is_absolute()
            else Path(script_path)
        )
        return python_exec.run_script(full)

    @agent.tool
    async def web_search(ctx: RunContext[AgentDeps], query: str) -> str:
        """Quick web search via Tavily. For deep research use the research() sub-agent."""
        return tavily_search(query, api_key=ctx.deps.settings.tavily_api_key)

    if tool_profile == "lite":
        # ── Lite profile: merged report tool + single delegate dispatcher ────
        # Saves ~5 tool schemas (~500 tokens) vs full — needed for Groq TPM limits.

        @agent.tool
        async def report_build(ctx: RunContext[AgentDeps], title: str, body: str) -> str:
            """Write a LaTeX report and compile it to PDF in one step."""
            reports_dir = ctx.deps.workspace / "vault" / "reports"
            tex_path = latex_write(title, body, reports_dir)
            if tex_path.startswith("[ERROR]"):
                return tex_path
            return latex_compile(tex_path)

        @agent.tool
        async def delegate(ctx: RunContext[AgentDeps], specialist: str, payload: str) -> str:
            """Route to a specialist sub-agent. specialist must be one of: math, writer, research, coder.
            math: equations/proofs; writer: academic prose; research: web research; coder: Python scripts."""
            s = specialist.strip().lower()
            if s in ("math", "math_solve"):
                return await run_math(payload, ctx.deps.settings)
            if s in ("writer", "write", "write_content"):
                return await run_writer(payload, "academic", ctx.deps.settings)
            if s in ("research",):
                return await run_research(payload, ctx.deps.settings)
            if s in ("coder", "code", "generate_code"):
                return await run_coder(payload, ctx.deps.settings)
            return f"[ERROR] Unknown specialist '{specialist}'. Choose from: math, writer, research, coder."

    else:
        # ── Full profile: all tools registered individually ───────────────────

        @agent.tool
        async def note_append(ctx: RunContext[AgentDeps], topic: str, body: str) -> str:
            """Save a markdown note to the vault when the user asks to remember something."""
            return append_note(topic, body, ctx.deps.memory)

        @agent.tool
        async def report_write(ctx: RunContext[AgentDeps], title: str, body: str) -> str:
            """Write a LaTeX report to vault/reports/{title}.tex. Call report_compile after."""
            reports_dir = ctx.deps.workspace / "vault" / "reports"
            return latex_write(title, body, reports_dir)

        @agent.tool
        async def report_compile(ctx: RunContext[AgentDeps], tex_path: str) -> str:
            """Compile a .tex to PDF via pdflatex. On failure, fix the LaTeX and retry."""
            return latex_compile(tex_path)

        @agent.tool
        async def math_solve(ctx: RunContext[AgentDeps], problem: str) -> str:
            """Delegate to MathAgent for algebra, calculus, ODEs, linear algebra, stats. Returns LaTeX."""
            return await run_math(problem, ctx.deps.settings)

        @agent.tool
        async def write_content(ctx: RunContext[AgentDeps], topic: str, style: str) -> str:
            """Delegate to WriterAgent for academic prose (abstracts, intros, conclusions). Returns LaTeX."""
            return await run_writer(topic, style, ctx.deps.settings)

        @agent.tool
        async def research(ctx: RunContext[AgentDeps], query: str) -> str:
            """Delegate to ResearchAgent for web-augmented research with citations. Returns LaTeX."""
            return await run_research(query, ctx.deps.settings)

        @agent.tool
        async def generate_code(ctx: RunContext[AgentDeps], spec: str) -> str:
            """Delegate to CoderAgent for Python/scripts/algorithms. Returns LaTeX with code blocks."""
            return await run_coder(spec, ctx.deps.settings)

    return agent


# ── Model catalogue ───────────────────────────────────────────────────────────
# Each entry: (model_id, display_label, provider, short_description)
AVAILABLE_MODELS: list[tuple[str, str, str, str]] = [
    # ── Gemini ────────────────────────────────────────────────────────────────
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", "gemini", "1000 RPD free · en hızlı"),
    ("gemini-2.5-flash",      "Gemini 2.5 Flash",      "gemini", "50 RPD free · akıllı"),
    ("gemini-2.5-pro",        "Gemini 2.5 Pro",        "gemini", "Gemini Pro aboneliği · en iyi"),
    ("gemini-2.0-flash",      "Gemini 2.0 Flash",      "gemini", "200 RPD free"),
    # ── Groq ──────────────────────────────────────────────────────────────────
    ("meta-llama/llama-4-scout-17b-16e-instruct", "Llama 4 Scout",  "groq", "30k TPM · hızlı"),
    ("llama-3.3-70b-versatile",                   "Llama 3.3 70B",  "groq", "12k TPM"),
    ("openai/gpt-oss-120b",                       "GPT-OSS 120B",   "groq", "8k TPM"),
    ("qwen/qwen3-32b",                            "Qwen 3 32B",     "groq", "6k TPM"),
]


def _label_for(model_id: str) -> str:
    """Return the display label for a model_id, or the id itself if not in catalogue."""
    for mid, label, _, _ in AVAILABLE_MODELS:
        if mid == model_id:
            return label
    return model_id


# ── Hybrid runner ─────────────────────────────────────────────────────────────

class JarvisAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.memory = Memory(settings)
        self.workspace = Path(".").resolve()
        self.session_id = str(uuid.uuid4())[:8]
        env_block = _build_env_block(self.workspace)
        self._agent_full = build_agent(settings, env_block, tool_profile="full")
        self._agent_lite = build_agent(settings, env_block, tool_profile="lite")
        self._agent = self._agent_full  # default; switches to lite when a Groq model is active
        self._history: list[Any] = []

        self._local_model = OpenAIModel(
            settings.local_model,
            provider=OpenAIProvider(base_url=settings.ollama_api_url, api_key="ollama"),
        )
        self._cloud_primary = build_cloud_model(settings)
        self._cloud_fallback = build_cloud_model(
            settings,
            model_id=settings.groq_model_fallback if settings.groq_api_key else settings.cloud_model_fallback,
        )
        self._using_fallback = False  # flips True after quota/availability issue; persists for session
        # Runtime model override (set by switch_model())
        self._active_model_id: str | None = None

    @property
    def _cloud_model(self):
        return self._cloud_fallback if self._using_fallback else self._cloud_primary

    @property
    def current_model_label(self) -> str:
        # Runtime override takes priority (set by switch_model())
        if self._active_model_id and not self._using_fallback:
            return _label_for(self._active_model_id)
        if self.settings.groq_api_key:
            model = self.settings.groq_model_fallback if self._using_fallback else self.settings.groq_model
            suffix = " (fallback)" if self._using_fallback else ""
            _GROQ_LABELS = {
                "groq/compound": "Groq Compound",
                "groq/compound-mini": "Groq Compound Mini",
                "openai/gpt-oss-120b": "GPT-OSS 120B (Groq)",
                "openai/gpt-oss-20b": "GPT-OSS 20B (Groq)",
                "llama-3.3-70b-versatile": "Llama 3.3 70B (Groq)",
                "meta-llama/llama-4-scout-17b-16e-instruct": "Llama 4 Scout (Groq)",
                "qwen/qwen3-32b": "Qwen 3 32B (Groq)",
            }
            return _GROQ_LABELS.get(model, f"{model} (Groq)") + suffix
        if self._using_fallback:
            return f"{self.settings.cloud_model_fallback} (fallback — daily quota hit)"
        return self.settings.cloud_model_label

    def reset(self) -> None:
        """Clear conversation history so the next turn starts fresh."""
        self._history = []

    def switch_model(self, model_id: str) -> str:
        """Switch the active cloud model at runtime. Returns the new display label.

        Works for any model in AVAILABLE_MODELS. Groq models require GROQ_API_KEY
        to be set in .env. Resets the fallback flag so the new model is tried fresh.
        """
        groq_ids = {m[0] for m in AVAILABLE_MODELS if m[2] == "groq"}

        if model_id in groq_ids:
            if not self.settings.groq_api_key:
                raise ValueError(
                    f"GROQ_API_KEY .env'de tanımlı değil — '{model_id}' kullanılamaz."
                )
            new_model = OpenAIModel(
                model_id,
                provider=OpenAIProvider(
                    base_url=self.settings.groq_api_url,
                    api_key=self.settings.groq_api_key,
                ),
            )
        else:
            new_model = GeminiModel(
                model_id,
                provider=GoogleGLAProvider(api_key=self.settings.gemini_api_key or None),
            )

        self._cloud_primary = new_model
        self._using_fallback = False
        self._active_model_id = model_id
        self._agent = self._agent_lite if model_id in groq_ids else self._agent_full
        return _label_for(model_id)

    async def chat(self, user_input: str, detected_language: str = "en") -> tuple[str, str]:
        """Run one turn. Returns (response_text, model_label)."""
        clean_input = re.sub(r"^/think\s*", "", user_input).strip()

        deps = AgentDeps(
            settings=self.settings,
            memory=self.memory,
            workspace=self.workspace,
            session_id=self.session_id,
            current_query=clean_input,
            detected_language=detected_language,
        )

        async def _try_cloud() -> Any:
            from pydantic_ai.settings import ModelSettings
            return await run_with_retry(
                lambda: self._agent.run(
                    clean_input,
                    model=self._cloud_model,
                    deps=deps,
                    message_history=self._history,
                    model_settings=ModelSettings(max_tokens=4000),
                ),
                label="Orchestrator",
            )

        try:
            result = await _try_cloud()
        except Exception as exc:
            msg = str(exc)
            if "404" in msg or "NOT_FOUND" in msg:
                # Groq: compound might be paid — try fallback model before giving up
                if self.settings.groq_api_key and not self._using_fallback:
                    print(
                        f"\n[JARVIS] Model '{self.settings.groq_model}' not available "
                        f"— switching to {self.settings.groq_model_fallback}."
                    )
                    self._using_fallback = True
                    result = await _try_cloud()
                else:
                    raise RuntimeError(
                        f"Cloud model not found (404). Check GROQ_MODEL / CLOUD_MODEL in .env."
                    ) from exc
            # Groq tool_use_failed (400): model generated Unicode or truncated JSON in a
            # function call. Retry once with explicit ASCII reminder prepended to input.
            elif "tool_use_failed" in msg or (
                "status_code: 400" in msg and "failed_generation" in msg
            ):
                print(
                    "\n[JARVIS] tool_use_failed — retrying with ASCII reminder..."
                )
                ascii_reminder = (
                    "[SYSTEM NOTE] Your previous function call was rejected because "
                    "it contained Unicode characters or was truncated. "
                    "Use ONLY ASCII in all tool arguments and Python code. "
                    "Retry the task from the beginning.\n\n"
                )
                _original_input = clean_input
                clean_input = ascii_reminder + clean_input
                deps.current_query = clean_input
                result = await _try_cloud()
                clean_input = _original_input  # restore for memory storage
            # Daily quota exhausted on primary model → permanently switch to
            # fallback (gemini-2.5-flash-lite has 50× higher daily quota).
            elif is_daily_quota_error(exc) and not self._using_fallback:
                print(
                    f"\n[JARVIS] Daily quota exhausted on {self.settings.effective_cloud_model} "
                    f"— switching to {self.settings.cloud_model_fallback} for the rest of this session."
                )
                self._using_fallback = True
                result = await _try_cloud()
            elif (
                "503" in msg
                or "UNAVAILABLE" in msg
                or "overloaded" in msg.lower()
            ):
                print(
                    "\n[JARVIS] Cloud unavailable after retries — "
                    "degraded local fallback (sub-agents disabled)."
                )
                result = await run_with_retry(
                    lambda: self._agent.run(
                        clean_input,
                        model=self._local_model,
                        deps=deps,
                        message_history=self._history,
                    ),
                    label="Orchestrator (local fallback)",
                )
                response = str(result.output)
                self._history = _trim_history(result.all_messages())
                self.memory.store("user", clean_input, self.session_id)
                self.memory.store("assistant", response, self.session_id)
                self.memory.log_turn("user", clean_input)
                self.memory.log_turn("assistant", response)
                return response, "Qwen 2.5 7B (local, degraded fallback)"
            else:
                raise

        response = str(result.output)
        model_label = self.current_model_label

        self._history = _trim_history(result.all_messages())
        self.memory.store("user", clean_input, self.session_id)
        self.memory.store("assistant", response, self.session_id)
        self.memory.log_turn("user", clean_input)
        self.memory.log_turn("assistant", response)

        return response, model_label

    async def chat_stream(
        self,
        user_input: str,
        detected_language: str = "en",
    ) -> AsyncGenerator[str, None]:
        """Stream one turn token-by-token. Yields text deltas.

        Updates conversation history and memory after the stream is exhausted.
        Use with voice.speak_stream() for quasi-live voice output.
        """
        clean_input = re.sub(r"^/think\s*", "", user_input).strip()

        deps = AgentDeps(
            settings=self.settings,
            memory=self.memory,
            workspace=self.workspace,
            session_id=self.session_id,
            current_query=clean_input,
            detected_language=detected_language,
        )

        chunks: list[str] = []

        def _open_stream():
            from pydantic_ai.settings import ModelSettings
            return self._agent.run_stream(
                clean_input,
                model=self._cloud_model,
                deps=deps,
                message_history=self._history,
                model_settings=ModelSettings(max_tokens=4000),
            )

        try:
            async with _open_stream() as stream:
                async for delta in stream.stream_text(delta=True):
                    chunks.append(delta)
                    yield delta
                self._history = _trim_history(stream.all_messages())
        except Exception as exc:
            msg = str(exc)
            should_fallback = not self._using_fallback and not chunks
            # Groq: compound unavailable (404/paid) → retry with fallback model
            if should_fallback and self.settings.groq_api_key and ("404" in msg or "NOT_FOUND" in msg):
                print(
                    f"\n[JARVIS] Model '{self.settings.groq_model}' not available "
                    f"— switching to {self.settings.groq_model_fallback}."
                )
                self._using_fallback = True
                async with _open_stream() as stream:
                    async for delta in stream.stream_text(delta=True):
                        chunks.append(delta)
                        yield delta
                    self._history = _trim_history(stream.all_messages())
            # Gemini: daily quota exhausted → retry with flash-lite
            elif should_fallback and is_daily_quota_error(exc):
                print(
                    f"\n[JARVIS] Daily quota exhausted on {self.settings.effective_cloud_model} "
                    f"— switching to {self.settings.cloud_model_fallback} for the rest of this session."
                )
                self._using_fallback = True
                async with _open_stream() as stream:
                    async for delta in stream.stream_text(delta=True):
                        chunks.append(delta)
                        yield delta
                    self._history = _trim_history(stream.all_messages())
            else:
                raise

        full_response = "".join(chunks)
        self.memory.store("user", clean_input, self.session_id)
        self.memory.store("assistant", full_response, self.session_id)
        self.memory.log_turn("user", clean_input)
        self.memory.log_turn("assistant", full_response)
