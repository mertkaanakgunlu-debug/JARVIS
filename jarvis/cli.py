"""Rich-themed interactive CLI for J.A.R.V.I.S."""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich.prompt import Prompt
from rich import print as rprint

from jarvis.agent import JarvisAgent, AVAILABLE_MODELS
from jarvis.config import Settings

BANNER = """[bold gold3]
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝[/bold gold3]
[dim]Just A Rather Very Intelligent System  ·  v0.1.0[/dim]"""

HELP_TEXT = """\
[bold]Commands:[/bold]
  [gold3]/think[/gold3] [dim]<message>[/dim]  Zorlu görevleri Pro model + planlayıcı ile çalıştır
  [gold3]/model[/gold3]             Kullanılabilir modelleri listele ve değiştir
  [gold3]/recall[/gold3] [dim]<query>[/dim]   Ham bellek arama sonuçlarını göster
  [gold3]/indexed[/gold3]          RAG vault'una indexlenmiş dosyaları listele
  [gold3]/status[/gold3]           Model + bellek istatistiklerini göster
  [gold3]/budget[/gold3]           Token kullanımı ve Vertex kredi tahmini
  [gold3]/reset[/gold3]            Konuşma geçmişini temizle (yeni görev başlarken)
  [gold3]/help[/gold3]             Bu mesajı göster
  [gold3]/exit[/gold3]             Çıkış (Ctrl+C de çalışır)

[bold]Doküman RAG (Faz 6):[/bold]
  [dim]"Index this PDF"     "Search vault for seismic"     "What did I save about X?"[/dim]

[bold]Model değiştirme (doğal dil):[/bold]
  [dim]"Modeli flash yap"   "Gemini Pro'ya geç"   "Switch to llama"[/dim]
"""

console = Console()


# ── Model-switch helpers ───────────────────────────────────────────────────────

# Keywords mapped to model IDs for fuzzy natural-language matching.
# Longer / more specific keywords first so they win over shorter ones.
_MODEL_KEYWORDS: list[tuple[list[str], str]] = [
    (["2.5-pro", "2.5 pro", "gemini pro", "gemini-pro", "pro"], "gemini-2.5-pro"),
    (["flash-lite", "flash lite", "flash-lite", "lite"],         "gemini-2.5-flash-lite"),
    (["2.5-flash", "2.5 flash", "gemini 2.5", "gemini2.5"],      "gemini-2.5-flash"),
    (["2.0-flash", "2.0 flash", "gemini 2.0", "gemini2.0"],      "gemini-2.0-flash"),
    (["flash"],                                                   "gemini-2.5-flash"),
    (["llama 4", "llama4", "scout"],                              "meta-llama/llama-4-scout-17b-16e-instruct"),
    (["llama 3.3", "llama3.3", "llama 70b", "llama 3"],          "llama-3.3-70b-versatile"),
    (["llama"],                                                   "llama-3.3-70b-versatile"),
    (["gpt-oss", "gpt oss", "gpt120", "gpt 120"],                "openai/gpt-oss-120b"),
    (["qwen3", "qwen 3", "qwen"],                                 "qwen/qwen3-32b"),
]

# Turkish + English trigger phrases that precede or accompany a model name.
_SWITCH_PATTERNS = [
    # Turkish
    r"modeli?\s+(.+?)\s*(?:yap|kullan|seç|ile değiştir|değiştir|olarak ayarla|aktif et)$",
    r"(.+?)\s+modeli?(?:ne|ni|e|i|ye|yi)?\s*(?:geç|değiştir|kullan|ayarla|seç)$",
    r"(?:modeli?\s+)?(.+?)\s+modeli?(?:ni|ne)?\s+(?:kullan|aktif et|seç|değiştir)$",
    r"modeli?\s+(.+)$",
    # English
    r"(?:switch|change|set|use)\s+(?:to\s+)?(?:model\s+)?(.+?)(?:\s+model)?$",
    r"(?:use|activate|enable)\s+(.+?)\s+model$",
]


