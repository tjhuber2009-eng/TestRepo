import pytest

from tournament.persistence_guard import verify_append_only_forward_data


def test_forward_jsonl_append_is_allowed():
    path = "prediction_market_tournament/data/signals.jsonl"
    verify_append_only_forward_data(
        [path],
        statuses={path: "M"},
        stats={path: (3, 0)},
    )


def test_new_forward_jsonl_is_allowed():
    path = "prediction_market_tournament/data/signals.jsonl"
    verify_append_only_forward_data(
        [path],
        statuses={path: "A"},
        stats={path: (3, 0)},
    )


def test_forward_jsonl_history_rewrite_is_rejected():
    path = "prediction_market_tournament/data/signals.jsonl"
    with pytest.raises(RuntimeError, match="append-only"):
        verify_append_only_forward_data(
            [path],
            statuses={path: "M"},
            stats={path: (1, 1)},
        )


def test_forward_jsonl_deletion_is_rejected():
    path = "prediction_market_tournament/data/signals.jsonl"
    with pytest.raises(RuntimeError, match="append-only"):
        verify_append_only_forward_data(
            [path],
            statuses={path: "D"},
            stats={path: (0, 12)},
        )


def test_forward_start_marker_can_only_be_added():
    path = "prediction_market_tournament/data/forward_start_v1.json"
    verify_append_only_forward_data(
        [path],
        statuses={path: "A"},
        stats={path: (1, 0)},
    )
    with pytest.raises(RuntimeError, match="immutable"):
        verify_append_only_forward_data(
            [path],
            statuses={path: "M"},
            stats={path: (1, 1)},
        )


def test_derived_leaderboard_may_be_replaced_but_not_deleted():
    path = "prediction_market_tournament/data/leaderboard.json"
    verify_append_only_forward_data(
        [path],
        statuses={path: "M"},
        stats={path: (4, 4)},
    )
    with pytest.raises(RuntimeError, match="may not be deleted"):
        verify_append_only_forward_data(
            [path],
            statuses={path: "D"},
            stats={path: (0, 10)},
        )


def test_unknown_forward_data_artifact_is_rejected():
    path = "prediction_market_tournament/data/manual_override.csv"
    with pytest.raises(RuntimeError, match="unrecognized"):
        verify_append_only_forward_data(
            [path],
            statuses={path: "A"},
            stats={path: (1, 0)},
        )
