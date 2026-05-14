"""GCP quota & Vertex AI usage tracker for JARVIS (Faz 17).

Two tiers:
  1. Local   — always works; reads data/usage.json from UsageTracker,
               shows session/all-time token counts + estimated $ cost,
               cross-checks against configured vertex_credit_usd.
  2. Cloud   — optional; queries Cloud Monitoring API for live Vertex AI
               RPM/TPM quota metrics.  Requires google-cloud-monitoring
               and cloudquotas APIs enabled.  15-minute TTL cache at
               data/gcp_quota_cache.json.

Public API:
    quota_status(settings) -> str       rich-formatted bars (always fast)
    quota_usage_today(settings) -> str  today's token + $ breakdown
    quota_forecast(settings) -> str     linear spend extrapolation
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)

_CACHE_PATH = Path("data/gcp_quota_cache.json")
_CACHE_TTL_SEC = 900  # 15 minutes


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if _CACHE_PATH.exists():
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            ts = data.get("_ts", 0)
            age = datetime.now(timezone.utc).timestamp() - ts
            if age < _CACHE_TTL_SEC:
                return data
    except Exception:
        pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data["_ts"] = datetime.now(timezone.utc).timestamp()
        _CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Local usage (from UsageTracker / usage.json) ──────────────────────────────

def _load_usage_json() -> dict:
    path = Path("data/usage.json")
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _today_cost_estimate(settings: "Settings") -> dict:
    """Estimate today's usage from usage.json (all-time total is best we have locally)."""
    usage = _load_usage_json()
    total_cost_usd = usage.get("cost_usd", 0.0)
    tokens_in = usage.get("tokens_in", 0)
    tokens_out = usage.get("tokens_out", 0)
    flash_turns = usage.get("flash_turns", 0)
    pro_turns = usage.get("pro_turns", 0)
    # Estimate remaining credit
    credit_usd = getattr(settings, "vertex_credit_usd", 0.0)
    usd_per_tl = getattr(settings, "_usd_per_tl", 0.026)  # ~38.5 TL per USD (May 2026)
    remaining_usd = max(0.0, credit_usd - total_cost_usd)
    return {
        "total_cost_usd": total_cost_usd,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "flash_turns": flash_turns,
        "pro_turns": pro_turns,
        "credit_usd": credit_usd,
        "remaining_usd": remaining_usd,
    }


# ── Cloud Monitoring (optional) ────────────────────────────────────────────────