def _resolve_model_keyword(text: str) -> str | None:
    """Return model_id if ``text`` matches any known model keyword, else None."""
    t = text.lower().strip()
    for keywords, model_id in _MODEL_KEYWORDS:
        if any(kw in t for kw in keywords):
            return model_id
    return None


def _detect_model_switch(user_input: str) -> str | None:
    """Return model_id if the input is a model-switch request, else None.

    Tries regex patterns first; on a match, resolves the captured group against
    the keyword table. Also does a plain keyword scan as a fallback.
    """
    lower = user_input.lower().strip()

    for pattern in _SWITCH_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            candidate = m.group(1).strip()
            model_id = _resolve_model_keyword(candidate)
            if model_id:
                return model_id

    # Fallback: bare keyword anywhere in the sentence, only when the sentence is short
    # (avoids false-positives on regular chat messages).
    if len(lower.split()) <= 8:
        model_id = _resolve_model_keyword(lower)
        if model_id:
            return model_id

    return None


# ── UI helpers ─────────────────────────────────────────────────────────────────

def _print_banner(settings: Settings) -> None:
    console.print(BANNER)
    console.print(Rule(style="gold3 dim"))
    console.print(
        f"[dim]  User: [bold]{settings.user_name}[/bold]  ·  "
        f"Local: [bold]{settings.local_model}[/bold]  ·  "
        f"Cloud: [bold]{settings.effective_cloud_model}[/bold][/dim]\n"
    )


def _print_jarvis(text: str, model_label: str) -> None:
    panel = Panel(
        Text(text, style="white"),
        title=f"[gold3]JARVIS[/gold3] [dim]via {model_label}[/dim]",
        border_style="gold3",
        padding=(0, 1),
    )
    console.print(panel)


def _print_error(text: str) -> None:
    console.print(f"[bold red]⚠  {text}[/bold red]")


