from __future__ import annotations

import json
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://aviationweather.gov/api/data"


def _get_json(url: str, timeout: float = 15.0):
    req = Request(
        url,
        headers={"User-Agent": "prediction-market-tournament/0.1"},
    )
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


@lru_cache(maxsize=256)
def station_location(
    station_id: str,
) -> tuple[float, float, float | None]:
    station_id = station_id.upper()
    q = urlencode({"ids": station_id, "format": "geojson"})
    payload = _get_json(f"{BASE}/stationinfo?{q}")
    features = payload.get("features") or []
    if not features:
        raise LookupError(f"station not found: {station_id}")

    feature = features[0]
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        raise LookupError(f"station has no coordinates: {station_id}")
    lon, lat = float(coords[0]), float(coords[1])

    properties = feature.get("properties") or {}
    raw_elevation = properties.get("elev")
    if raw_elevation in (None, ""):
        raw_elevation = properties.get("elevation")
    elevation = None
    if raw_elevation not in (None, ""):
        try:
            elevation = float(raw_elevation)
        except (TypeError, ValueError):
            elevation = None

    return lat, lon, elevation


def station_coordinates(station_id: str) -> tuple[float, float]:
    lat, lon, _ = station_location(station_id)
    return lat, lon


def station_elevation_m(station_id: str) -> float | None:
    _, _, elevation = station_location(station_id)
    return elevation
