import json
from datetime import datetime, timezone

import pytest

from tournament.freeze import (
    create_forward_marker,
    implementation_hash,
    require_forward_started,
)


def _make_root(tmp_path):
    root = tmp_path / "prediction_market_tournament"
    (root / "config").mkdir(parents=True)
    (root / "tournament").mkdir()
    (root / "scripts").mkdir()
    (root / "config" / "frozen_v1.json").write_text(
        json.dumps(
            {
                "project": "prediction-market-tournament",
                "version": "PMT-FROZEN-V1",
            }
        ),
        encoding="utf-8",
    )
    (root / "tournament" / "strategy.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    (root / "scripts" / "scan_crypto_live.py").write_text(
        "print('scan')\n",
        encoding="utf-8",
    )
    (root / "deploy").mkdir()
    (root / "deploy" / "pmt-forward.service").write_text(
        "[Service]\nExecStart=python\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='pmt-test'\n",
        encoding="utf-8",
    )
    return root


def test_forward_marker_binds_spec_and_implementation(tmp_path):
    root = _make_root(tmp_path)
    expected_impl = implementation_hash(root)
    marker = create_forward_marker(
        root,
        started_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )
    assert marker["implementation_sha256"] == expected_impl
    assert require_forward_started(root) == marker

    (root / "tournament" / "strategy.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="implementation_sha256"):
        require_forward_started(root)


def test_forward_marker_rejects_preexisting_signal_ledger(tmp_path):
    root = _make_root(tmp_path)
    data = root / "data"
    data.mkdir()
    (data / "signals.jsonl").write_text(
        '{"kind":"signal"}\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="pre-existing forward data"):
        create_forward_marker(root)


def test_forward_marker_is_one_shot(tmp_path):
    root = _make_root(tmp_path)
    create_forward_marker(root)
    with pytest.raises(FileExistsError):
        create_forward_marker(root)


def test_implementation_hash_ignores_prestart_recovery_helpers(tmp_path):
    root = _make_root(tmp_path)
    before = implementation_hash(root)

    helper = root / "deploy" / "recover_oracle_a1.sh"
    helper.write_text("#!/bin/sh\necho one\n", encoding="utf-8")
    after_add = implementation_hash(root)
    helper.write_text("#!/bin/sh\necho two\n", encoding="utf-8")
    after_edit = implementation_hash(root)

    assert before == after_add == after_edit


def test_implementation_hash_tracks_live_service_unit(tmp_path):
    root = _make_root(tmp_path)
    before = implementation_hash(root)
    service = root / "deploy" / "pmt-forward.service"
    service.write_text(
        "[Service]\nExecStart=python changed.py\n",
        encoding="utf-8",
    )
    assert implementation_hash(root) != before
