"""jarvis/gcp_quota.py -- BUG-17 and BUG-18 regressions.

BUG-17: _try_fetch_cloud_quotas()'s cache-hit check tested
cached.get("rpm_pro") is not None, but no code anywhere ever writes a key
literally named "rpm_pro" (only "rpm_pro_used"/"rpm_flash_used"/etc.) --
the check never fired, so a fresh cache was silently ignored and every call
tried a live Cloud Monitoring fetch.

BUG-18: quota_forecast()'s daily-rate math read usage.json's "last_updated",
which usage.py refreshes on every single save (effectively always "now") --
days_elapsed collapsed to 1 every time, so daily_rate became the entire
all-time cost total instead of a real per-day average. Fixed by anchoring on
"first_seen" (usage.py, set once via setdefault and never overwritten).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

from jarvis import gcp_quota

_NO_CREDIT_SETTINGS = SimpleNamespace(vertex_credit_usd=0.0)


def test_fresh_cache_is_returned_without_a_live_fetch(isolated_cwd):
    cache_path = Path("data/gcp_quota_cache.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"rpm_flash_used": 42, "_ts": time.time()}))

    result = gcp_quota._try_fetch_cloud_quotas(settings=None)

    # BUG-17: the old key check ("rpm_pro") never matched anything actually
    # written to the cache, so this would have fallen through to a live
    # fetch attempt (and returned {} once that failed/was unavailable)
    # instead of the cached value.
    assert result.get("rpm_flash_used") == 42


def test_stale_cache_is_not_returned(isolated_cwd):
    cache_path = Path("data/gcp_quota_cache.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stale_ts = time.time() - gcp_quota._CACHE_TTL_SEC - 60
    cache_path.write_text(json.dumps({"rpm_flash_used": 42, "_ts": stale_ts}))

    # _load_cache() itself already filters by TTL -- confirm a stale entry
    # still isn't treated as fresh after the BUG-17 fix (i.e. the fix didn't
    # overcorrect into "any cache file, regardless of age, is a hit").
    assert gcp_quota._load_cache() == {}


def test_forecast_daily_rate_uses_first_seen_not_last_updated(isolated_cwd):
    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    usage_path = Path("data/usage.json")
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(json.dumps({
        "cost_usd": 10.0, "tokens_in": 0, "tokens_out": 0,
        "flash_turns": 0, "pro_turns": 0,
        "first_seen": ten_days_ago, "last_updated": now,
    }))

    text = gcp_quota.quota_forecast(_NO_CREDIT_SETTINGS)

    # "$<rate>/gün" is unique to the daily-rate line -- the other two dollar
    # amounts in this output ("Bu aya kadar", "Ay sonu tahmin") have no
    # trailing slash. Rich markup tags sit between the label and the "$",
    # so don't anchor the pattern on the "oran:" label text itself.
    match = re.search(r"\$([\d.]+)/", text)
    assert match, text
    daily_rate = float(match.group(1))
    # BUG-18: reading "last_updated" (== now) would have given days_elapsed=1
    # and daily_rate == the full $10.0 total. With a real ~10-day-old
    # first_seen, the true rate is ~$10/11 =~ $0.91/day -- nowhere near $10.
    assert daily_rate < 2.0


def test_forecast_falls_back_to_day_of_month_without_first_seen(isolated_cwd):
    usage_path = Path("data/usage.json")
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(json.dumps({
        "cost_usd": 5.0, "tokens_in": 0, "tokens_out": 0,
        "flash_turns": 0, "pro_turns": 0,
    }))

    # Must not raise even with neither first_seen nor last_updated present
    # (e.g. a usage.json predating both fields).
    text = gcp_quota.quota_forecast(_NO_CREDIT_SETTINGS)
    assert "Harcama Tahmini" in text