def _show_model_menu(agent: JarvisAgent) -> None:
    """Print the numbered model list and return. Selection is handled by caller."""
    current_id = agent._active_model_id or agent.settings.effective_cloud_model

    table = Table(
        show_header=True,
        header_style="bold gold3",
        border_style="dim",
        title="[bold gold3]Kullanılabilir Modeller[/bold gold3]",
        title_justify="left",
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Model", min_width=24)
    table.add_column("Sağlayıcı", width=8)
    table.add_column("Not", style="dim")

    last_provider = ""
    for i, (mid, label, provider, desc) in enumerate(AVAILABLE_MODELS, start=1):
        if provider != last_provider:
            if last_provider:
                table.add_row("", "", "", "")   # blank separator row
            last_provider = provider

        active_mark = " [gold3]★[/gold3]" if mid == current_id else ""
        _PROVIDER_LABELS = {"vertex": "Vertex", "aistudio": "AI Studio", "gemini": "Gemini", "groq": "Groq"}
        provider_str = _PROVIDER_LABELS.get(provider, provider.title())
        table.add_row(str(i), label + active_mark, provider_str, desc)

    console.print(table)


# ── Main loops ─────────────────────────────────────────────────────────────────

async def _run_loop(agent: JarvisAgent) -> None:
    settings = agent.settings

    _print_banner(settings)
    console.print(f"[dim]Type [bold gold3]/help[/bold gold3] for commands. Ctrl+C to exit.[/dim]\n")

    while True:
        try:
            user_input = Prompt.ask(f"[bold blue]{settings.user_name}[/bold blue]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]JARVIS offline. Goodbye.[/dim]")
            break

        if not user_input:
            continue

        lower = user_input.lower().strip()

        # ── Built-in commands ───────────────────────────────────────────────

        if lower in ("/exit", "/quit", "exit", "quit"):
            console.print("[dim]JARVIS offline. Goodbye.[/dim]")
            break

        if lower == "/help":
            console.print(HELP_TEXT)
            continue

        if lower == "/status":
            count = agent.memory.count()
            docs_count = agent.memory.count_docs()
            active = agent._active_model_id or settings.effective_cloud_model
            cost = agent.usage.session_cost
            ef_label = "Gemini text-embedding-004" if agent.memory._gemini_ef_active else "default ONNX"
            console.print(
                f"[dim]Session ID:[/dim]      [bold]{agent.session_id}[/bold]\n"
                f"[dim]Memory turns:[/dim]    [bold]{count}[/bold]\n"
                f"[dim]Vault chunks:[/dim]    [bold]{docs_count}[/bold] [dim](embed: {ef_label})[/dim]\n"
                f"[dim]Active model:[/dim]    [bold]{agent.current_model_label}[/bold] [dim]({active})[/dim]\n"
                f"[dim]Session cost:[/dim]    [yellow]~${cost:.5f}[/yellow]"
            )
            continue

        if lower == "/indexed":
            sources = agent.memory.list_indexed()
            if not sources:
                console.print("[dim]No documents indexed yet. Ask JARVIS to index a file.[/dim]")
            else:
                from pathlib import Path as _Path
                lines = [f"[bold gold3]Indexed documents ({len(sources)}):[/bold gold3]"]
                for src in sources:
                    lines.append(f"  [dim]•[/dim] {_Path(src).name}  [dim]{src}[/dim]")
                console.print("\n".join(lines))
            continue

        if lower in ("/budget", "/b"):
            from rich.panel import Panel as _Panel
            console.print(_Panel(
                agent.usage.report(settings.vertex_credit_usd),
                title="[bold gold3]JARVIS — Usage & Budget[/bold gold3]",
                border_style="gold3 dim",
                padding=(0, 2),
            ))
            continue

        if lower in ("/reset", "/clear"):
            agent.reset()
            console.print("[dim]Conversation history cleared.[/dim]")
            continue

        if lower.startswith("/recall "):
            query = user_input[8:].strip()
            ctx = agent.memory.recall(query, n=8)
            console.print(Panel(ctx or "[dim](no results)[/dim]", title="[gold3]Memory recall[/gold3]", border_style="dim"))
            continue

        # ── /model command ──────────────────────────────────────────────────
        if lower == "/model" or lower.startswith("/model "):
            # If a model ID/number was given inline: /model 2 or /model flash
            inline_arg = user_input[6:].strip() if len(lower) > 6 else ""

            if inline_arg:
                # Try numeric
                if inline_arg.isdigit():
                    idx = int(inline_arg) - 1
                    if 0 <= idx < len(AVAILABLE_MODELS):
                        chosen_id = AVAILABLE_MODELS[idx][0]
                    else:
                        _print_error(f"Geçersiz numara: {inline_arg}")
                        continue
                else:
                    chosen_id = _resolve_model_keyword(inline_arg)
                    if not chosen_id:
                        _print_error(f"Model bulunamadı: '{inline_arg}'")
                        continue
            else:
                # Interactive menu
                _show_model_menu(agent)
                try:
                    raw = Prompt.ask(
                        "\n[dim]Numara girin (iptal için Enter)[/dim]",
                        default="",
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    continue

                if not raw:
                    continue

                if raw.isdigit():
                    idx = int(raw) - 1
                    if 0 <= idx < len(AVAILABLE_MODELS):
                        chosen_id = AVAILABLE_MODELS[idx][0]
                    else:
                        _print_error(f"Geçersiz numara: {raw}")
                        continue
                else:
                    # Accept name/keyword instead of number
                    chosen_id = _resolve_model_keyword(raw)
                    if not chosen_id:
                        _print_error(f"Model bulunamadı: '{raw}'")
                        continue

            try:
                label = agent.switch_model(chosen_id)
                console.print(f"[gold3]✓[/gold3] Model değiştirildi → [bold]{label}[/bold] [dim]({chosen_id})[/dim]")
            except ValueError as e:
                _print_error(str(e))
            continue

        # ── Natural-language model switch detection ─────────────────────────
        detected_switch = _detect_model_switch(user_input)
        if detected_switch:
            try:
                label = agent.switch_model(detected_switch)
                console.print(f"[gold3]✓[/gold3] Model değiştirildi → [bold]{label}[/bold] [dim]({detected_switch})[/dim]")
            except ValueError as e:
                _print_error(str(e))
            continue

        # ── Regular chat turn ───────────────────────────────────────────────
        with console.status("[gold3]Thinking…[/gold3]", spinner="dots"):
            try:
                response, model_label = await agent.chat(user_input)
            except Exception as e:
                _print_error(f"Error: {e}")
                continue

        _print_jarvis(response, model_label)


async def _run_voice_loop(agent: JarvisAgent) -> None:
    try:
        from jarvis.voice import VoiceEngine, is_exit_phrase
    except ImportError as exc:
        _print_error(
            f"Voice dependencies not installed: {exc}\n"
            "Run: pip install faster-whisper sounddevice edge-tts miniaudio numpy"
        )
        return

    settings = agent.settings
    voice = VoiceEngine(settings)
    loop = asyncio.get_running_loop()

    _print_banner(settings)
    console.print("[gold3]Voice mode active.[/gold3] Speak naturally — JARVIS listens automatically.")
    console.print("[dim]Say 'goodbye' / 'güle güle' to exit.  Ctrl+C also works.[/dim]\n")

    console.print("[dim]Loading voice models (first run downloads ~800 MB)...[/dim]")
    await loop.run_in_executor(None, voice.load)
    console.print("[gold3]Ready.[/gold3]\n")

    while True:
        console.print("[dim]Listening...[/dim]", end="\r")
        try:
            text, lang = await loop.run_in_executor(None, voice.listen)
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[dim]JARVIS offline. Goodbye.[/dim]")
            break

        if not text:
            continue

        console.print(f"[bold blue]{settings.user_name}:[/bold blue] {text}   ")

        if is_exit_phrase(text):
            farewell = "Goodbye, Sir." if lang != "tr" else "Güle güle, efendim."
            console.print(f"[dim]JARVIS:[/dim] {farewell}")
            await voice.speak(farewell, lang)
            break

        # Voice-mode model switch (natural language only)
        detected_switch = _detect_model_switch(text)
        if detected_switch:
            try:
                label = agent.switch_model(detected_switch)
                msg = f"Model değiştirildi: {label}"
                console.print(f"[gold3]✓[/gold3] {msg}")
                await voice.speak(msg, lang)
            except ValueError as e:
                _print_error(str(e))
            continue

        console.print("[dim]Thinking...[/dim]", end="\r")
        response_chunks: list[str] = []
        llm_error: list[BaseException] = []

        async def _collecting_stream():
            try:
                async for delta in agent.chat_stream(text, detected_language=lang):
                    response_chunks.append(delta)
                    yield delta
            except BaseException as exc:
                llm_error.append(exc)
                return

        try:
            await voice.speak_stream(_collecting_stream(), lang=lang)
        except Exception as exc:
            _print_error(f"TTS error: {exc}")

        if llm_error:
            from jarvis.utils import is_daily_quota_error
            exc = llm_error[0]
            if is_daily_quota_error(exc):
                _print_error(
                    f"Daily quota exhausted on {settings.effective_cloud_model} "
                    f"and {settings.cloud_model_fallback} both. Free-tier daily quotas "
                    "reset at midnight UTC. Wait or upgrade billing."
                )
            else:
                _print_error(f"LLM error: {exc}")

        if response_chunks:
            _print_jarvis("".join(response_chunks), agent.current_model_label)


def run(voice: bool = False) -> None:
    settings = Settings()
    if not settings.gemini_api_key:
        console.print(
            "[yellow]Warning:[/yellow] GEMINI_API_KEY not set in .env — "
            "cloud fallback (/think) will fail. Local model only."
        )

    agent = JarvisAgent(settings)
    try:
        if voice:
            asyncio.run(_run_voice_loop(agent))
        else:
            asyncio.run(_run_loop(agent))
    except KeyboardInterrupt:
        console.print("\n[dim]JARVIS offline.[/dim]")