def _try_fetch_cloud_quotas(settings: "Settings") -> dict:
    """
    Try to fetch Vertex AI RPM quota usage via Cloud Monitoring API.
    Returns {} if library not installed or API call fails.

    Requires:
        pip install google-cloud-monitoring
        gcloud services enable monitoring.googleapis.com
    """
    cached = _load_cache()
    if cached.get("rpm_pro") is not None:
        return cached  # fresh cache hit

    try:
        from google.cloud import monitoring_v3
        from google.auth import default as gauth_default
    except ImportError:
        logger.debug("google-cloud-monitoring not installed — skipping cloud quota fetch")
        return {}

    project = getattr(settings, "google_cloud_project", "")
    if not project:
        return {}

    try:
        client = monitoring_v3.MetricServiceClient()
        project_name = f"projects/{project}"
        now = datetime.now(timezone.utc)
        # Query last 60 seconds
        interval = monitoring_v3.TimeInterval(
            {
                "end_time": {"seconds": int(now.timestamp()), "nanos": 0},
                "start_time": {"seconds": int(now.timestamp()) - 120, "nanos": 0},
            }
        )

        # Try to query Vertex AI quota usage metric
        # metric: aiplatform.googleapis.com/quota/generate_content_requests_per_minute_per_project_per_base_model/usage
        results: dict = {}
        metric_type = (
            "aiplatform.googleapis.com/quota/"
            "generate_content_requests_per_minute_per_project_per_base_model/usage"
        )
        try:
            response = client.list_time_series(
                request={
                    "name": project_name,
                    "filter": f'metric.type = "{metric_type}"',
                    "interval": interval,
                    "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                }
            )
            for ts in response:
                labels = dict(ts.metric.labels)
                model = labels.get("base_model", "unknown")
                if ts.points:
                    val = ts.points[-1].value.int64_value or ts.points[-1].value.double_value
                    if "flash" in model.lower():
                        results["rpm_flash_used"] = max(results.get("rpm_flash_used", 0), val)
                    elif "pro" in model.lower():
                        results["rpm_pro_used"] = max(results.get("rpm_pro_used", 0), val)
        except Exception as exc:
            logger.debug("Cloud Monitoring quota fetch error: %s", exc)

        # Try to fetch quota limits from Cloud Quotas API
        try:
            from google.cloud import cloudquotas_v1
            qclient = cloudquotas_v1.CloudQuotasClient()
            region = getattr(settings, "google_cloud_region", "europe-west1")
            parent = f"projects/{project}/locations/{region}/services/aiplatform.googleapis.com"
            for qi in qclient.list_quota_infos(parent=parent):
                name = qi.quota_id or ""
                if "generate_content_requests_per_minute" in name:
                    for dims in qi.dimensions_infos:
                        val = dims.details.value if dims.details else 0
                        if val:
                            if "flash" in name.lower():
                                results.setdefault("rpm_flash_limit", val)
                            elif "pro" in name.lower():
                                results.setdefault("rpm_pro_limit", val)
        except Exception as exc:
            logger.debug("Cloud Quotas API fetch error: %s", exc)

        if results:
            _save_cache(results)
        return results

    except Exception as exc:
        logger.debug("Cloud quota fetch outer error: %s", exc)
        return {}


# ── Bar rendering ──────────────────────────────────────────────────────────────

def _bar(pct: float, width: int = 10) -> str:
    """Render a simple ASCII progress bar (0.0-1.0 → filled/empty chars)."""
    filled = min(width, int(pct * width))
    color = "green" if pct < 0.6 else ("yellow" if pct < 0.85 else "red")
    bar = "█" * filled + "░" * (width - filled)
    return f"[{color}]{bar}[/{color}]"


# ── Public API ────────────────────────────────────────────────────────────────

def quota_status(settings: "Settings") -> str:
    """Return Rich-formatted quota status with bars."""
    usage = _today_cost_estimate(settings)
    cloud = _try_fetch_cloud_quotas(settings)

    lines = ["[bold gold3]⚡ GCP / Vertex AI Durum[/bold gold3]", ""]

    # RPM bars (cloud data if available)
    rpm_flash_used  = cloud.get("rpm_flash_used", None)
    rpm_flash_limit = cloud.get("rpm_flash_limit", 400)   # typical default
    rpm_pro_used    = cloud.get("rpm_pro_used", None)
    rpm_pro_limit   = cloud.get("rpm_pro_limit", 100)

    if rpm_flash_used is not None:
        pct = min(1.0, rpm_flash_used / max(1, rpm_flash_limit))
        lines.append(
            f"  Gemini Flash RPM  {_bar(pct)}  "
            f"[cyan]{pct:.0%}[/cyan] ({int(rpm_flash_used)}/{rpm_flash_limit})"
        )
    else:
        lines.append("  Gemini Flash RPM  [dim](Cloud Monitoring erişilemiyor)[/dim]")

    if rpm_pro_used is not None:
        pct = min(1.0, rpm_pro_used / max(1, rpm_pro_limit))
        lines.append(
            f"  Gemini Pro RPM    {_bar(pct)}  "
            f"[cyan]{pct:.0%}[/cyan] ({int(rpm_pro_used)}/{rpm_pro_limit})"
        )
    else:
        lines.append("  Gemini Pro RPM    [dim](Cloud Monitoring erişilemiyor)[/dim]")

    lines.append("")

    # Local cost tracking
    total_usd   = usage["total_cost_usd"]
    credit_usd  = usage["credit_usd"]
    remaining   = usage["remaining_usd"]

    if credit_usd > 0:
        pct_used = min(1.0, total_usd / credit_usd)
        pct_left = 1.0 - pct_used
        # Credit remaining in USD and estimated TRY (approx 38.5 TL per USD May 2026)
        tl_rate = 38.5
        remaining_tl = remaining * tl_rate
        credit_tl    = credit_usd * tl_rate
        lines.append(
            f"  Harcanan (toplam) {_bar(pct_used)}  "
            f"[yellow]${total_usd:.4f}[/yellow] / [dim]${credit_usd:.2f}[/dim]"
        )
        lines.append(
            f"  Kredi kalan       {_bar(pct_left)}  "
            f"[bold green]~₺{remaining_tl:,.0f}[/bold green] "
            f"[dim](₺{credit_tl:,.0f} toplam bütçe)[/dim]"
        )
    else:
        lines.append(
            f"  Harcanan (toplam) [yellow]${total_usd:.5f}[/yellow]  "
            "[dim](VERTEX_CREDIT_USD .env'de ayarlı değil)[/dim]"
        )

    lines.append("")
    lines.append(
        f"  [dim]Flash dönüş: {usage['flash_turns']:,}  "
        f"Pro dönüş: {usage['pro_turns']:,}  "
        f"Toplam token: {usage['tokens_in']+usage['tokens_out']:,}[/dim]"
    )

    cache_age = ""
    cache = _load_cache()
    if cache.get("_ts"):
        age_s = int(datetime.now(timezone.utc).timestamp() - cache["_ts"])
        cache_age = f"  [dim](Cloud cache: {age_s}s önce güncellendi)[/dim]"
    if cache_age:
        lines.append(cache_age)

    return "\n".join(lines)


def quota_usage_today(settings: "Settings") -> str:
    """Return today's token usage + cost breakdown."""
    usage = _today_cost_estimate(settings)
    total = usage["tokens_in"] + usage["tokens_out"]
    lines = [
        "[bold gold3]📊 Token Kullanımı (Kümülatif)[/bold gold3]",
        f"  Giriş token:   [cyan]{usage['tokens_in']:>12,}[/cyan]",
        f"  Çıkış token:   [cyan]{usage['tokens_out']:>12,}[/cyan]",
        f"  Toplam:        [cyan]{total:>12,}[/cyan]",
        f"  Flash dönüş:   [dim]{usage['flash_turns']:>4}[/dim]",
        f"  Pro dönüş:     [dim]{usage['pro_turns']:>4}[/dim]",
        f"  Tahmini maliyet: [yellow]${usage['total_cost_usd']:.5f}[/yellow]  "
        f"[dim](~{usage['total_cost_usd']*100:.3f} sent)[/dim]",
    ]
    return "\n".join(lines)


def quota_forecast(settings: "Settings") -> str:
    """Linear spend extrapolation to end of month."""
    usage = _today_cost_estimate(settings)
    total_usd = usage["total_cost_usd"]
    credit_usd = usage["credit_usd"]

    today = date.today()
    day_of_month = today.day
    # Estimate months of operation from usage.json timestamp or day of month
    usage_data = _load_usage_json()
    # If we have last_updated, compute daily rate
    daily_rate = 0.0
    last_updated = usage_data.get("last_updated", "")
    if last_updated:
        try:
            start_ts = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            days_elapsed = max(1, (datetime.now(timezone.utc) - start_ts).days + 1)
            daily_rate = total_usd / days_elapsed
        except Exception:
            pass
    if daily_rate == 0 and day_of_month > 0:
        daily_rate = total_usd / max(1, day_of_month)

    days_in_month = 30
    remaining_days = max(0, days_in_month - day_of_month)
    forecast_additional = daily_rate * remaining_days
    forecast_total = total_usd + forecast_additional
    tl_rate = 38.5

    lines = [
        "[bold gold3]🔮 Harcama Tahmini[/bold gold3]",
        f"  Günlük oran:   [yellow]${daily_rate:.5f}/gün[/yellow]",
        f"  Bu aya kadar:  [yellow]${total_usd:.4f}[/yellow]",
        f"  Ay sonu tahmin: [yellow]${forecast_total:.4f}[/yellow]  "
        f"[dim](~₺{forecast_total*tl_rate:.0f})[/dim]",
    ]
    if credit_usd > 0:
        months_remaining = max(0.0, (credit_usd - total_usd) / max(daily_rate * 30, 0.001))
        lines.append(
            f"  Kredi ömrü:    [bold green]~{months_remaining:.1f} ay[/bold green]  "
            f"[dim](${credit_usd:.2f} toplam)[/dim]"
        )
    return "\n".join(lines)


def quota_alert_check(settings: "Settings") -> list[str]:
    """Return a list of alert messages for over-threshold conditions. Empty = all OK."""
    alerts: list[str] = []
    cloud = _try_fetch_cloud_quotas(settings)
    usage = _today_cost_estimate(settings)

    rpm_alert = getattr(settings, "gcp_alert_rpm_pct", 0.8)
    spend_alert = getattr(settings, "gcp_alert_daily_spend_pct", 0.9)
    credit_low_tl = getattr(settings, "gcp_alert_credit_low_tl", 1000.0)
    tl_rate = 38.5

    # RPM alerts
    if cloud.get("rpm_flash_used") is not None:
        limit = cloud.get("rpm_flash_limit", 400)
        pct = cloud["rpm_flash_used"] / max(1, limit)
        if pct >= rpm_alert:
            alerts.append(
                f"⚠ Gemini Flash RPM %{pct*100:.0f} "
                f"({int(cloud['rpm_flash_used'])}/{limit})"
            )
    if cloud.get("rpm_pro_used") is not None:
        limit = cloud.get("rpm_pro_limit", 100)
        pct = cloud["rpm_pro_used"] / max(1, limit)
        if pct >= rpm_alert:
            alerts.append(
                f"⚠ Gemini Pro RPM %{pct*100:.0f} "
                f"({int(cloud['rpm_pro_used'])}/{limit})"
            )

    # Credit low alert
    if usage["credit_usd"] > 0:
        remaining_tl = usage["remaining_usd"] * tl_rate
        if remaining_tl < credit_low_tl:
            alerts.append(
                f"⚠ Vertex kredi azalıyor: ~₺{remaining_tl:,.0f} kaldı "
                f"(eşik ₺{credit_low_tl:,.0f})"
            )

    return alerts
