from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        [
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    )
    if name
}


def target_date_from_event(event: dict) -> date:
    title = str(event.get("title") or "")
    match = re.search(
        r"\bon\s+"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+(\d{1,2})(?:,?\s+(\d{4}))?",
        title,
        re.I,
    )
    end = str(event.get("endDate") or "")
    fallback_year = (
        int(end[:4])
        if len(end) >= 4 and end[:4].isdigit()
        else datetime.now(timezone.utc).year
    )
    if not match:
        if len(end) >= 10:
            return date.fromisoformat(end[:10])
        raise ValueError(f"cannot parse target date: {title}")
    return date(
        int(match.group(3) or fallback_year),
        MONTHS[match.group(1).lower()],
        int(match.group(2)),
    )


def is_temperature_event(event: dict) -> bool:
    title = str(event.get("title") or "").lower()
    return (
        "highest temperature in " in title
        or "lowest temperature in " in title
    )


def target_date_in_scan_window(
    target: date,
    *,
    now_utc: datetime,
    backward_days: int = 1,
    forward_days: int = 8,
) -> bool:
    """Broad UTC guard for station-local event dates worldwide."""
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    if backward_days < 0 or forward_days < 0:
        raise ValueError("scan window days must be non-negative")
    utc_date = now_utc.astimezone(timezone.utc).date()
    return (
        utc_date - timedelta(days=backward_days)
        <= target
        <= utc_date + timedelta(days=forward_days)
    )
