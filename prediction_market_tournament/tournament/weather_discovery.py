from __future__ import annotations

import re
from datetime import date, datetime, timezone

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
