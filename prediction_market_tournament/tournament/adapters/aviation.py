from __future__ import annotations

import json
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://aviationweather.gov/api/data"


def _get_json(url: str, timeout: float = 15.0):
    req = Request(url, headers={"User-Agent": "prediction-market-tournament/0.1"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


@lru_cache(maxsize=256)
def station_coordinates(station_id: str) -> tuple[float, float]:
    station_id = station_id.upper()
    q = urlencode({"ids": station_id, "format": "geojson"})
    payload = _get_json(f"{BASE}/stationinfo?{q}")
    features = payload.get("features") or []
    if not features:
        raise LookupError(f"station not found: {station_id}")
    coords = (features[0].get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        raise LookupError(f"station has no coordinates: {station_id}")
    lon, lat = float(coords[0]), float(coords[1])
    return lat, lon
