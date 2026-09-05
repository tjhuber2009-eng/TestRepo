from tournament.weather_market import extract_station_code, parse_temperature_bracket


def test_station_code():
    assert extract_station_code("https://www.weather.gov/wrh/timeseries?site=klax") == "KLAX"


def test_range_f():
    b = parse_temperature_bracket(
        "Will the highest temperature in Los Angeles be between 70-71°F on September 4?"
    )
    assert (b.kind, b.unit, b.lower, b.upper) == ("max", "F", 70, 71)


def test_low_tail():
    b = parse_temperature_bracket(
        "Will the lowest temperature in Denver be 55°F or below on September 4?"
    )
    assert b.kind == "min" and b.lower is None and b.upper == 55


def test_high_tail_c():
    b = parse_temperature_bracket(
        "Will the highest temperature in Madrid be 38°C or higher on September 4?"
    )
    assert b.kind == "max" and b.lower == 38 and b.upper is None
