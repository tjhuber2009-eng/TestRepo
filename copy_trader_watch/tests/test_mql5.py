from adapters import mql5

HTML = """
<table><thead><tr><th>#</th><th>Signals</th><th>Price</th><th>Growth</th><th>Subscribers</th><th>Funds</th><th>Balance</th><th>Weeks</th><th>Expert Advisors</th><th>Trades</th><th>Win %</th><th>Activity</th><th>PF</th><th>Expected Payoff</th><th>Drawdown</th><th>Leverage</th></tr></thead><tbody>
<tr><td>1</td><td><a href="/en/signals/12345">Free Alpha</a></td><td>Free</td><td>250%</td><td>5</td><td>1K USD</td><td>2K USD</td><td>52</td><td>100%</td><td>500</td><td>60%</td><td>20%</td><td>1.80</td><td>2.5 USD</td><td>12%</td><td>1:100</td></tr>
<tr><td>2</td><td><a href="/en/signals/67890">Paid Beta</a></td><td>30 USD per month</td><td>100%</td><td>1</td><td>0 USD</td><td>1K USD</td><td>20</td><td>50%</td><td>200</td><td>55%</td><td>10%</td><td>1.40</td><td>1 USD</td><td>20%</td><td>1:500</td></tr>
</tbody></table>
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
