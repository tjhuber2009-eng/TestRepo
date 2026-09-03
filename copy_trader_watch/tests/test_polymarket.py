from adapters import polymarket as pm


def test_position_stats_cost_roi_and_pf():
    rows = [
        {"realizedPnl": 50, "totalBought": 100, "avgPrice": 0.5, "timestamp": 1_700_000_000},
        {"realizedPnl": -10, "totalBought": 100, "avgPrice": 0.5, "timestamp": 1_700_086_400},
        {"realizedPnl": 20, "totalBought": 200, "avgPrice": 0.5, "timestamp": 1_700_172_800},
    ]
    stats = pm.position_stats(rows)
    # Cost = 50 + 50 + 100 = 200; realized pnl = 60 => 30% cost ROI.
    assert round(stats["cost_roi_pct"], 8) == 30.0
    assert round(stats["profit_factor"], 8) == 7.0
    assert round(stats["win_rate_pct"], 8) == round(2 / 3 * 100, 8)
    assert round(stats["sample_age_days"], 8) == 2.0


def test_profit_concentration_detects_one_big_win():
    rows = [
        {"realizedPnl": 90, "totalBought": 100, "avgPrice": 0.5},
        {"realizedPnl": 10, "totalBought": 100, "avgPrice": 0.5},
    ]
    stats = pm.position_stats(rows)
    assert stats["profit_concentration_pct"] == 90.0


def test_open_concentration():
    top1, top2 = pm.open_concentration([
        {"currentValue": 60}, {"currentValue": 30}, {"currentValue": 10}
    ])
    assert top1 == 60.0
    assert top2 == 90.0


def test_copyability_rewards_persistence_and_penalizes_concentration():
    stats = {"closed_positions": 200, "closed_positions_per_day": 1, "profit_concentration_pct": 10}
    persistent = pm.copyability_score(stats, 20, True)
    transient = pm.copyability_score(stats, 20, False)
    concentrated = pm.copyability_score(stats, 80, True)
    assert persistent > transient
    assert concentrated < persistent
