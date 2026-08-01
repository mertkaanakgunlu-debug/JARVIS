"""Post-MVP Faz 3 — the Open-Meteo weather source.

No network. `_get` is the single seam every request in the module goes
through, so patching it covers the forecast call, the geocoding call and both
failure paths at once.

The response fixtures below are the SHAPE Open-Meteo actually returned on
2026-08-01, trimmed to the fields this module reads -- not a shape invented to
match the parser. A parser tested against its own author's idea of the API is
tested against nothing.
"""
from __future__ import annotations

import pytest

from jarvis.tools import weather as weather_tool
from jarvis.tools.weather import (
    WeatherReport,
    WeatherUnavailable,
    fetch_weather,
    geocode,
    weather_report,
)

FORECAST = {
    "latitude": 41.0,
    "longitude": 28.9375,
    "timezone": "Europe/Istanbul",
    "current": {
        "time": "2026-08-01T14:30",
        "temperature_2m": 27.8,
        "apparent_temperature": 26.9,
        "weather_code": 1,
        "wind_speed_10m": 27.8,
    },
    "daily": {
        "time": ["2026-08-01"],
        "weather_code": [3],
        "temperature_2m_max": [27.9],
        "temperature_2m_min": [21.8],
        "precipitation_probability_max": [0],
    },
}

GEO = {"results": [{"name": "Ankara", "latitude": 39.91987, "longitude": 32.85427}]}


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if anything tries to reach the internet from these tests."""
    def forbidden(*_args, **_kwargs):
        raise AssertionError("a weather test attempted a real HTTP call")

    monkeypatch.setattr(weather_tool, "_get", forbidden)
    return monkeypatch


def _serve(monkeypatch, payload, capture: dict | None = None):
    def fake_get(url, params, timeout):
        if capture is not None:
            capture["url"], capture["params"], capture["timeout"] = url, params, timeout
        return payload

    monkeypatch.setattr(weather_tool, "_get", fake_get)


# ── Reading the response ─────────────────────────────────────────────────────

def test_every_field_comes_from_the_response(no_network):
    _serve(no_network, FORECAST)
    report = fetch_weather()

    assert report.temperature_c == 27.8
    assert report.feels_like_c == 26.9
    assert report.high_c == 27.9
    assert report.low_c == 21.8
    assert report.precipitation_probability_pct == 0
    assert report.wind_kmh == 27.8
    assert report.observed_at == "2026-08-01T14:30"


def test_the_days_code_wins_over_this_minutes_code(no_network):
    """A briefing is a statement about today, so the DAILY aggregate describes
    it -- current says 1 (az bulutlu) while the day is 3 (çok bulutlu)."""
    _serve(no_network, FORECAST)
    report = fetch_weather()
    assert report.wmo_code == 3
    assert report.condition == "çok bulutlu"


def test_a_response_without_daily_still_says_something_true(no_network):
    _serve(no_network, {**FORECAST, "daily": {}})
    report = fetch_weather()
    assert report.wmo_code == 1
    assert report.condition == "az bulutlu"
    assert report.high_c is None and report.low_c is None


def test_an_unmapped_code_stays_honestly_blank(no_network):
    """Rounding an unknown WMO code into a neighbour's description would be
    inventing a condition -- exactly the class Faz 3 is measured on."""
    _serve(no_network, {**FORECAST, "daily": {**FORECAST["daily"], "weather_code": [7]}})
    report = fetch_weather()
    assert report.wmo_code == 7
    assert report.condition == ""


def test_missing_readings_are_none_not_zero(no_network):
    _serve(no_network, {"current": {}, "daily": {}})
    report = fetch_weather()
    assert report.temperature_c is None
    assert report.feels_like_c is None
    assert report.precipitation_probability_pct is None


def test_a_non_dict_response_is_a_failure_not_a_blank_report(no_network):
    _serve(no_network, ["unexpected"])
    with pytest.raises(WeatherUnavailable):
        fetch_weather()


# ── The rendered line ────────────────────────────────────────────────────────

def test_the_line_carries_every_number_a_narrator_could_repeat(no_network):
    _serve(no_network, FORECAST)
    line = fetch_weather().as_line()
    for value in ("27.8", "26.9", "21.8", "27.9", "%0"):
        assert value in line, line


def test_integers_render_without_a_trailing_zero(no_network):
    """"20" and "20.0" are different strings, and the briefing audit compares
    strings -- the facts must not carry a formatting variant the model would
    never reproduce."""
    _serve(no_network, {"current": {"temperature_2m": 20.0, "weather_code": 0}, "daily": {}})
    assert "20°C" in fetch_weather().as_line()


def test_a_report_with_no_temperature_says_so(no_network):
    _serve(no_network, {"current": {}, "daily": {}})
    assert "sıcaklık bilgisi yok" in fetch_weather().as_line()


def test_place_is_carried_through_untouched(no_network):
    _serve(no_network, FORECAST)
    assert fetch_weather(place="Kadıköy").as_line().startswith("Kadıköy:")


# ── Geocoding ────────────────────────────────────────────────────────────────

def test_geocode_returns_the_name_the_service_resolved(no_network):
    _serve(no_network, GEO)
    latitude, longitude, name = geocode("ankara")
    assert (round(latitude, 2), round(longitude, 2), name) == (39.92, 32.85, "Ankara")


def test_an_unresolvable_city_fails_rather_than_defaulting_home(no_network):
    """Silently answering for Istanbul when the user asked about somewhere
    else would be a confidently wrong reading, which is worse than no reading."""
    _serve(no_network, {"results": []})
    with pytest.raises(WeatherUnavailable, match="konum bulunamadı"):
        geocode("zzzqqq")


def test_an_empty_city_never_reaches_the_network(no_network):
    with pytest.raises(WeatherUnavailable):
        geocode("   ")


# ── Settings and the model-facing string ─────────────────────────────────────

def test_configured_coordinates_reach_the_request(no_network):
    class FakeSettings:
        weather_latitude = 39.9
        weather_longitude = 32.8
        weather_place = "Ankara"
        calendar_timezone = "Europe/Istanbul"

    captured: dict = {}
    _serve(no_network, FORECAST, captured)
    report = weather_tool.fetch_for_settings(FakeSettings())

    assert captured["params"]["latitude"] == 39.9
    assert captured["params"]["timezone"] == "Europe/Istanbul"
    assert report.place == "Ankara"


def test_the_tool_string_uses_the_prefix_the_audit_recognizes(no_network):
    """`[ERROR]`, not a friendlier label: tool_accounting.content_is_failure
    keys on that exact prefix, and calendar.py records what happens when a
    tool invents its own (a failed call logged as ok:true)."""
    def boom(*_a, **_k):
        raise WeatherUnavailable("bağlantı yok")

    no_network.setattr(weather_tool, "fetch_for_settings", boom)
    assert weather_report().startswith("[ERROR]")


def test_a_successful_tool_call_is_not_prefixed_as_a_failure(no_network):
    _serve(no_network, FORECAST)
    out = weather_report()
    assert out.startswith("[Weather]")
    assert "[ERROR]" not in out


def test_report_is_frozen():
    """Facts handed to a narrator must not be editable downstream."""
    report = WeatherReport(
        "İstanbul", 41.0, 28.9, "", "açık", 0, 20.0, 20.0, 21.0, 19.0, 0, 5.0
    )
    with pytest.raises(Exception):
        report.temperature_c = 99  # type: ignore[misc]
