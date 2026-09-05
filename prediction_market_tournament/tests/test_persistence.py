import pytest

import scripts.persist_forward_git as persist


def _patch_git_diff(monkeypatch, statuses, stats):
    monkeypatch.setattr(
        persist,
        "_staged_status",
        lambda repo_root: dict(statuses),
    )
    monkeypatch.setattr(
        persist,
        "_staged_numstat",
        lambda repo_root: dict(stats),
    )


def test_forward_jsonl_append_is_allowed(monkeypatch, tmp_path):
    path = "prediction_market_tournament/data/signals.jsonl"
    _patch_git_diff(
        monkeypatch,
        {path: "M"},
        {path: (3, 0)},
    )
    persist._verify_append_only_forward_data(tmp_path, [path])


def test_new_forward_jsonl_is_allowed(monkeypatch, tmp_path):
    path = "prediction_market_tournament/data/signals.jsonl"
    _patch_git_diff(
        monkeypatch,
        {path: "A"},
        {path: (3, 0)},
    )
    persist._verify_append_only_forward_data(tmp_path, [path])


def test_forward_jsonl_history_rewrite_is_rejected(monkeypatch, tmp_path):
    path = "prediction_market_tournament/data/signals.jsonl"
    _patch_git_diff(
        monkeypatch,
        {path: "M"},
        {path: (1, 1)},
    )
    with pytest.raises(RuntimeError, match="append-only"):
        persist._verify_append_only_forward_data(tmp_path, [path])


def test_forward_jsonl_deletion_is_rejected(monkeypatch, tmp_path):
    path = "prediction_market_tournament/data/signals.jsonl"
    _patch_git_diff(
        monkeypatch,
        {path: "D"},
        {path: (0, 12)},
    )
    with pytest.raises(RuntimeError, match="append-only"):
        persist._verify_append_only_forward_data(tmp_path, [path])


def test_forward_start_marker_can_only_be_added(monkeypatch, tmp_path):
    path = "prediction_market_tournament/data/forward_start_v1.json"

    _patch_git_diff(
        monkeypatch,
        {path: "A"},
        {path: (1, 0)},
    )
    persist._verify_append_only_forward_data(tmp_path, [path])

    _patch_git_diff(
        monkeypatch,
        {path: "M"},
        {path: (1, 1)},
    )
    with pytest.raises(RuntimeError, match="immutable"):
        persist._verify_append_only_forward_data(tmp_path, [path])


def test_derived_leaderboard_may_be_replaced_but_not_deleted(
    monkeypatch,
    tmp_path,
):
    path = "prediction_market_tournament/data/leaderboard.json"
    _patch_git_diff(
        monkeypatch,
        {path: "M"},
        {path: (4, 4)},
    )
    persist._verify_append_only_forward_data(tmp_path, [path])

    _patch_git_diff(
        monkeypatch,
        {path: "D"},
        {path: (0, 10)},
    )
    with pytest.raises(RuntimeError, match="may not be deleted"):
        persist._verify_append_only_forward_data(tmp_path, [path])


def test_unknown_forward_data_artifact_is_rejected(monkeypatch, tmp_path):
    path = "prediction_market_tournament/data/manual_override.csv"
    _patch_git_diff(
        monkeypatch,
        {path: "A"},
        {path: (1, 0)},
    )
    with pytest.raises(RuntimeError, match="unrecognized"):
        persist._verify_append_only_forward_data(tmp_path, [path])
