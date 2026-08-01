"""Weather via Open-Meteo — keyless, deterministic (Post-MVP Faz 3).

Open-Meteo was chosen over every alternative for one reason that matters more
here than accuracy: it needs **no API key**. Faz 3's acceptance is a briefing
that runs every morning without the owner having provisioned anything, and a
source that can be unavailable because a key expired is a source that turns
the briefing's honesty requirement into a recurring chore. A missing key is
also indistinguishable, from the model's side, from "the weather is unknown" —
and the whole point of BriefingFacts is that the difference between *unknown*
and *not fetched* is visible.

Two responsibilities, deliberately kept apart:

  * ``fetch_weather()`` returns a typed ``WeatherReport`` or raises
    ``WeatherUnavailable``. This is what jarvis/briefing.py consumes, because
    a briefing section needs to record *why* a source failed, not read a
    human sentence and guess.
  * ``weather_report()`` renders one string for the model-facing ``weather``
    tool, with the ``[ERROR]`` prefix on failure that
    ``tool_accounting.content_is_failure`` already recognizes (see
    jarvis/tools/calendar.py's note on why the prefix has to be that exact
    one and not a friendlier label).

Nothing here is generated: every number in the output came out of one HTTP
response, and the WMO code table below is a fixed translation of a published
enumeration. There is no path through this module where a temperature is
inferred, rounded from a guess, or carried over from a previous call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.config import Settings

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Istanbul. Overridable via Settings.weather_latitude/longitude/place, and
# per-call via the tool's `city` argument.
DEFAULT_LATITUDE = 41.0138
DEFAULT_LONGITUDE = 28.9497
DEFAULT_PLACE = "İstanbul"

DEFAULT_TIMEOUT_SEC = 8.0

# WMO 4677 present-weather codes, as documented by Open-Meteo. Turkish, since
# every user-facing string in this project is. Kept as a literal table rather
# than bucketed ranges so an unknown code stays honestly unknown instead of
# being rounded into a neighbour's description -- an invented condition is
# exactly the fabrication class Faz 3 is measured on.
_WMO_TR: dict[int, str] = {
    0: "açık",
    1: "az bulutlu",
    2: "parçalı bulutlu",
    3: "çok bulutlu",
    45: "sisli",
    48: "kırağılı sis",
    51: "hafif çisenti",
    53: "çisenti",
    55: "yoğun çisenti",
    56: "dondurucu hafif çisenti",
    57: "dondurucu çisenti",
    61: "hafif yağmurlu",
    63: "yağmurlu",
    65: "kuvvetli yağmurlu",
    66: "dondurucu hafif yağmur",
    67: "dondurucu yağmur",
    71: "hafif kar yağışlı",
    73: "kar yağışlı",
    75: "yoğun kar yağışlı",
    77: "kar taneli",
    80: "hafif sağanak yağışlı",
    81: "sağanak yağışlı",
    82: "şiddetli sağanak yağışlı",
    85: "hafif kar sağanaklı",
    86: "yoğun kar sağanaklı",
    95: "gök gürültülü fırtına",
    96: "dolulu gök gürültülü fırtına",
    99: "yoğun dolulu gök gürültülü fırtına",
}


class WeatherUnavailable(RuntimeError):
    """The forecast could not be retrieved. Carries the reason, because a
    briefing section reports WHY it is empty rather than just being empty."""


@dataclass(frozen=True)
class WeatherReport:
    """One place, one moment. Every field is either a value the API returned
    or None -- never a default standing in for a missing reading."""

    place: str
    latitude: float
    longitude: float
    observed_at: str          # API-local ISO timestamp, e.g. "2026-08-01T14:30"
    condition: str            # Turkish text for wmo_code, or "" if unmapped
    wmo_code: int | None
    temperature_c: float | None
    feels_like_c: float | None
    high_c: float | None
    low_c: float | None
    precipitation_probability_pct: int | None
    wind_kmh: float | None

    def as_line(self) -> str:
        """One line, suitable for a briefing fact list.

        Written so that every number a narrator could repeat is present here
        verbatim: jarvis/briefing.py's audit compares the model's numerals
        against exactly this text.
        """
        head = f"{self.place}: "
        if self.temperature_c is not None:
            head += f"{_num(self.temperature_c)}°C"
            if self.condition:
                head += f", {self.condition}"
        elif self.condition:
            head += self.condition
        else:
            head += "sıcaklık bilgisi yok"

        extras: list[str] = []
        if self.feels_like_c is not None:
            extras.append(f"hissedilen {_num(self.feels_like_c)}°C")
        if self.low_c is not None and self.high_c is not None:
            extras.append(f"gün içi {_num(self.low_c)}–{_num(self.high_c)}°C")
        if self.precipitation_probability_pct is not None:
            extras.append(f"yağış ihtimali %{self.precipitation_probability_pct}")
        if self.wind_kmh is not None:
            extras.append(f"rüzgâr {_num(self.wind_kmh)} km/s")
        if extras:
            head += " (" + ", ".join(extras) + ")"
        return head


def _num(value: float) -> str:
    """Render a float the way the API gave it: no trailing '.0' on integers.

    Matters beyond cosmetics. The briefing audit checks that every numeral the
    model uttered appears in the facts; "27.8" and "28" are different strings,
    so the facts must not carry a formatting variant the model would never
    reproduce.
    """
    rounded = round(float(value), 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _get(url: str, params: dict[str, Any], timeout: float) -> Any:
    """One HTTP GET, with every failure mode collapsed into WeatherUnavailable.

    httpx is already a declared dependency (requirements.txt), so this adds no
    install surface. Import is local because this module is imported at tool-
    registration time on every startup, including runs that never ask for
    weather.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover -- declared dependency
        raise WeatherUnavailable(f"httpx kurulu değil: {exc}") from exc

    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001 -- network, HTTP status, or bad JSON
        raise WeatherUnavailable(
            f"Open-Meteo isteği başarısız ({type(exc).__name__}): {exc}"
        ) from exc


