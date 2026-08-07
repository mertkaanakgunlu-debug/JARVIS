"""Rich-themed interactive CLI for J.A.R.V.I.S."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich.prompt import Prompt

from jarvis import paths
from jarvis.agent import JarvisAgent, AVAILABLE_MODELS, ConfirmationRequired
from jarvis.providers.labels import label_for_provider
from jarvis.config import Settings
from jarvis.voice.state import VoiceState

# Faz D (voice observability): display() returns one of these keys; mapped to
# a human-readable status line the CLI overwrites in place (same "[dim]...[/dim]",
# end="\r" idiom the old static "Thinking..." print used).
_VOICE_STATE_LABELS = {
    "idle": "Idle",
    "listening": "Listening...",
    "speech_detected": "Speech detected...",
    "transcribing": "Transcribing...",
    "thinking": "Thinking...",
    "awaiting_confirmation": "Awaiting your answer...",
    "speaking": "Speaking...",
}


# ── Agent Runtime rev.2, Faz 5 ──────────────────────────────────────────────
# JarvisAgent no longer silently auto-resumes the "most recently active"
# session (see agent.py's own comment on this) -- the CLI is the one caller
# that preserves that "continue where I left off" UX, but now explicitly:
# it remembers its OWN last session id in a small JARVIS_HOME-aware file and
# passes it as resume_session_id, instead of the agent guessing.
def _cli_last_session_path() -> Path:
    return paths.data_dir() / "cli_last_session.txt"


def _read_last_session_id() -> str | None:
    """Best-effort -- a missing/unreadable/corrupt file just means "start a
    fresh session", never a startup failure."""
    try:
        text = _cli_last_session_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _write_last_session_id(session_id: str) -> None:
    try:
        path = _cli_last_session_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(session_id, encoding="utf-8")
    except OSError:
        pass

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
  [gold3]/status[/gold3]           Model + bellek + oturum istatistiklerini göster
  [gold3]/budget[/gold3]           Token kullanımı ve Vertex kredi tahmini
  [gold3]/quota[/gold3]            GCP kota + Vertex kullanım durumu (RPM, kredi, tahmin)
  [gold3]/monitor[/gold3]          Proaktif monitör durumunu göster
  [gold3]/killswitch[/gold3]       Güvenlik anahtarı durumu (alt: on, off <sebep>)
  [gold3]/todo[/gold3]             To-do listesi (alt: today, analyze, add <başlık>)
  [gold3]/schedule[/gold3]         Planlı görev ve hatırlatıcıları listele
  [gold3]/sessions[/gold3]         Son oturumları listele
  [gold3]/session[/gold3] [dim]<id>[/dim]     Geçmiş oturuma geç
  [gold3]/entities[/gold3]         Tanınan varlıkları (kişi/proje/dosya) listele
  [gold3]/facts[/gold3]            Bilinen kalıcı gerçekleri (semantic memory) listele
  [gold3]/procedures[/gold3]       Onay bekleyen prosedürler (alt: approve <id>, reject <id>)
  [gold3]/meta[/gold3]             Persona/direktif dosyalarının sürüm kaydını göster
  [gold3]/reset[/gold3]            Mevcut oturumu arşivle, yeni başlat
  [gold3]/help[/gold3]             Bu mesajı göster
  [gold3]/exit[/gold3]             Çıkış (Ctrl+C de çalışır)

[bold]Doküman RAG (Faz 6):[/bold]
  [dim]"Index this PDF"     "Search vault for seismic"     "What did I save about X?"[/dim]

[bold]Spotify (Faz 8 — set SPOTIFY_CLIENT_ID/SECRET in .env):[/bold]
  [dim]"Play Bohemian Rhapsody"    "Pause music"    "What's playing?"[/dim]

[bold]Google Calendar (Faz 9 — OAuth credentials in data/calendar_credentials.json):[/bold]
  [dim]"Bu haftaki etkinliklerimi listele"    "Yarın 15:00'e toplantı ekle"    "Standupı iptal et"[/dim]

[bold]Google Drive (Faz 14 — same OAuth credentials):[/bold]
  [dim]"Drive'da sismik raporu bul"    "Bu PDF'yi Drive'a yükle"    "Drive klasörümü listele"[/dim]

[bold]ITU Webmail (Faz 15 — ITU_USERNAME/ITU_PASSWORD in .env):[/bold]
  [dim]"ITU inboxumu göster"    "Hocama mail at"    "Ödev konulu maili bul"[/dim]

[bold]Finans Analizi (Faz 16 — Burgan Bank + Gmail):[/bold]
  [dim]"Bu ayki harcamalarımı özetle"    "Yemek bütçesi koy 1500 TL"    "Grafik oluştur"[/dim]
  [dim]finance("sync")  ·  finance("summary")  ·  finance("budget_status")  ·  finance("chart")[/dim]

[bold]GCP Kota Takibi (Faz 17 — VERTEX_CREDIT_USD in .env):[/bold]
  [dim]/quota  ·  /quota usage  ·  /quota forecast[/dim]
  [dim]gcp_quota("status")  ·  gcp_quota("usage")  ·  gcp_quota("forecast")[/dim]

[bold]Jeofizik Matematik (Faz 18 — SymPy + Devito + Plotly + PyVista):[/bold]
  [dim]"Akustik dalga denklemini çöz"  "2D sismik simülasyon yap"  "Kontur haritası oluştur"[/dim]
  [dim]geo_math("solve_symbolic", expression="...")  ·  geo_math("wave_simulate_2d")  ·  geo_math("plot_contour")[/dim]

[bold]Proaktif Monitor (Faz 10 — python -m jarvis --monitor):[/bold]
  [dim]Arka planda Gmail + Takvim + ITU mail + Burgan bütçe izler, Windows toast bildirimi gönderir.[/dim]

[bold]Model değiştirme (doğal dil):[/bold]
  [dim]"Modeli flash yap"   "Gemini Pro'ya geç"   "Switch to llama"[/dim]
