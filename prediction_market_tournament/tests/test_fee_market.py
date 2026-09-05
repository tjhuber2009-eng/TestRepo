import tournament.adapters.polymarket as pm
from tournament.fees import FEE_RATES


def test_current_sports_fallback_rate():
    assert FEE_RATES["sports"] == 0.03


def test_market_fee_curve_uses_fd(monkeypatch):
    monkeypatch.setattr(pm, "get_clob_market_info", lambda _: {"fd": {"r": 0.07, "e": 1, "to": True}})
    assert pm.market_fee_curve("0xabc") == (0.07, 1.0)


def test_market_fee_curve_rejects_missing_fd(monkeypatch):
    monkeypatch.setattr(pm, "get_clob_market_info", lambda _: {"tbf": 1000})
    try:
        pm.market_fee_curve("0xabc")
    except LookupError:
        pass
    else:
        raise AssertionError("missing fd must not silently fall back")
