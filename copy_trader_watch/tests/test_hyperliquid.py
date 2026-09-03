from adapters import hyperliquid as hl


def test_period_metrics_uses_pnl_change_not_raw_deposit_jump():
    window = {"accountValueHistory": [[0, "100"], [1, "1100"]], "pnlHistory": [[0, "0"], [1, "10"]]}
    ret, dd = hl._period_metrics(window)
    assert round(ret, 8) == 10.0
    assert round(dd, 8) == 0.0


def test_period_drawdown_from_pnl_path():
    window = {"accountValueHistory": [[0, "100"], [1, "100"], [2, "100"]], "pnlHistory": [[0, "0"], [1, "20"], [2, "8"]]}
    ret, dd = hl._period_metrics(window)
    assert round(ret, 8) == 8.0
    assert round(dd, 8) == -10.0


def test_fill_stats():
    fills = [{"time": 0, "closedPnl": "10"}, {"time": 86_400_000, "closedPnl": "-5"}, {"time": 172_800_000, "closedPnl": "5"}, {"time": 172_800_001, "closedPnl": "0"}]
    stats = hl._fill_stats(fills)
    assert stats["trades"] == 4
    assert round(stats["win_rate_pct"], 8) == round(2 / 3 * 100, 8)
    assert round(stats["profit_factor"], 8) == 3.0
    assert round(stats["profit_concentration_pct"], 8) == round(10 / 15 * 100, 8)


def test_current_leverage():
    assert hl._current_leverage({"marginSummary": {"accountValue": "100", "totalNtlPos": "600"}}) == 6.0


def test_nonrolling_forward_primitives():
    assert hl._latest_value([[1, "12"], [2, "15"]]) == 15.0
    assert hl._current_account_value({"marginSummary": {"accountValue": "250", "totalNtlPos": "0"}}) == 250.0
