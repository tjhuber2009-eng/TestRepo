from adapters import mql5

HTML = """
<table><thead><tr><th>#</th><th>Signals</th><th>Price</th><th>Growth</th><th>Subscribers</th><th>Funds</th><th>Balance</th><th>Weeks</th><th>Expert Advisors</th><th>Trades</th><th>Win %</th><th>Activity</th><th>PF</th><th>Expected Payoff</th><th>Drawdown</th><th>Leverage</th></tr></thead><tbody>
<tr><td>1</td><td><a href="/en/signals/12345">Free Alpha</a></td><td>Free</td><td>250%</td><td>5</td><td>1K USD</td><td>2K USD</td><td>52</td><td>100%</td><td>500</td><td>60%</td><td>20%</td><td>1.80</td><td>2.5 USD</td><td>12%</td><td>1:100</td></tr>
<tr><td>2</td><td><a href="/en/signals/67890">Paid Beta</a></td><td>30 USD per month</td><td>100%</td><td>1</td><td>0 USD</td><td>1K USD</td><td>20</td><td>50%</td><td>200</td><td>55%</td><td>10%</td><td>1.40</td><td>1 USD</td><td>20%</td><td>1:500</td></tr>
</tbody></table>
"""

DIV_HTML = """
<div id="signals-table" class="signals-table">
  <div class="row signal">
    <div class="cell rank">1</div>
    <div class="cell signal-name"><a class="signal-avatar" href="/en/signals/24680">Grid Alpha</a></div>
    <div class="cell price">Free</div>
    <div class="cell growth">321.5%</div>
    <div class="cell subscribers">17</div>
    <div class="cell funds">4K USD</div>
    <div class="cell balance">10K USD</div>
    <div class="cell weeks">104</div>
    <div class="cell expert-advisors">100%</div>
    <div class="cell trades">2345</div>
    <div class="cell win">61.2%</div>
    <div class="cell activity">24%</div>
    <div class="cell profit-factor">1.92</div>
    <div class="cell payoff">3.2 USD</div>
    <div class="cell drawdown">14.4%</div>
    <div class="cell leverage">1:100</div>
  </div>
</div>
"""


def test_parse_table_extracts_signal_metrics():
    rows = mql5.parse_table(HTML)
    assert len(rows) == 2
    a = rows[0]
    assert a["signal_id"] == "12345"
    assert a["free"] is True
    assert a["growth_pct"] == 250.0
    assert a["drawdown_pct"] == 12.0
    assert a["profit_factor"] == 1.8
    assert a["leverage"] == 100.0


def test_parse_current_div_grid_extracts_metrics():
    rows = mql5.parse_table(DIV_HTML)
    assert len(rows) == 1
    row = rows[0]
    assert row["signal_id"] == "24680"
    assert row["name"] == "Grid Alpha"
    assert row["free"] is True
    assert row["growth_pct"] == 321.5
    assert row["subscribers"] == 17
    assert row["weeks"] == 104.0
    assert row["trades"] == 2345
    assert row["win_rate_pct"] == 61.2
    assert row["profit_factor"] == 1.92
    assert row["drawdown_pct"] == 14.4
    assert row["leverage"] == 100.0


def test_div_and_table_duplicates_are_deduplicated():
    duplicate = DIV_HTML.replace("24680", "12345").replace("Grid Alpha", "Free Alpha")
    rows = mql5.parse_table(HTML + duplicate)
    assert len([r for r in rows if r["signal_id"] == "12345"]) == 1


def test_paid_signal_not_actionable():
    snap = mql5._normalize(mql5.parse_table(HTML)[1], "2026-09-03T00:00:00Z")
    assert snap.free is False
    assert snap.actionable is False
    assert snap.max_drawdown_pct == -20.0


def test_free_signal_still_requires_real_demo_verification():
    snap = mql5._normalize(mql5.parse_table(HTML)[0], "2026-09-03T00:00:00Z")
    assert snap.free is True
    assert snap.actionable is False
    assert "real-vs-demo" in snap.actionable_reason
