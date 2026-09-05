import math

import tournament.adapters.polymarket as pm
from tournament.fees import FEE_RATES


def test_current_sports_fallback_rate():
    assert FEE_RATES["sports"] == 0.03


def test_market_fee_curve_uses_fd(monkeypatch):
    monkeypatch.setattr(
        pm,
        "get_clob_market_info",
        lambda _: {
            "mos": 5,
            "fd": {"r": 0.07, "e": 1, "to": True},
        },
    )
    assert pm.market_fee_curve("0xabc") == (0.07, 1.0)


def test_market_fee_curve_rejects_missing_fd(monkeypatch):
    monkeypatch.setattr(
        pm,
        "get_clob_market_info",
        lambda _: {"mos": 5, "tbf": 1000},
    )
    try:
        pm.market_fee_curve("0xabc")
    except LookupError:
        pass
    else:
        raise AssertionError("missing fd must not silently fall back")


def test_book_level_fee_is_integrated_at_each_price():
    book = {
        "min_order_size": "1",
        "asks": [
            {"price": "0.20", "size": "10"},
            {"price": "0.80", "size": "10"},
        ],
    }
    quote = pm.market_buy_quote(
        book,
        4.0,
        fee_rate=0.07,
        fee_exponent=1.0,
        min_order_shares=1.0,
    )
    assert quote is not None
    assert math.isclose(quote.shares, 12.32741617357002)
    assert math.isclose(quote.average_price, 0.31328)
    assert math.isclose(quote.fee_usd, 0.13806706114398423)
    assert math.isclose(quote.spent_usd + quote.fee_usd, 4.0)

    # A nonlinear fee applied only to VWAP would be materially wrong here.
    vwap_approx_fee = (
        quote.shares
        * 0.07
        * (quote.average_price * (1.0 - quote.average_price))
    )
    assert not math.isclose(quote.fee_usd, vwap_approx_fee, rel_tol=0.1)


def test_quote_enforces_book_and_market_minimum_order_size():
    book = {
        "min_order_size": "5",
        "asks": [{"price": "0.50", "size": "100"}],
    }
    assert (
        pm.market_buy_quote(
            book,
            0.50,
            fee_rate=0.0,
            min_order_shares=1.0,
        )
        is None
    )


def test_book_identity_must_match_condition_and_token():
    book = {
        "market": "0xabc",
        "asset_id": "token-1",
        "asks": [],
    }
    pm.validate_book_identity(
        book,
        token_id="token-1",
        condition_id="0xabc",
    )
    try:
        pm.validate_book_identity(
            book,
            token_id="wrong",
            condition_id="0xabc",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("mismatched asset id must fail closed")


def test_fee_enabled_quote_treats_budget_as_all_in_cash():
    book = {
        "min_order_size": "1",
        "asks": [{"price": "0.50", "size": "100"}],
    }
    quote = pm.market_buy_quote(
        book,
        5.0,
        fee_rate=0.07,
        fee_exponent=1.0,
        min_order_shares=1.0,
    )
    assert quote is not None
    assert quote.spent_usd < 5.0
    assert quote.fee_usd > 0.0
    assert math.isclose(quote.spent_usd + quote.fee_usd, 5.0)
