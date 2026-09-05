from datetime import date

from tournament.weather_discovery import is_temperature_event, target_date_from_event


def test_temperature_event_filter():
    assert is_temperature_event({"title": "Highest temperature in Los Angeles on September 5?"})
    assert not is_temperature_event({"title": "Will Bitcoin go up?"})


def test_target_date_with_year_from_enddate():
    e = {
        "title": "Highest temperature in Los Angeles on September 5?",
        "endDate": "2026-09-05T23:59:00Z",
    }
    assert target_date_from_event(e) == date(2026, 9, 5)
