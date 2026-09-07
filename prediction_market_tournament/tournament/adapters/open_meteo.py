from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"


def _get_json(url: str, timeout: float = 20.0):
    req = Request(url, headers={"User-Agent": "prediction-market-tournament/0.1"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


@lru_cache(maxsize=256)
def fetch_temperature_ensemble(
    latitude: float,
    longitude: float,
    start_date: date,
    end_date: date | None = None,
    *,
    model: str = "ncep_gefs025",
    unit: str = "fahrenheit",
    timezone: str = "auto",
    elevation: float | None = None,
):
    end_date = end_date or start_date
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m",
        "models": model,
        "temperature_unit": unit,
        "timezone": timezone,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    if elevation is not None:
        params["elevation"] = elevation
    q = urlencode(params)
    return _get_json(f"{ENSEMBLE_API}?{q}")


def member_daily_extremes(payload: dict, *, kind: str = "max") -> list[float]:
    if kind not in {"max", "min"}:
        raise ValueError("kind must be max or min")
    hourly = payload.get("hourly") or {}
    member_keys = [
        k for k, v in hourly.items()
        if k != "time" and k.startswith("temperature_2m") and isinstance(v, list)
    ]
    vals = []
    for key in member_keys:
        series = [float(x) for x in hourly[key] if x is not None]
        if series:
            vals.append(max(series) if kind == "max" else min(series))
    return vals


def bracket_probability(
    values: list[float],
    *,
    lower: float | None = None,
    upper: float | None = None,
    resolution_increment: float = 1.0,
) -> float:
    """Smoothed probability after mapping continuous forecasts to source bins.

    Polymarket daily-temperature rules currently resolve using whole-degree
    station history. Instead of Python rounding (which uses ties-to-even), use
    half-increment bin boundaries. For a 76-77 whole-degree bracket this means
    continuous model values in [75.5, 77.5) are treated as bracket-consistent.
    """
    if not values:
        raise ValueError("values cannot be empty")
    if resolution_increment <= 0:
        raise ValueError("resolution_increment must be > 0")

    half = resolution_increment / 2.0
    effective_lower = None if lower is None else lower - half
    effective_upper = None if upper is None else upper + half

    hit = 0
    for x in values:
        if effective_lower is not None and x < effective_lower:
            continue
        if effective_upper is not None and x >= effective_upper:
            continue
        hit += 1
    return (hit + 0.5) / (len(values) + 1.0)