def geocode(city: str, timeout: float = DEFAULT_TIMEOUT_SEC) -> tuple[float, float, str]:
    """City name → (latitude, longitude, canonical name).

    Also keyless. Only the FIRST result is used and its name is returned
    rather than the user's spelling, so a briefing that says "Ankara" is
    reporting the place Open-Meteo actually resolved -- if the geocoder
    matched something else, the output says so instead of silently answering
    for the wrong city.
    """
    name = (city or "").strip()
    if not name:
        raise WeatherUnavailable("Şehir adı boş.")
    payload = _get(
        GEOCODING_URL,
        {"name": name, "count": 1, "language": "tr", "format": "json"},
        timeout,
    )
    results = (payload or {}).get("results") or []
    if not results:
        raise WeatherUnavailable(f"'{name}' için konum bulunamadı.")
    first = results[0]
    try:
        return float(first["latitude"]), float(first["longitude"]), str(first.get("name") or name)
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherUnavailable(f"Konum yanıtı okunamadı: {exc}") from exc


def _first(seq: Any) -> Any:
    """First element of a daily-series list, or None.

    Open-Meteo returns `daily` as parallel arrays. `forecast_days=1` means one
    element each, but a defensive read costs nothing and an IndexError inside a
    briefing source would take out a section that had already succeeded.
    """
    if isinstance(seq, list) and seq:
        return seq[0]
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_weather(
    latitude: float = DEFAULT_LATITUDE,
    longitude: float = DEFAULT_LONGITUDE,
    place: str = DEFAULT_PLACE,
    tz_name: str = "Europe/Istanbul",
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> WeatherReport:
    """Current conditions plus today's high/low for one coordinate.

    `tz_name` is passed to Open-Meteo so `daily` covers the user's calendar
    day and `current.time` is a local reading -- asking for UTC and converting
    here would reintroduce exactly the split-timezone bug jarvis/clock.py
    exists to prevent (a "today" that means a different day than the calendar's
    "today" for three hours out of twenty-four).
    """
    payload = _get(
        FORECAST_URL,
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max"
            ),
            "timezone": tz_name,
            "forecast_days": 1,
        },
        timeout,
    )
    if not isinstance(payload, dict):
        raise WeatherUnavailable("Open-Meteo beklenmeyen bir yanıt döndürdü.")

    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    # The day's aggregate code describes the day the briefing is about; the
    # current code describes this minute. The daily one wins when both exist
    # because a briefing is a statement about today, and falls back to current
    # so a response missing `daily` still says something true.
    code = _as_int(_first(daily.get("weather_code")))
    if code is None:
        code = _as_int(current.get("weather_code"))

    return WeatherReport(
        place=place,
        latitude=_as_float(payload.get("latitude")) or float(latitude),
        longitude=_as_float(payload.get("longitude")) or float(longitude),
        observed_at=str(current.get("time") or ""),
        condition=_WMO_TR.get(code, "") if code is not None else "",
        wmo_code=code,
        temperature_c=_as_float(current.get("temperature_2m")),
        feels_like_c=_as_float(current.get("apparent_temperature")),
        high_c=_as_float(_first(daily.get("temperature_2m_max"))),
        low_c=_as_float(_first(daily.get("temperature_2m_min"))),
        precipitation_probability_pct=_as_int(
            _first(daily.get("precipitation_probability_max"))
        ),
        wind_kmh=_as_float(current.get("wind_speed_10m")),
    )


def fetch_for_settings(
    settings: "Settings | None" = None,
    city: str = "",
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> WeatherReport:
    """The configured home location, or a named city when one is given.

    This is the single place the settings→coordinates decision is made, so the
    briefing service and the model-facing tool cannot disagree about where
    "hava nasıl" means.
    """
    from jarvis.clock import DEFAULT_TZ

    tz_name = getattr(settings, "calendar_timezone", None) or DEFAULT_TZ
    if city.strip():
        latitude, longitude, resolved = geocode(city, timeout=timeout)
        return fetch_weather(latitude, longitude, resolved, tz_name, timeout)

    return fetch_weather(
        latitude=_as_float(getattr(settings, "weather_latitude", None)) or DEFAULT_LATITUDE,
        longitude=_as_float(getattr(settings, "weather_longitude", None)) or DEFAULT_LONGITUDE,
        place=str(getattr(settings, "weather_place", None) or DEFAULT_PLACE),
        tz_name=tz_name,
        timeout=timeout,
    )


def weather_report(city: str = "", settings: "Settings | None" = None) -> str:
    """Model-facing string for the `weather` tool."""
    try:
        report = fetch_for_settings(settings, city=city)
    except WeatherUnavailable as exc:
        return f"[ERROR] Hava durumu alınamadı: {exc}"
    stamp = f" (ölçüm {report.observed_at})" if report.observed_at else ""
    return f"[Weather] {report.as_line()}{stamp}"