"""

console = Console()


# ── Model-switch helpers ───────────────────────────────────────────────────────

# Keywords mapped to model IDs for fuzzy natural-language matching.
# Longer / more specific keywords first so they win over shorter ones.
_MODEL_KEYWORDS: list[tuple[list[str], str]] = [
    (["yerel model", "yerel modele", "local model", "ollama", "qwen2.5", "qwen 2.5"], "local/qwen2.5-7b"),
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
    """Return model_id if ``text`` matches any known model keyword, else None.

    Word-boundary matched, not a raw substring test — an unanchored `"pro" in t`
    previously matched inside unrelated words ("proje", "problem", "program",
    "profesyonel", ...), hijacking ordinary messages into a model switch instead
    of answering them (BUG-modelswitch). `\\b` treats Turkish suffix apostrophes
    ("pro'ya") as a boundary but not letters glued directly onto the keyword, so
    "proje" still correctly fails to match "pro".
    """
    t = text.lower().strip()
    for keywords, model_id in _MODEL_KEYWORDS:
        if any(re.search(rf"\b{re.escape(kw)}\b", t) for kw in keywords):
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

def _print_banner(settings: Settings, monitor_active: bool = False) -> None:
    """Announce the routing that will ACTUALLY be used, local tier first.

    Until 2026-07-31 this printed only a cloud model, chosen from
    `settings.use_vertex` — which means "CLOUD_TIER is set", not "cloud calls
    can happen". With the project's local-first default (`CLOUD_POLICY=off`)
    that produced a banner reading "Vertex AI · fast: gemini-…" on a machine
    where no cloud call was structurally possible and every answer came from
    local Ollama. The owner hit exactly this while testing.

    Local is named first because it is the primary tier; cloud is shown as what
    it is — an escalation path — and only when policy actually permits one.
    """
    console.print(BANNER)
    console.print(Rule(style="gold3 dim"))

    parts = [f"Local: [bold]{settings.local_model}[/bold]"]
    policy = getattr(settings, "cloud_policy", "auto")
    if policy == "off":
        parts.append("Cloud: [dim]kapalı (CLOUD_POLICY=off)[/dim]")
    else:
        cloud_model = (
            settings.vertex_model_fast if settings.use_vertex
            else settings.effective_cloud_model
        )
        tier = "Vertex" if settings.use_vertex else "AI Studio"
        suffix = " (yalnız açık seçimde)" if policy == "explicit" else ""
        parts.append(f"Cloud: [bold]{cloud_model}[/bold] · {tier}{suffix}")

    monitor_str = "  ·  [green]monitor ✓[/green]" if monitor_active else ""
    console.print(
        f"[dim]  User: [bold]{settings.user_name}[/bold]  ·  "
        f"{'  ·  '.join(parts)}{monitor_str}[/dim]\n"
    )


def _print_voice_diagnostics(engine, state=None) -> None:
    """Faz D (voice observability): startup diagnostics for --voice mode --
    which mic/speaker were actually picked, the fixed sample rate, and
    whether Whisper actually loaded onto the requested device (owner's own
    real incident: CUDA silently falling back to CPU with no visible sign
    short of reading logs). Best-effort throughout -- a diagnostics print
    must never itself crash voice mode startup.

    Now a thin renderer over jarvis.voice.diagnostics.collect(), which the
    API's GET /voice/status serves too, so the two surfaces cannot report
    different things (the review's "telemetry is measurable but not
    observable" finding)."""
    from jarvis.voice.diagnostics import collect

    snap = collect(engine, state)
    console.print(
        f"[dim]Input device:  {snap.input_device}\n"
        f"Output device: {snap.output_device}\n"
        f"Sample rate:   {snap.sample_rate if snap.sample_rate is not None else 'unknown'} Hz\n"
        f"STT device:    {snap.stt_device}[/dim]\n"
    )


def _print_voice_status(engine, state=None) -> None:
    """`/voice-status`: the LIVE half of the same snapshot -- the counters
    that answer "is capture keeping up right now?", which the startup print
    (all-static fields, printed once before anything has happened) never
    could."""
    from jarvis.voice.diagnostics import collect

    snap = collect(engine, state)

    def _n(value, suffix: str = "") -> str:
        return "—" if value is None else f"{value}{suffix}"

    lines = [
        f"Input device:   {snap.input_device}",
        f"Output device:  {snap.output_device}",
        f"Sample rate:    {_n(snap.sample_rate, ' Hz')}",
        f"STT device:     {snap.stt_device}",
        "",
        f"State:          capture={_n(snap.capture)}  response={_n(snap.response)}"
        f"  display={_n(snap.display)}",
        f"Mic RMS:        {snap.mic_rms:.4f}",
        f"Queue depth:    {_n(snap.queue_depth)} frame(s)",
        f"Input status:   {_n(snap.input_status_count)} flagged"
        f"  ({_n(snap.input_overflow_count)} real overflow)",
        f"Out underruns:  {_n(snap.output_underrun_count)}",
        "",
        f"Last turn:      end={_n(snap.last_turn_end_reason)}"
        f"  audio={_n(round(snap.last_captured_audio_s, 2) if snap.last_captured_audio_s is not None else None, 's')}"
        f"  stt={_n(round(snap.last_stt_s, 2) if snap.last_stt_s is not None else None, 's')}",
        f"Last turn VAD:  max={_n(round(snap.last_vad_prob_max, 3) if snap.last_vad_prob_max is not None else None)}"
        f"  mean={_n(round(snap.last_vad_prob_mean, 3) if snap.last_vad_prob_mean is not None else None)}",
    ]
    console.print(Panel(
        Text("\n".join(lines), style="white"),
        title="[gold3]voice status[/gold3]",
        border_style="gold3",
        padding=(0, 1),
    ))


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
        provider_str = label_for_provider(provider)
        table.add_row(str(i), label + active_mark, provider_str, desc)

    console.print(table)


# ── Confirmation gate (Faz 4 / BUG-3) ────────────────────────────────────────

async def _handle_confirmation_cli(agent: JarvisAgent, conf_id: str, payload: dict) -> None:
    """A tool call was interrupted for confirmation (jarvis.agent.ConfirmationRequired).
    Show what's pending, ask once, then resume the same turn via
    agent.resume_and_stream() with the user's decision.

    Review remediation: resume_and_stream() can itself yield a SECOND
    __jarvis_confirm__ marker if the resumed turn hits another confirmable
    tool call -- before this fix, that marker was printed as JARVIS's raw
    reply instead of a new approval prompt, leaving the graph interrupted
    with no path left to resume it. Loops instead of returning: each
    iteration prompts once and resumes once; if the resume yields a fresh
    marker, the loop continues with that marker's conf_id/payload.

    Completion-contract TTFB (2026-08-07): a resumed turn can also be
    contracted+enforce, so this loop can now also see a __jarvis_progress__
    marker (silently skipped -- the "Thinking…" status spinner already covers
    it, and text mode has no separate progress surface to update) and a
    __jarvis_final__ correction marker, which was previously unhandled here
    and printed as raw JSON. Unlike voice, text output CAN redraw, so the
    fix is to REPLACE the accumulated chunks with the authoritative text --
    mirrors parse_final_marker's own docstring on why voice swallows the
    same marker instead."""
    from jarvis.voice.session import parse_confirm_marker, parse_final_marker, parse_progress_marker

    while True:
        tools = payload.get("tools", [])
        lines = []
        for t in tools:
            desc = t.get("description") or f"{t.get('name')}({t.get('args')})"
            lines.append(f"• {desc}")
        console.print(Panel(
            "\n".join(lines) or "(no detail)",
            title="[bold red]Confirmation required[/bold red]",
            border_style="red",
        ))
        try:
            raw = Prompt.ask(
                "[bold]Approve?[/bold] [dim](y = yes, anything else = deny + reason)[/dim]",
                default="n",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            raw = "n"

        decision = "approve" if raw.lower() in ("y", "yes", "evet", "onay", "onayla") else f"deny:{raw}"

        chunks: list[str] = []
        next_marker: dict | None = None
        with console.status("[gold3]Thinking…[/gold3]", spinner="dots"):
            async for token in agent.resume_and_stream(conf_id, decision):
                marker = parse_confirm_marker(token)
                if marker is not None:
                    next_marker = marker
                    break
                if parse_progress_marker(token) is not None:
                    continue
                final_text = parse_final_marker(token)
                if final_text is not None:
                    chunks = [final_text]
                    continue
                chunks.append(token)
        if chunks:
            _print_jarvis("".join(chunks), agent.current_model_label)

        if next_marker is None:
            return
        conf_id, payload = next_marker["id"], next_marker["payload"]


# ── Main loops ─────────────────────────────────────────────────────────────────

async def _run_loop(agent: JarvisAgent, monitor=None) -> None:
    """Faz 5: connect MCP servers (Playwright, etc.) on this function's own
    long-lived loop before any turn can run, and guarantee cleanup on the way
    out (normal /exit, or Ctrl+C/EOF) -- see JarvisAgent.connect_mcp_tools()'s
    docstring for why this must be the loop that opens the connection, not
    wherever the first chat() call happens to come from."""
    await agent.connect_mcp_tools()
    agent.run_startup_backfill()
    try:
        await _run_loop_impl(agent, monitor)
    finally:
        await agent.close_mcp_tools()


async def _run_loop_impl(agent: JarvisAgent, monitor=None) -> None:
    settings = agent.settings

    _print_banner(settings, monitor_active=monitor is not None)
    console.print("[dim]Type [bold gold3]/help[/bold gold3] for commands. Ctrl+C to exit.[/dim]\n")

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
            summaries_count = agent.memory.count_summaries()
            active = agent._active_model_id or settings.effective_cloud_model
            cost = agent.usage.session_cost
            from jarvis.memory import GEMINI_EMBED_MODEL
            ef_label = {
                "ollama": "Ollama nomic-embed-text (local)",
                # Read from the constant, not retyped: this line said
                # "text-embedding-004" long after that id was retired.
                "gemini": f"Gemini {GEMINI_EMBED_MODEL.removeprefix('models/')}",
                "default": "default ONNX",
            }[agent.memory._embedding_backend]
            total_sessions = agent.session_store.total_sessions()
            total_entities = agent.session_store.total_entities()
            total_facts = agent.facts_store.total_facts()
            total_procedures = agent.procedure_store.total()
            history_len = len(agent._history)
            unpriced = agent.usage.session_unpriced_tokens
            unpriced_note = (
                f" [magenta]+ {unpriced:,} unpriced tok[/magenta] [dim](AI Studio billing mode unknown)[/dim]"
                if unpriced else ""
            )
            console.print(
                f"[dim]Session ID:[/dim]      [bold]{agent.session_id}[/bold] [dim]({history_len} messages loaded)[/dim]\n"
                f"[dim]Total sessions:[/dim]  [bold]{total_sessions}[/bold]\n"
                f"[dim]Known entities:[/dim]  [bold]{total_entities}[/bold]\n"
                f"[dim]Known facts:[/dim]     [bold]{total_facts}[/bold] [dim](semantic memory)[/dim]\n"
                f"[dim]Procedures:[/dim]      [bold]{total_procedures}[/bold] [dim](procedural memory)[/dim]\n"
                f"[dim]Summaries indexed:[/dim][bold]{summaries_count}[/bold]\n"
                f"[dim]Memory turns:[/dim]    [bold]{count}[/bold]\n"
                f"[dim]Vault chunks:[/dim]    [bold]{docs_count}[/bold] [dim](embed: {ef_label})[/dim]\n"
                f"[dim]Active model:[/dim]    [bold]{agent.current_model_label}[/bold] [dim]({active})[/dim]\n"
                f"[dim]Session cost:[/dim]    [yellow]~${cost:.5f}[/yellow]{unpriced_note}"
            )
            trace = agent.last_turn_trace
            if trace:
                # Response-scoped marker; a critic/planner-only fallback shows
                # as a dim turn-level note instead of relabeling the answer.
                if trace.get("response_fallback_used"):
                    fb_marker = " [yellow](fallback)[/yellow]"
                elif trace.get("turn_had_any_fallback"):
                    fb_marker = " [dim](fallback elsewhere in turn)[/dim]"
                else:
                    fb_marker = ""
                # Faz 2.5: the rule that chose the role, not just the role.
                # "requested=reasoning" alone cannot distinguish a router
                # misreading an easy request from a genuinely hard one.
                why = trace.get("role_reason")
                console.print(
                    f"[dim]Last turn:[/dim]       requested=[bold]{trace['requested_role']}[/bold]"
                    f"{f' [dim]({why})[/dim]' if why else ''} "
                    f"-> actual=[bold]{trace['provider']}:{trace['model']}[/bold]"
                    f"{fb_marker} "
                    f"[dim]({trace['calls']} LLM call(s), "
                    f"{trace['input_tokens']} in / {trace['output_tokens']} out)[/dim]"
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
            old_id = agent.session_id
            # reset_async: SQLite archive runs in to_thread instead of
            # blocking this REPL's event loop mid-prompt.
            await agent.reset_async()
            _write_last_session_id(agent.session_id)
            console.print(
                f"[dim]Session [bold]{old_id}[/bold] archived. "
                f"New session: [bold]{agent.session_id}[/bold][/dim]"
            )
            continue

        if lower == "/sessions":
            rows = agent.session_store.list_sessions(n=10)
            if not rows:
                console.print("[dim]No sessions yet.[/dim]")
            else:
                table = Table(
                    show_header=True,
                    header_style="bold gold3",
                    border_style="dim",
                    title="[bold gold3]Oturumlar[/bold gold3]",
                    title_justify="left",
                )
                table.add_column("ID", style="bold", width=14)
                table.add_column("Tarih", width=10)
                table.add_column("Mesaj", justify="right", width=6)
                table.add_column("Konu", style="dim")
                table.add_column("Durum", width=8)
                for r in rows:
                    date_str = r["created_at"][:10] if r["created_at"] else "-"
                    active_mark = "[green]aktif[/green]" if r["id"] == agent.session_id else (
                        "[dim]arşiv[/dim]" if r["status"] == "archived" else "[dim]—[/dim]"
                    )
                    table.add_row(
                        r["id"],
                        date_str,
                        str(r["message_count"] or 0),
                        (r["topic_hint"] or "—")[:50],
                        active_mark,
                    )
                console.print(table)
            continue

        if lower.startswith("/session "):
            sid = user_input[9:].strip()
            if not sid:
                _print_error("Kullanım: /session <id>")
                continue
            try:
                n = agent.switch_session(sid)
                _write_last_session_id(agent.session_id)
                console.print(
                    f"[gold3]✓[/gold3] Oturum yüklendi: [bold]{sid}[/bold] [dim]({n} mesaj)[/dim]"
                )
            except Exception as e:
                _print_error(f"Oturum yüklenemedi: {e}")
            continue

        if lower == "/entities":
            rows = agent.session_store.top_entities(n=20)
            if not rows:
                console.print("[dim]Henüz tanınan varlık yok. Birkaç konuşma sonrası oluşur.[/dim]")
            else:
                table = Table(
                    show_header=True,
                    header_style="bold gold3",
                    border_style="dim",
                    title="[bold gold3]Tanınan Varlıklar[/bold gold3]",
                    title_justify="left",
                )
                table.add_column("İsim", style="bold", min_width=14)
                table.add_column("Tür", width=12)
                table.add_column("Açıklama", style="dim")
                table.add_column("Anılma", justify="right", width=6)
                _TYPE_COLORS = {
                    "person": "cyan",
                    "project": "green",
                    "file": "yellow",
                    "organization": "magenta",
                    "topic": "blue",
                }
                for r in rows:
                    color = _TYPE_COLORS.get(r["type"], "white")
                    table.add_row(
                        r["name"],
                        f"[{color}]{r['type']}[/{color}]",
                        (r["description"] or "—")[:60],
                        str(r["mention_count"]),
                    )
                console.print(table)
            continue

        if lower == "/facts":
            rows = agent.facts_store.list_facts(n=20)
            if not rows:
                console.print("[dim]Henüz kayıtlı gerçek yok. Birkaç konuşma sonrası oluşur.[/dim]")
            else:
                table = Table(
                    show_header=True,
                    header_style="bold gold3",
                    border_style="dim",
                    title="[bold gold3]Bilinen Gerçekler (Semantic Memory)[/bold gold3]",
                    title_justify="left",
                )
                table.add_column("Gerçek", style="bold")
                table.add_column("Anılma", justify="right", width=6)
                table.add_column("Son görülme", width=19)
                for r in rows:
                    table.add_row(r["fact_text"], str(r["mention_count"]), (r["last_seen"] or "")[:19])
                console.print(table)
            continue

        # ── /procedures command (GPT-5.6 review remediation, Faz 3) ──────────
        if lower == "/procedures" or lower.startswith("/procedures "):
            sub = user_input[len("/procedures"):].strip()

            if not sub or sub == "list":
                drafts = agent.procedure_store.get_drafts()
                if not drafts:
                    console.print("[dim]Onay bekleyen taslak prosedür yok.[/dim]")
                else:
                    table = Table(
                        show_header=True,
                        header_style="bold gold3",
                        border_style="dim",
                        title=f"[bold gold3]Onay Bekleyen Prosedürler ({len(drafts)})[/bold gold3]",
                        title_justify="left",
                    )
                    table.add_column("ID", style="dim", width=6)
                    table.add_column("İsim", style="bold")
                    table.add_column("Açıklama")
                    table.add_column("Oluşturulma", width=19)
                    for p in drafts:
                        table.add_row(str(p["id"]), p["name"], p["description"], (p["created_at"] or "")[:19])
                    console.print(table)
                    console.print(
                        "[dim]Onaylamak için: /procedures approve <id>  ·  "
                        "Reddetmek için: /procedures reject <id>[/dim]"
                    )
                continue

            if sub.startswith("approve "):
                try:
                    pid = int(sub[len("approve "):].strip())
                except ValueError:
                    _print_error("Geçersiz id.")
                    continue
                row = agent.procedure_store.get(pid)
                if not row or row["status"] != "draft":
                    _print_error(f"#{pid} bulunamadı ya da zaten taslak değil.")
                    continue
                agent.procedure_store.approve(pid)
                agent.memory.approve_procedure(pid, row["name"], row["body"])
                console.print(f"[gold3]✓[/gold3] Prosedür onaylandı: {row['name']} (id={pid}) — artık recall'a dahil.")
                continue

            if sub.startswith("reject "):
                try:
                    pid = int(sub[len("reject "):].strip())
                except ValueError:
                    _print_error("Geçersiz id.")
                    continue
                row = agent.procedure_store.get(pid)
                if agent.procedure_store.reject(pid):
                    agent.memory.delete_procedure(pid)
                    console.print(f"[gold3]✓[/gold3] Taslak reddedildi ve silindi: {row['name'] if row else pid} (id={pid})")
                else:
                    _print_error(f"#{pid} bulunamadı ya da zaten taslak değil.")
                continue

            _print_error(f"Bilinmeyen alt komut: '{sub}'. Geçerli: list, approve <id>, reject <id>")
            continue

        if lower == "/meta":
            from pathlib import Path as _Path
            from rich.markdown import Markdown
            versions_path = _Path(__file__).parent / "prompts" / "CORE_VERSIONS.md"
            if versions_path.exists():
                console.print(Markdown(versions_path.read_text(encoding="utf-8")))
            else:
                console.print("[dim](CORE_VERSIONS.md bulunamadı)[/dim]")
            continue

        if lower.startswith("/recall "):
            query = user_input[8:].strip()
            ctx = agent.memory.recall(query, n=8)
            console.print(Panel(ctx or "[dim](no results)[/dim]", title="[gold3]Memory recall[/gold3]", border_style="dim"))
            continue

        if lower == "/monitor":
            if monitor is None:
                console.print(
                    "[dim]Monitor etkin değil. Başlatmak için:[/dim]\n"
                    "  [bold gold3]python -m jarvis --monitor[/bold gold3]  (standalone)\n"
                    "  [bold gold3]python -m jarvis --monitor --voice[/bold gold3]  (sesli mod + monitor)"
                )
            else:
                console.print(Panel(
                    monitor.status_line(),
                    title="[bold gold3]JARVIS Monitor[/bold gold3]",
                    border_style="gold3 dim",
                    padding=(0, 1),
                ))
            continue

        # ── /killswitch command (Faz 4) ─────────────────────────────────────
        if lower == "/killswitch" or lower.startswith("/killswitch "):
            sub = user_input[len("/killswitch"):].strip()
            from jarvis import kill_switch as _ks

            if not sub or sub == "status":
                st = _ks.status()
                if st.get("enabled", True):
                    console.print("Kill switch: [green]ON[/green] — actions flow normally.")
                else:
                    console.print(
                        f"Kill switch: [bold red]OFF[/bold red] — risky external actions "
                        f"(email send, calendar write, shell exec, ...) are blocked. "
                        f"Reason: {st.get('reason') or '(none given)'}"
                    )
                continue

            if sub == "on":
                _ks.enable()
                console.print("[gold3]✓[/gold3] Kill switch re-enabled — JARVIS may act normally again.")
                continue

            if sub == "off" or sub.startswith("off "):
                reason_text = sub[4:].strip() if sub.startswith("off ") else ""
                _ks.disable(reason_text)
                console.print(
                    "[bold red]⚠[/bold red] Kill switch OFF — every risky external action "
                    "(email send, calendar write, shell exec, ...) is now blocked."
                    + (f" Reason: {reason_text}" if reason_text else "")
                )
                continue

            _print_error(f"Bilinmeyen alt komut: '{sub}'. Geçerli: status, on, off <sebep>")
            continue

        # ── /workflow command (Agent Runtime rev.2, Faz 7 Part 2) ───────────
        # Approval resolution is a CLI command, deliberately NOT a tool the
        # agent itself can call -- exposing "approve"/"deny" as a tool would
        # let the model resolve its own confirmation gate, exactly the
        # bypass this whole safety mechanism exists to prevent (see
        # jarvis/execution/workflow_engine.py's own module docstring: the
        # approval pause has no LangGraph interrupt to piggyback on here,
        # since the workflow engine runs outside the compiled graph -- this
        # command is its human-only equivalent). Only a human, via this
        # command, can approve or deny a paused step.
        if lower == "/workflow" or lower.startswith("/workflow "):
            sub = user_input[len("/workflow"):].strip()
            from jarvis.execution import workflow_store
            from jarvis.execution.workflow_approval import resolve_workflow_approval
            from jarvis.execution.workflow_engine import render_workflow_report

            if not sub or sub == "list":
                rows = workflow_store.list_workflows(limit=20)
                if not rows:
                    console.print("[dim]Henüz bir workflow yok.[/dim]")
                else:
                    table = Table(border_style="gold3 dim")
                    table.add_column("ID")
                    table.add_column("Durum")
                    table.add_column("Güncellendi")
                    for r in rows:
                        table.add_row(r["workflow_id"], r["status"], r["updated_at"][:19])
                    console.print(table)
                continue

            parts = sub.split(maxsplit=1)
            verb = parts[0].lower()

            if verb == "show" and len(parts) == 2:
                plan = workflow_store.load(parts[1])
                if plan is None:
                    _print_error(f"Workflow bulunamadı: '{parts[1]}'")
                else:
                    console.print(Panel(render_workflow_report(plan), border_style="gold3 dim"))
                continue

            if verb in ("approve", "deny") and len(parts) == 2:
                rest = parts[1].split(maxsplit=1)
                workflow_id = rest[0]
                reason = rest[1] if len(rest) > 1 else ""
                decision = "approve" if verb == "approve" else (
                    f"deny:{reason}" if reason else "deny"
                )
                tools = agent.get_workflow_tools()
                outcome = await resolve_workflow_approval(
                    workflow_id, decision,
                    tools=tools, settings=agent.settings,
                    workspace=agent.workspace, transport="cli",
                )
                # Review remediation: `report` is non-empty for the "not
                # currently awaiting approval" outcome too (ok=False,
                # reapproval_required=False), so gating the error branch on
                # `not outcome.report` let that failure fall through and
                # render as an ordinary success panel with outcome.message
                # never shown. ok=False + reapproval_required=False is
                # ALWAYS a hard failure regardless of what report/plan
                # happen to be populated -- report/plan are extra context,
                # not a success signal.
                if not outcome.ok and not outcome.reapproval_required:
                    _print_error(outcome.message)
                    continue
                if outcome.reapproval_required:
                    console.print(f"[yellow]{outcome.message}[/yellow]")
                console.print(Panel(outcome.report, border_style="gold3 dim"))
                continue

            _print_error(
                f"Bilinmeyen /workflow kullanımı: '{sub}'. "
                "Geçerli: list, show <id>, approve <id>, deny <id> [sebep]"
            )
            continue

        # ── /quota command (Faz 17) ─────────────────────────────────────────
        if lower in ("/quota", "/quota status", "/quota usage", "/quota forecast"):
            from jarvis.gcp_quota import quota_status, quota_usage_today, quota_forecast
            if "usage" in lower:
                content = quota_usage_today(settings)
            elif "forecast" in lower:
                content = quota_forecast(settings)
            else:
                content = quota_status(settings)
            console.print(Panel(
                content,
                title="[bold gold3]GCP / Vertex AI Kota[/bold gold3]",
                border_style="gold3 dim",
                padding=(0, 1),
            ))
            continue

        # ── /todo command (Faz 13-D) ────────────────────────────────────────
        if lower == "/todo" or lower.startswith("/todo "):
            sub = user_input[5:].strip() if len(lower) > 5 else ""
            store = agent.todo_store

            if not sub or sub == "list":
                tasks = store.list_open()
                if not tasks:
                    console.print(
                        "[dim]Açık to-do yok.[/dim]\n"
                        'JARVIS\'e söyle: [bold gold3]"Sismik rapor için to-do ekle"[/bold gold3]'
                    )
                else:
                    from jarvis.todo_store import PRIORITY_LABELS
                    table = Table(
                        show_header=True,
                        header_style="bold gold3",
                        border_style="dim",
                        title=f"[bold gold3]To-Do Listesi ({len(tasks)} açık)[/bold gold3]",
                        title_justify="left",
                    )
                    table.add_column("ID", style="dim", width=10)
                    table.add_column("Başlık", min_width=25)
                    table.add_column("Öncelik", width=20)
                    table.add_column("Kategori", width=10)
                    table.add_column("Bitiş", width=12)
                    for t in tasks:
                        pri = PRIORITY_LABELS.get(t.get("priority") or "", "⬜ bekliyor")
                        due = t.get("due_date") or "—"
                        cat = t.get("category") or "other"
                        table.add_row(t["id"], t["title"], pri, cat, due[:10])
                    console.print(table)
                continue

            if sub == "today":
                tasks = store.top_open(n=5)
                if not tasks:
                    console.print("[gold3]🌟 Tebrikler![/gold3] Bugün için açık görev yok.")
                else:
                    console.print(f"[bold gold3]🌟 Bugünün {len(tasks)} öncelikli görevi:[/bold gold3]")
                    from jarvis.todo_store import PRIORITY_LABELS
                    for i, t in enumerate(tasks, 1):
                        pri = PRIORITY_LABELS.get(t.get("priority") or "", "")
                        due = f"  [dim](📅 {t['due_date']})[/dim]" if t.get("due_date") else ""
                        console.print(f"  [bold]{i}.[/bold] [{t['id']}] {t['title']}{due}")
                        if pri:
                            console.print(f"      {pri}")
                        if t.get("instructions"):
                            for line in t["instructions"].split("\n")[:2]:
                                if line.strip():
                                    console.print(f"      [dim]{line.strip()}[/dim]")
                continue

            if sub == "analyze":
                with console.status("[gold3]Görevler analiz ediliyor...[/gold3]", spinner="dots"):
                    import asyncio
                    async def _analyze():
                        from jarvis.todo_analyzer import reanalyze_all
                        return await reanalyze_all(agent.settings, store)
                    try:
                        count = asyncio.run(_analyze())
                        console.print(f"[gold3]✓[/gold3] {count} görev yeniden önceliklendirildi.")
                    except Exception as e:
                        _print_error(f"Analiz hatası: {e}")
                continue

            # /todo add <title>
            if sub.startswith("add "):
                title = sub[4:].strip()
                if title:
                    tid = store.add(title)
                    console.print(f"[gold3]✓[/gold3] Eklendi [{tid}]: {title} [dim](AI analiz bekleniyor)[/dim]")
                else:
                    _print_error("Kullanım: /todo add <başlık>")
                continue

            _print_error(f"Bilinmeyen alt komut: '{sub}'. Geçerli: list, today, analyze, add <başlık>")
            continue

        # ── /schedule command (Faz 13-C) ────────────────────────────────────
        if lower == "/schedule" or lower.startswith("/schedule"):
            tasks = agent.scheduler.list_tasks(status="active")
            paused = agent.scheduler.list_tasks(status="paused")
            done   = agent.scheduler.list_tasks(status="done")

            if not tasks and not paused:
                console.print(
                    "[dim]Planlı görev yok.[/dim]\n"
                    'JARVIS\'e söyle veya doğrudan ekle: [bold gold3]schedule("add", title="...", '
                    'schedule_type="daily", run_at="09:00")[/bold gold3]'
                )
            else:
                if tasks:
                    table = Table(
                        show_header=True,
                        header_style="bold gold3",
                        border_style="dim",
                        title=f"[bold gold3]Aktif Görevler ({len(tasks)})[/bold gold3]",
                        title_justify="left",
                    )
                    table.add_column("ID", style="dim", width=10)
                    table.add_column("Başlık", min_width=20)
                    table.add_column("Tür", width=10)
                    table.add_column("Saat", width=6)
                    table.add_column("Sonraki", width=18)
                    _TYPE_LABELS = {
                        "once": "tek sef.", "daily": "günlük",
                        "weekly": "haftalık", "monthly": "aylık",
                    }
                    for t in tasks:
                        table.add_row(
                            t["id"],
                            t["title"],
                            _TYPE_LABELS.get(t["schedule_type"], t["schedule_type"]),
                            t["run_at"],
                            t["next_run"][:16],
                        )
                    console.print(table)
                if paused:
                    console.print(f"[dim]⏸ {len(paused)} görev duraklatıldı[/dim]")
                if done:
                    console.print(f"[dim]✅ {len(done)} görev tamamlandı[/dim]")
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
        confirmation: ConfirmationRequired | None = None
        with console.status("[gold3]Thinking…[/gold3]", spinner="dots"):
            try:
                response, model_label = await agent.chat(user_input, transport="cli-text")
            except ConfirmationRequired as cr:
                confirmation = cr
            except Exception as e:
                _print_error(f"Error: {e}")
                continue

        if confirmation is not None:
            await _handle_confirmation_cli(agent, confirmation.conf_id, confirmation.payload)
            continue

        _print_jarvis(response, model_label)


async def _run_voice_response(
    agent: JarvisAgent, engine, text: str, lang: str, settings: Settings,
    set_pending_confirmation=None, state=None,
) -> None:
    """One turn's response: agent.chat_stream() -> engine.speak_stream(), sentence-
    chunked so TTS starts before generation finishes. Runs as a cancellable task
    (see jarvis/voice/session.py) so a BargeIn event can interrupt it mid-flight.

    Faz 4 / BUG-4: if the graph interrupts for confirmation, chat_stream()
    yields the __jarvis_confirm__ marker as a single delta instead of real
    text -- previously that raw JSON was spoken verbatim. Now it's detected,
    swapped for a natural spoken question, and set_pending_confirmation()
    tells the enclosing loop the *next* transcript is the yes/no answer, not
    a new command (see jarvis/voice/session.py's resolve_confirmation).

    state (Faz D): drive_voice_session() already set response="thinking" the
    moment this turn's transcript arrived (before this function was even
    scheduled) -- this function's own job is only to flip to "speaking"
    around its own speak_stream() call.
    """
    from jarvis.voice.session import (
        arm_and_speak_confirmation, describe_progress, parse_confirm_marker,
        parse_final_marker, parse_progress_marker,
    )

    response_chunks: list[str] = []
    llm_error: list[BaseException] = []
    confirm_marker: dict | None = None

    async def _collecting_stream():
        nonlocal confirm_marker
        try:
            async for delta in agent.chat_stream(text, detected_language=lang, transport="voice-cli"):
                marker = parse_confirm_marker(delta)
                if marker is not None:
                    confirm_marker = marker
                    return
                # Completion-contract TTFB: a short, deterministic
                # acknowledgement instead of the silence a contracted+enforce
                # turn used to leave until the whole graph finished. Spoken
                # (yielded into the same TTS stream) but kept OUT of
                # response_chunks -- it must not be printed as if it were
                # part of JARVIS's answer, nor mistaken for one later.
                progress = parse_progress_marker(delta)
                if progress is not None:
                    yield describe_progress(progress, lang)
                    continue
                # Paket A: the terminal-answer correction. Swallowed here
                # rather than spoken: TTS has already said the superseded
                # sentences and there is no un-saying them, so reading the
                # replacement aloud would give the user the answer twice.
                # The correction still reaches history and memory, which is
                # where it changes what the NEXT turn believes.
                if parse_final_marker(delta) is not None:
                    return
                response_chunks.append(delta)
                yield delta
        except Exception as exc:
            # Deliberately Exception, not BaseException -- asyncio.CancelledError
            # (a barge-in cancelling this task) must propagate, never be swallowed.
            llm_error.append(exc)
            return

    try:
        if state is not None:
            state.set_response("speaking")
        await engine.speak_stream(_collecting_stream(), lang=lang)
    except Exception as exc:
        _print_error(f"TTS error: {exc}")

    if confirm_marker is not None:
        # Arm-before-speak via the shared helper (see arm_and_speak_confirmation):
        # set_pending_confirmation runs before the question TTS, so a barge-in
        # cancelling this turn mid-question still leaves the confirmation armed.
        try:
            await arm_and_speak_confirmation(
                engine, confirm_marker, lang,
                set_pending_confirmation=set_pending_confirmation,
                on_message=lambda q: console.print(
                    f"[bold yellow]JARVIS (confirmation):[/bold yellow] {q}"
                ),
                state=state,
            )
        except Exception as exc:
            _print_error(f"TTS error: {exc}")
        return

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


async def _run_voice_loop(
    agent: JarvisAgent, wakeword: bool = False, ptt: bool = False, monitor=None,
) -> None:
    try:
        from jarvis.voice.engine import RealtimeVoiceEngine
        from jarvis.voice.io_duplex import DuplexAudioIO
        from jarvis.voice.wakeword import WakewordDetector
        from jarvis.voice.text import is_exit_phrase
        from jarvis.voice.session import (
            drive_voice_session, STOP_SESSION, PendingConfirmation, resolve_confirmation,
        )
        from jarvis.voice.diagnostics import (
            clear_session as clear_voice_session, register_session as register_voice_session,
        )
    except ImportError as exc:
        _print_error(
            f"Voice dependencies not installed: {exc}\n"
            "Run: pip install -r requirements.txt"
        )
        return

    settings = agent.settings
    # Faz 5: connect MCP servers (Playwright, etc.) now, on this function's
    # own long-lived loop -- see JarvisAgent.connect_mcp_tools()'s docstring.
    await agent.connect_mcp_tools()
    agent.run_startup_backfill()
    engine = RealtimeVoiceEngine(DuplexAudioIO(settings), settings)
    loop = asyncio.get_running_loop()

    _print_banner(settings, monitor_active=monitor is not None)
    if ptt:
        # Faz E: press-to-ARM, not real hold-to-talk key-down/up -- Enter
        # starts listening for one utterance, VAD still ends it on silence.
        # A deliberately simpler, more reliable alternative to continuous
        # listen/wake-word while voice-input capture reliability is still
        # being hardened (see ROADMAP.md's P0).
        console.print('[gold3]Push-to-talk mode.[/gold3] Press [bold]Enter[/bold], then speak.')
    elif wakeword:
        console.print('[gold3]Wake-word mode.[/gold3] Say "[bold]Hey JARVIS[/bold]" to activate, then speak.')
    else:
        console.print("[gold3]Voice mode active.[/gold3] Speak naturally — JARVIS listens automatically.")
    console.print("[dim]Say 'goodbye' / 'güle güle' to exit.  Ctrl+C also works.[/dim]")
    console.print("[dim]You can interrupt JARVIS mid-sentence by speaking — barge-in is on.[/dim]\n")

    console.print("[dim]Loading voice models (first run downloads several hundred MB)...[/dim]")
    await engine.load()
    _print_voice_diagnostics(engine)

    ww_detector = None
    if ptt:
        # Deliberately simpler than the wake-word/PTT race in voice_api.py:
        # this mode never touches the wake-word model at all.
        console.print("[gold3]Ready.[/gold3]\n")
    elif wakeword:
        console.print("[dim]Loading wake-word model (hey_jarvis)...[/dim]")
        ww_detector = WakewordDetector()
        ok = await loop.run_in_executor(None, ww_detector.load)
        if ok:
            console.print('[gold3]Ready.[/gold3] Waiting for "Hey JARVIS"...\n')
        else:
            console.print("[yellow]Wake-word model unavailable — falling back to continuous listen.[/yellow]\n")
            wakeword = False
    else:
        console.print("[gold3]Ready.[/gold3]\n")

    pending_confirmation: "PendingConfirmation | None" = None

    def _set_pending(p: "PendingConfirmation | None") -> None:
        nonlocal pending_confirmation
        pending_confirmation = p

    # Faz D (voice observability): one shared, orchestration-owned VoiceState
    # per session -- drive_voice_session() mutates its capture axis;
    # _run_voice_response()/arm_and_speak_confirmation()/resolve_confirmation()
    # (all reached from _handle_transcript below) mutate its response axis.
    # See jarvis/voice/state.py's module docstring for why this lives here and
    # not in the engine. mic_level fires once per audio frame (far too often
    # to print each time) -- the latest value is only ever surfaced piggybacked
    # on the state line's own, much rarer, redraw trigger.
    _telemetry = {"mic_rms": 0.0}

    def _on_mic_level(event) -> None:
        _telemetry["mic_rms"] = event.rms

    def _render_voice_state(s) -> None:
        label = _VOICE_STATE_LABELS.get(s.display, s.display)
        console.print(f"[dim]{label} (mic {_telemetry['mic_rms']:.3f})[/dim]", end="\r")

    voice_state = VoiceState(on_change=_render_voice_state)

    # Make this session readable by anything else in the process that wants a
    # snapshot (jarvis/voice/diagnostics.py). Registered once, for the
    # session's whole lifetime -- cleared in the outer finally below.
    register_voice_session(engine, voice_state)

    async def _handle_transcript(text: str, lang: str):
        nonlocal pending_confirmation
        console.print(f"[bold blue]{settings.user_name}:[/bold blue] {text}   ")

        # Faz 4 / BUG-4: a pending confirmation always consumes the *next*
        # utterance as its yes/no answer -- checked before exit-phrase/model-
        # switch detection so e.g. "hayır" during a pending confirmation
        # denies it rather than being misread as an unrelated command.
        # Follow-up finding (2026-07-23): the earlier is_confirmation_still_
        # pending() pre-check here was NOT atomic against the Electron HUD
        # (or another client) resolving the SAME conf_id via /chat/confirm
        # in between -- this coroutine is only scheduled via
        # asyncio.ensure_future by drive_voice_session, not run inline, so a
        # real gap exists between "check" and resume_and_stream()'s own pop.
        # claim_pending_confirmation() is a single synchronous dict.pop()
        # (no `await` inside it, so nothing else can interleave on this
        # event loop) -- claim FIRST, then only proceed if we actually got
        # it, passing the claimed dict through so it's never looked up
        # (and never race-popped) a second time.
        if pending_confirmation is not None:
            pending, pending_confirmation = pending_confirmation, None
            claimed = agent.claim_pending_confirmation(pending.conf_id)
            if claimed is not None:

                async def _resolve():
                    await resolve_confirmation(
                        agent, engine, pending, text, lang,
                        on_message=lambda full: _print_jarvis(full, agent.current_model_label),
                        set_pending_confirmation=_set_pending,
                        pre_claimed=claimed,
                        state=voice_state,
                    )

                return _resolve()
            # Resolved elsewhere or TTL-evicted -- fall through as a new turn.

        if is_exit_phrase(text):
            farewell = "Goodbye, Sir." if lang != "tr" else "Güle güle, efendim."
            console.print(f"[dim]JARVIS:[/dim] {farewell}")

            async def _single(t=farewell):
                yield t

            await engine.speak_stream(_single(), lang)
            return STOP_SESSION

        detected_switch = _detect_model_switch(text)
        if detected_switch:
            try:
                label = agent.switch_model(detected_switch)
                msg = f"Model değiştirildi: {label}"
                console.print(f"[gold3]✓[/gold3] {msg}")

                async def _single(t=msg):
                    yield t

                await engine.speak_stream(_single(), lang)
            except ValueError as e:
                _print_error(str(e))
            return None

        return _run_voice_response(agent, engine, text, lang, settings, _set_pending, voice_state)

    def _on_barge_in() -> None:
        console.print("[dim](interrupted)[/dim]")

    try:
        while True:
            if ptt:
                # The press-to-arm gate doubles as the only typed-input point
                # in voice mode, so it's also where /voice-status lives: type
                # it instead of pressing a bare Enter and the mic stays shut
                # while the snapshot prints. Anything else (including the
                # bare Enter that is the normal case) arms as usual.
                console.print(
                    "[dim]Press Enter to speak (or /voice-status)...[/dim]", end="\r",
                )
                typed = (await loop.run_in_executor(None, input) or "").strip().lower()
                if typed in ("/voice-status", "/voice_status", "/status"):
                    _print_voice_status(engine, voice_state)
                    continue
                console.print("[gold3]Listening...[/gold3]              ")
            elif wakeword and ww_detector is not None:
                console.print('[dim]Waiting for "Hey JARVIS"...[/dim]', end="\r")
                await loop.run_in_executor(None, ww_detector.listen)
                console.print("[gold3]Hey! Listening...[/gold3]              ")

            await engine.start()
            try:
                outcome = await drive_voice_session(
                    engine, _handle_transcript,
                    on_barge_in=_on_barge_in,
                    on_mic_level=_on_mic_level,
                    state=voice_state,
                    stop_after_first_turn=wakeword or ptt,
                )
            finally:
                await engine.stop()

            if outcome == "exit" or not (wakeword or ptt):
                break
            # outcome == "turn_complete" and wakeword/ptt=True: loop back and
            # re-gate (wake phrase, or the next Enter press) for the next command.
    except KeyboardInterrupt:
        console.print("\n[dim]JARVIS offline. Goodbye.[/dim]")
    finally:
        clear_voice_session()
        await agent.close_mcp_tools()  # Faz 5: don't leave a launched browser process behind


def run(voice: bool = False, wakeword: bool = False, ptt: bool = False, monitor: bool = False) -> None:
    settings = Settings()
    # Only a MISSING credential that the config actually intends to use is worth
    # warning about. Under the project's local-first default (CLOUD_POLICY=off)
    # no cloud call can happen at all, so this used to warn about an unset
    # GEMINI_API_KEY on a perfectly healthy, fully-local install — training the
    # owner to ignore a startup warning, which is worse than printing nothing.
    if (
        getattr(settings, "cloud_policy", "auto") != "off"
        and not settings.gemini_api_key
        and not settings.use_vertex
    ):
        console.print(
            "[yellow]Warning:[/yellow] CLOUD_POLICY is not 'off' but no cloud tier is "
            "configured — set GEMINI_API_KEY or CLOUD_TIER=vertex in .env, or set "
            "CLOUD_POLICY=off to run fully local."
        )

    # Start background monitor daemon if requested
    monitor_instance = None
    agent = JarvisAgent(  # init first so scheduler is available
        settings, resume_session_id=_read_last_session_id(),
    )
    # Covers both cases: a valid id was resumed (no-op, already correct) and
    # no valid id existed so a fresh session was created (persist THIS one,
    # so the next launch resumes it instead of repeating the same miss).
    _write_last_session_id(agent.session_id)

    if monitor:
        from jarvis.monitor import JarvisMonitor
        monitor_instance = JarvisMonitor(
            settings, scheduler=agent.scheduler, todo_store=agent.todo_store, agent=agent
        )
        monitor_instance.start()
        console.print(
            f"[dim green]Monitor başlatıldı[/dim green] — "
            f"e-posta: her {settings.monitor_email_interval_min} dk  ·  "
            f"takvim: her {settings.monitor_calendar_interval_min} dk  ·  "
            f"önce {settings.monitor_calendar_lookahead_min} dk uyarı  ·  "
            f"zamanlayıcı: her {settings.monitor_schedule_interval_sec} sn[/dim green]"
        )
    try:
        if voice or wakeword or ptt:
            asyncio.run(_run_voice_loop(agent, wakeword=wakeword, ptt=ptt, monitor=monitor_instance))
        else:
            asyncio.run(_run_loop(agent, monitor=monitor_instance))
    except KeyboardInterrupt:
        console.print("\n[dim]JARVIS offline.[/dim]")
    finally:
        if monitor_instance:
            monitor_instance.stop()
