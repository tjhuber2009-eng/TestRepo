from adapters import collective2 as c2


HTML = """
<div class="strategy-card">
  <h2><a href="/details/usa-stocks">USA STOCKS</a></h2>
  <div>20.5% Annual Return</div>
  <div>(18.1%) Maximum drawdown</div>
  <div>Strategy age 1042.82</div>
  <div>0.81 Sharpe ratio</div>
  <div>59.62% Profitable</div>
  <div>0.733 Average Leverage</div>
  <div>1.25 :1 W:L Ratio</div>
  <div>$300,000 Suggested Capital</div>
  <div>$0 / month Subscription fee</div>
  <div>since Oct 27, 2023</div>
</div>
<div class="strategy-card">
  <h2><a href="/details/paid-alpha">Paid Alpha</a></h2>
  <div>66.4% Annual Return</div>
  <div>(29.7%) Maximum drawdown</div>
  <div>Strategy age 900</div>
  <div>1.60 Sharpe ratio</div>
  <div>58% Profitable</div>
  <div>1.5 Average Leverage</div>
  <div>1.1 :1 W:L Ratio</div>
  <div>$50,000 Suggested Capital</div>
  <div>$75 / month Subscription fee</div>
</div>
"""


def test_parse_collective2_free_strategy():
    rows = c2.parse_page(HTML, "https://collective2.com/lb/320")
    assert len(rows) == 2
    usa = next(r for r in rows if r["name"] == "USA STOCKS")
    assert usa["free"] is True
    assert usa["monthly_fee_usd"] == 0
    assert usa["return_pct"] == 20.5
    assert usa["max_drawdown_pct"] == 18.1
    assert usa["age_days"] == 1042.82
    assert usa["sharpe"] == 0.81
    assert usa["win_rate_pct"] == 59.62
    assert usa["average_leverage"] == 0.733
    assert usa["suggested_capital"] == 300000


def test_free_strategy_can_pass_actionable_screen_without_age_gate():
    row = next(r for r in c2.parse_page(HTML, "https://collective2.com/lb/320") if r["name"] == "USA STOCKS")
    snap = c2.normalize(row, "2026-09-03T00:00:00Z")
    assert snap.free is True
    assert snap.us_access == "conditional"
    assert snap.actionable is True
    assert snap.max_drawdown_pct == -18.1
    assert snap.metadata["suggested_capital"] == 300000
    assert "broker-sponsored" in snap.actionable_reason


def test_young_free_strategy_is_not_rejected_for_sample_size():
    row = next(r for r in c2.parse_page(HTML, "https://collective2.com/lb/320") if r["name"] == "USA STOCKS")
    row = dict(row)
    row["age_days"] = 3
    snap = c2.normalize(row, "2026-09-03T00:00:00Z")
    assert snap.actionable is True
    assert snap.forward_test_eligible is True


def test_paid_strategy_is_not_actionable():
    row = next(r for r in c2.parse_page(HTML, "https://collective2.com/lb/320") if r["name"] == "Paid Alpha")
    snap = c2.normalize(row, "2026-09-03T00:00:00Z")
    assert snap.free is False
    assert snap.actionable is False
    assert "75" in snap.actionable_reason


def test_high_suggested_capital_reduces_copyability_all_else_equal():
    row = next(r for r in c2.parse_page(HTML, "https://collective2.com/lb/320") if r["name"] == "USA STOCKS")
    lower_capital = dict(row)
    lower_capital["suggested_capital"] = 50_000
    assert c2._copyability(row) < c2._copyability(lower_capital)
