from datetime import date, datetime, timezone

from tournament.weather_discovery import (
    is_temperature_event,
    target_date_from_event,
    target_date_in_scan_window,
)


def test_temperature_event_filter():
    assert is_temperature_event({"title": "Highest temperature in Los Angeles on September 5?"})
    assert not is_temperature_event({"title": "Will Bitcoin go up?"})


def test_target_date_with_year_from_enddate():
    e = {
        "title": "Highest temperature in Los Angeles on September 5?",
        "endDate": "2026-09-05T23:59:00Z",
    }
    assert target_date_from_event(e) == date(2026, 9, 5)


def test_scan_window_keeps_western_local_today_after_utc_midnight():
    now_utc = datetime(
        2026,
        9,
        6,
        0,
        30,
        tzinfo=timezone.utc,
    )
    assert target_date_in_scan_window(
        date(2026, 9, 5),
        now_utc=now_utc,
    )
    assert not target_date_in_scan_window(
        date(2026, 9, 4),
        now_utc=now_utc,
    )
