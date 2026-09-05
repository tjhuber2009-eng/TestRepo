from datetime import date
from urllib.parse import parse_qs, urlparse

import tournament.adapters.aviation as aviation
import tournament.adapters.open_meteo as open_meteo


def test_station_location_reads_geojson_elevation(monkeypatch):
    aviation.station_location.cache_clear()
    monkeypatch.setattr(
        aviation,
        "_get_json",
        lambda _: {
            "features": [
                {
                    "geometry": {
                        "coordinates": [-118.408, 33.942]
                    },
                    "properties": {"elev": 38.0},
                }
            ]
        },
    )
    assert aviation.station_location("klax") == (
        33.942,
        -118.408,
        38.0,
    )


def test_station_location_allows_missing_elevation(monkeypatch):
    aviation.station_location.cache_clear()
    monkeypatch.setattr(
        aviation,
        "_get_json",
        lambda _: {
            "features": [
                {
                    "geometry": {
                        "coordinates": [-0.4543, 51.47]
                    },
                    "properties": {},
                }
            ]
        },
    )
    assert aviation.station_location("egll") == (
        51.47,
        -0.4543,
        None,
    )


def test_open_meteo_sends_exact_station_elevation(monkeypatch):
    open_meteo.fetch_temperature_ensemble.cache_clear()
    captured = {}

    def fake_get(url):
        captured["url"] = url
        return {"hourly": {"time": []}}

    monkeypatch.setattr(open_meteo, "_get_json", fake_get)
    open_meteo.fetch_temperature_ensemble(
        33.942,
        -118.408,
        date(2026, 9, 5),
        model="ncep_gefs025",
        unit="fahrenheit",
        timezone="auto",
        elevation=38.0,
    )
    query = parse_qs(urlparse(captured["url"]).query)
    assert query["elevation"] == ["38.0"]
    assert query["models"] == ["ncep_gefs025"]
