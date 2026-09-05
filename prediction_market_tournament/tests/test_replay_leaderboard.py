from datetime import (
    datetime,
    timedelta,
    timezone,
)

from tournament.leaderboard import (
    build_equal_window_leaderboard,
)
from tournament.models import Signal
from tournament.scoring import (
    settle_binary_signal,
)


def _trade(
    i,
    start,
    retwin=True,
    resolve_minutes=10,
    lane="x",
):
    signal = Signal(
        f"s{i}",
        lane,
        f"m{i}",
        start,
        "YES",
        0.5,
        0.6,
        "taker",
        1.0,
        0.0,
    )
    trade = settle_binary_signal(
        signal,
        retwin,
        resolved_at=(
            start
            + timedelta(
                minutes=resolve_minutes
            )
        ),
    )
    return signal, trade


def test_equal_window_marks_short_sample_provisional():
    start = datetime(
        2026,
        9,
        1,
        tzinfo=timezone.utc,
    )
    signal, trade = _trade(
        1,
        start + timedelta(hours=1),
        True,
    )
    rows = (
        build_equal_window_leaderboard(
            [signal],
            [trade],
            window_start=start,
            as_of=(
                start
                + timedelta(days=5)
            ),
        )
    )
    assert len(rows) == 1
    assert rows[0].calendar_days == 5
    assert rows[0].provisional is True
    assert rows[0].net_return > 0


def test_concurrency_cap_skips_sixth_simultaneous_trade():
    start = datetime(
        2026,
        9,
        1,
        tzinfo=timezone.utc,
    )
    pairs = [
        _trade(
            i,
            start
            + timedelta(seconds=i),
            True,
            resolve_minutes=60,
        )
        for i in range(6)
    ]
    rows = (
        build_equal_window_leaderboard(
            [s for s, _ in pairs],
            [t for _, t in pairs],
            window_start=start,
            as_of=(
                start
                + timedelta(days=1)
            ),
            max_concurrent_positions=5,
        )
    )
    assert rows[0].admitted_trades == 5
    assert (
        rows[0].skipped_concurrency
        == 1
    )
