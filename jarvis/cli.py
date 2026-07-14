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
  [gold3]/status[/gold3]           Model + bellek + oturum istatistiklerini göster
  [gold3]/budget[/gold3]           Token kullanımı ve Vertex kredi tahmini
  [gold3]/quota[/gold3]            GCP kota + Vertex kullanım durumu (RPM, kredi, tahmin)
  [gold3]/monitor[/gold3]          Proaktif monitör durumunu göster
  [gold3]/todo[/gold3]             To-do listesi (alt: today, analyze, add <başlık>)
  [gold3]/schedule[/gold3]         Planlı görev ve hatırlatıcıları listele
  [gold3]/sessions[/gold3]         Son oturumları listele
  [gold3]/session[/gold3] [dim]<id>[/dim]     Geçmiş oturuma geç
  [gold3]/entities[/gold3]         Tanınan varlıkları (kişi/proje/dosya) listele
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

def _print_banner(settings: Settings, monitor_active: bool = False) -> None:
    console.print(BANNER)
    console.print(Rule(style="gold3 dim"))
    if settings.use_vertex:
        model_str = (
            f"Vertex AI  ·  fast: [bold]{settings.vertex_model_fast}[/bold]  "
            f"·  pro: [bold]{settings.vertex_model_primary}[/bold]"
        )
    else:
        model_str = f"Cloud: [bold]{settings.effective_cloud_model}[/bold]"
    monitor_str = "  ·  [green]monitor ✓[/green]" if monitor_active else ""
    console.print(
        f"[dim]  User: [bold]{settings.user_name}[/bold]  ·  {model_str}{monitor_str}[/dim]\n"
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
        _PROVIDER_LABELS = {"vertex": "Vertex", "aistudio": "AI Studio", "gemini": "Gemini", "groq": "Groq", "local": "Ollama"}
        provider_str = _PROVIDER_LABELS.get(provider, provider.title())
        table.add_row(str(i), label + active_mark, provider_str, desc)

    console.print(table)


# ── Main loops ─────────────────────────────────────────────────────────────────

async def _run_loop(agent: JarvisAgent, monitor=None) -> None:
    settings = agent.settings

    _print_banner(settings, monitor_active=monitor is not None)
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
            summaries_count = agent.memory.count_summaries()
            active = agent._active_model_id or settings.effective_cloud_model
            cost = agent.usage.session_cost
            ef_label = {
                "ollama": "Ollama nomic-embed-text (local)",
                "gemini": "Gemini text-embedding-004",
                "default": "default ONNX",
            }[agent.memory._embedding_backend]
            total_sessions = agent.session_store.total_sessions()
            total_entities = agent.session_store.total_entities()
            history_len = len(agent._history)
            console.print(
                f"[dim]Session ID:[/dim]      [bold]{agent.session_id}[/bold] [dim]({history_len} messages loaded)[/dim]\n"
                f"[dim]Total sessions:[/dim]  [bold]{total_sessions}[/bold]\n"
                f"[dim]Known entities:[/dim]  [bold]{total_entities}[/bold]\n"
                f"[dim]Summaries indexed:[/dim][bold]{summaries_count}[/bold]\n"
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
            old_id = agent.session_id
            agent.reset()
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
        with console.status("[gold3]Thinking…[/gold3]", spinner="dots"):
            try:
                response, model_label = await agent.chat(user_input)
            except Exception as e:
                _print_error(f"Error: {e}")
                continue

        _print_jarvis(response, model_label)


async def _run_voice_loop(agent: JarvisAgent, wakeword: bool = False, monitor=None) -> None:
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

    _print_banner(settings, monitor_active=monitor is not None)
    if wakeword:
        console.print('[gold3]Wake-word mode.[/gold3] Say "[bold]Hey JARVIS[/bold]" to activate, then speak.')
    else:
        console.print("[gold3]Voice mode active.[/gold3] Speak naturally — JARVIS listens automatically.")
    console.print("[dim]Say 'goodbye' / 'güle güle' to exit.  Ctrl+C also works.[/dim]\n")

    console.print("[dim]Loading voice models (first run downloads ~800 MB)...[/dim]")
    await loop.run_in_executor(None, voice.load)

    if wakeword:
        console.print("[dim]Loading wake-word model (hey_jarvis)...[/dim]")
        ok = await loop.run_in_executor(None, voice.load_wakeword)
        if ok:
            console.print('[gold3]Ready.[/gold3] Waiting for "Hey JARVIS"...\n')
        else:
            console.print("[yellow]Wake-word model unavailable — falling back to continuous listen.[/yellow]\n")
            wakeword = False
    else:
        console.print("[gold3]Ready.[/gold3]\n")

    while True:
        if wakeword:
            console.print('[dim]Waiting for "Hey JARVIS"...[/dim]', end="\r")
            try:
                await loop.run_in_executor(None, voice.listen_for_wakeword)
            except (KeyboardInterrupt, asyncio.CancelledError):
                console.print("\n[dim]JARVIS offline. Goodbye.[/dim]")
                break
            console.print("[gold3]Hey! Listening...[/gold3]              ")

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


def run(voice: bool = False, wakeword: bool = False, monitor: bool = False) -> None:
    settings = Settings()
    if not settings.gemini_api_key and not settings.use_vertex:
        console.print(
            "[yellow]Warning:[/yellow] GEMINI_API_KEY not set and Vertex AI not configured — "
            "set GEMINI_API_KEY or CLOUD_TIER=vertex in .env."
        )

    # Start background monitor daemon if requested
    monitor_instance = None
    agent = JarvisAgent(settings)  # init first so scheduler is available

    if monitor:
        from jarvis.monitor import JarvisMonitor
        monitor_instance = JarvisMonitor(
            settings, scheduler=agent.scheduler, todo_store=agent.todo_store
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
        if voice or wakeword:
            asyncio.run(_run_voice_loop(agent, wakeword=wakeword, monitor=monitor_instance))
        else:
            asyncio.run(_run_loop(agent, monitor=monitor_instance))
    except KeyboardInterrupt:
        console.print("\n[dim]JARVIS offline.[/dim]")
    finally:
        if monitor_instance:
            monitor_instance.stop()
