"""The system prompt must tell the model what day it is.

Nothing did until 2026-07-30. The prompt committed to a timezone
(jarvis/prompts/core/05_memory_policy.md: "Timezone: Europe/Istanbul") but never
stated the current date, so every relative request -- "bu ay", "yarın", "geçen
hafta" -- was resolved against a date the model guessed from its training
distribution.

Measured: asked to analyse "hesabımdaki para akışını" on 2026-07-30, qwen3:8b
called finance('export', month=5) in 5 of 5 gate runs. The ledger held July data,
the export correctly answered "2026-05 için kayıtlı işlem yok", and the model
then reported to the user that the export had failed. Every tool defaulted to the
right period; the model overrode them with an invented one. The owner's live
calendar failure ("Yarın öğlen saat 3'e ... ekle") is the same family.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from jarvis.agent import _build_now_block

ISTANBUL = timezone(timedelta(hours=3))  # no DST since 2016, so this is exact


def test_states_todays_date():
    now = datetime.now(ISTANBUL)
    block = _build_now_block()

    assert f"{now:%Y-%m-%d}" in block


def test_states_the_current_month_as_year_and_month_numbers():
    """finance/schedule tools take year=/month= ints, so the block spells them out
    rather than leaving the model to parse a date string."""
    now = datetime.now(ISTANBUL)
    block = _build_now_block()

    assert f"{now:%Y-%m}" in block
    assert f"year={now.year}" in block
    assert f"month={now.month}" in block


def test_states_tomorrow_explicitly():
    """'yarın' is the single most common relative date in this user's requests and
    the one their live calendar failure turned on."""
    tomorrow = datetime.now(ISTANBUL) + timedelta(days=1)
    block = _build_now_block()

    assert f"{tomorrow:%Y-%m-%d}" in block


def test_names_the_turkish_weekday_and_month():
    block = _build_now_block()
    weekdays = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    months = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
              "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    now = datetime.now(ISTANBUL)

    assert weekdays[now.weekday()] in block
    assert months[now.month - 1] in block


def test_tells_the_model_not_to_guess_a_period():
    """The specific instruction that counters the observed failure: omit year/month
    and let the tool default, rather than inventing a month."""
    block = _build_now_block()

    assert re.search(r"OMIT|omit", block)
    assert "do not guess a month" in block


def test_contains_no_placeholder_or_unformatted_field():
    block = _build_now_block()

    assert "{" not in block and "}" not in block


def test_env_block_names_downloads_and_documents(tmp_path, monkeypatch):
    """Only Home and Desktop were listed, so asked for "indirilenlerdeki Hesap
    Hareketleri.pdf" the model invented `data/uploads/...` — a path that does not
    exist AND sits inside files.PROTECTED_DIRS, so it was refused twice over. A
    statement or an invoice lands in Downloads far more often than on the Desktop."""
    from jarvis.agent import _build_env_block

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    block = _build_env_block(tmp_path / "workspace")

    assert "Downloads" in block
    assert "indirilenler" in block
    assert "Documents" in block
    # And it must say not to invent one.
    assert "data/uploads" in block, "the observed wrong guess is named explicitly"


def test_env_block_downloads_stays_inside_the_isolation_root(tmp_path, monkeypatch):
    """Same rule as Desktop: under JARVIS_HOME the sandbox is the whole world, and
    leaking the real profile path into an 'isolated' prompt is the bug
    tests/test_jarvis_home.py already guards for Desktop."""
    from jarvis.agent import _build_env_block

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    block = _build_env_block(tmp_path / "workspace")

    assert str(tmp_path / "Downloads") in block


def test_the_env_block_is_recomputed_per_read(monkeypatch):
    """A stored string would freeze the date at construction. An API server or a
    wake-word session runs for days, and a confidently-stated stale date is worse
    than none, so JarvisAgent._env_block is a property."""
    from jarvis.agent import JarvisAgent

    prop = JarvisAgent.__dict__.get("_env_block")
    assert isinstance(prop, property), "_env_block must be a property, not an attribute"

    calls = []
    monkeypatch.setattr(
        "jarvis.agent._build_now_block", lambda: calls.append(1) or "NOW"
    )

    fake = object.__new__(JarvisAgent)
    fake._env_static = "STATIC"
    assert prop.fget(fake) == "STATICNOW"
    assert prop.fget(fake) == "STATICNOW"
    assert len(calls) == 2, "the clock must be read again on every access"
