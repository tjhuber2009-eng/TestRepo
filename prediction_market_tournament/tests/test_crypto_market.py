from datetime import datetime, timezone

import pytest

import tournament.crypto_market as crypto
from tournament.adapters.polymarket import MarketExecutionRules


START_S = 1788600000
START_MS = START_S * 1000.0


def _event_market():
    event = {
        "slug": f"btc-updown-5m-{START_S}",
        "title": "BTC Up or Down 5m",
        "description": (
            "Resolves from Chainlink BTC/USD TWAP data stream "
            "btc-usd-twap-60s"
        ),
        "resolutionSource": (
            "https://data.chain.link/streams/"
            "btc-usd-twap-60s-streams"
        ),
    }
    market = {
        "id": "123",
        "conditionId": "0xabc",
        "question": "BTC Up or Down 5m",
        "outcomes": '["Up","Down"]',
        "clobTokenIds": '["up-token","down-token"]',
        "active": True,
        "closed": False,
        "acceptingOrders": True,
    }
    event["markets"] = [market]
    return event, market


def _raw_points(observed_ms: float, *, center: float, direction: float):
    points = []
    for i in range(121):
        timestamp = observed_ms - (120 - i) * 1000.0
        # Vary increments so the volatility estimator is non-zero.
        wobble = (i % 4) * 0.03
        price = center + direction * i * 0.04 + wobble
        points.append((timestamp, price))
    return points


def _cfg(entry_seconds, min_edge=0.04):
    return {
        "entry_seconds_remaining": entry_seconds,
        "checkpoint_max_lag_seconds": 3,
        "min_edge": min_edge,
        "twap_window_seconds": 60,
        "volatility_lookback_seconds": 120,
        "minimum_raw_price_points": 30,
        "raw_spot_max_age_seconds": 3,
        "min_fair_probability": 0.92,
    }


def _patch_books(monkeypatch, *, up=0.50, down=0.50, fee_rate=0.0):
    monkeypatch.setattr(
        crypto,
        "market_execution_rules",
        lambda _: MarketExecutionRules(
            fee_rate=fee_rate,
            fee_exponent=1.0,
            min_order_shares=1.0,
        ),
    )
    books = {
        "up-token": {
            "min_order_size": "1",
            "asks": [{"price": str(up), "size": "1000"}],
        },
        "down-token": {
            "min_order_size": "1",
            "asks": [{"price": str(down), "size": "1000"}],
        },
    }
    monkeypatch.setattr(crypto, "get_book", lambda token: books[token])


def test_btc_slug_requires_exact_five_minute_alignment():
    assert crypto.btc_5m_slug(START_S) == f"btc-updown-5m-{START_S}"
    with pytest.raises(ValueError):
        crypto.btc_5m_slug(START_S + 1)


def test_market_validation_rejects_non_twap_rules():
    event, market = _event_market()
    event["description"] = "spot close"
    event["resolutionSource"] = "https://example.com/spot"
    with pytest.raises(ValueError, match="60s TWAP"):
        crypto.validate_btc_5m_market(
            event,
            market,
            expected_start_epoch_seconds=START_S,
        )


def test_twap_checkpoint_selects_up_from_exact_executable_books(monkeypatch):
    event, market = _event_market()
    _patch_books(monkeypatch, up=0.45, down=0.55, fee_rate=0.07)

    checkpoint_ms = START_MS + 180_000.0
    observed_ms = checkpoint_ms + 1_000.0
    signal = crypto.crypto_signal_from_market(
        market,
        event=event,
        lane="crypto_twap_taker",
        strike=100.0,
        raw_points=_raw_points(
            observed_ms,
            center=99.5,
            direction=0.02,
        ),
        window_start_ms=START_MS,
        observed_at=datetime.fromtimestamp(
            observed_ms / 1000.0,
            tz=timezone.utc,
        ),
        lane_cfg=_cfg(120),
        size_usd=5.0,
    )
    assert signal is not None
    assert signal.side == "UP"
    assert signal.executed_shares is not None
    assert signal.entry_fee_usd is not None
    assert signal.metadata["event_slug"] == f"btc-updown-5m-{START_S}"


def test_twap_checkpoint_rejects_stale_raw_spot(monkeypatch):
    event, market = _event_market()
    _patch_books(monkeypatch)

    checkpoint_ms = START_MS + 180_000.0
    observed_ms = checkpoint_ms + 1_000.0
    points = _raw_points(
        observed_ms - 5_000.0,
        center=100.0,
        direction=0.01,
    )
    signal = crypto.crypto_signal_from_market(
        market,
        event=event,
        lane="crypto_twap_taker",
        strike=100.0,
        raw_points=points,
        window_start_ms=START_MS,
        observed_at=datetime.fromtimestamp(
            observed_ms / 1000.0,
            tz=timezone.utc,
        ),
        lane_cfg=_cfg(120),
        size_usd=5.0,
    )
    assert signal is None


def test_late_resolution_requires_observed_final_window_segment(monkeypatch):
    event, market = _event_market()
    _patch_books(monkeypatch, up=0.20, down=0.80)

    checkpoint_ms = START_MS + 270_000.0
    observed_ms = checkpoint_ms + 1_000.0
    points = _raw_points(
        observed_ms,
        center=100.0,
        direction=0.04,
    )
    signal = crypto.crypto_signal_from_market(
        market,
        event=event,
        lane="crypto_late_resolution",
        strike=100.0,
        raw_points=points,
        window_start_ms=START_MS,
        observed_at=datetime.fromtimestamp(
            observed_ms / 1000.0,
            tz=timezone.utc,
        ),
        lane_cfg=_cfg(30, min_edge=0.025),
        size_usd=5.0,
    )
    assert signal is not None
    assert signal.side == "UP"
    assert signal.metadata["known_window_mean"] is not None


def test_checkpoint_is_missed_after_frozen_lag(monkeypatch):
    event, market = _event_market()
    _patch_books(monkeypatch)

    checkpoint_ms = START_MS + 180_000.0
    observed_ms = checkpoint_ms + 4_000.0
    signal = crypto.crypto_signal_from_market(
        market,
        event=event,
        lane="crypto_twap_taker",
        strike=100.0,
        raw_points=_raw_points(
            observed_ms,
            center=100.0,
            direction=0.01,
        ),
        window_start_ms=START_MS,
        observed_at=datetime.fromtimestamp(
            observed_ms / 1000.0,
            tz=timezone.utc,
        ),
        lane_cfg=_cfg(120),
        size_usd=5.0,
    )
    assert signal is None
