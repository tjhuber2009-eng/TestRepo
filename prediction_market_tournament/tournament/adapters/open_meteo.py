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
):
    end_date = end_date or start_date
    q = urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m",
        "models": model,
        "temperature_unit": unit,
        "timezone": timezone,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    })
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
) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    hit = 0
    for x in values:
        if lower is not None and x < lower:
            continue
        if upper is not None and x > upper:
            continue
        hit += 1
    return (hit + 0.5) / (len(values) + 1.0)
