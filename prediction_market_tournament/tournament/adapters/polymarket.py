from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


@dataclass(frozen=True)
class MarketExecutionRules:
    fee_rate: float
    fee_exponent: float
    min_order_shares: float


@dataclass(frozen=True)
class MarketBuyQuote:
    spent_usd: float
    shares: float
    average_price: float
    fee_usd: float

    @property
    def all_in_cost_per_share(self) -> float:
        return (self.spent_usd + self.fee_usd) / self.shares


def _get_json(url: str, timeout: float = 15.0):
    req = Request(
        url,
        headers={"User-Agent": "prediction-market-tournament/0.1"},
    )
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_events(
    *,
    active: bool = True,
    closed: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    query = urlencode(
        {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset,
        }
    )
    return _get_json(f"{GAMMA}/events?{query}")


def list_markets(
    *,
    active: bool = True,
    closed: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    query = urlencode(
        {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset,
        }
    )
    return _get_json(f"{GAMMA}/markets?{query}")


def get_market_by_id(market_id: str):
    if not str(market_id).strip():
        raise ValueError("market_id cannot be empty")
    encoded = quote(str(market_id), safe="")
    return _get_json(f"{GAMMA}/markets/{encoded}")


def get_book(token_id: str):
    query = urlencode({"token_id": token_id})
    return _get_json(f"{CLOB}/book?{query}")


def get_clob_market_info(condition_id: str):
    if not str(condition_id).strip():
        raise ValueError("condition_id cannot be empty")
    encoded = quote(str(condition_id), safe="")
    return _get_json(f"{CLOB}/clob-markets/{encoded}")


def market_execution_rules(condition_id: str) -> MarketExecutionRules:
    info = get_clob_market_info(condition_id)
    fee_details = info.get("fd")
    if not isinstance(fee_details, dict):
        raise LookupError("CLOB market fee details (fd) missing")
    try:
        rate = float(fee_details["r"])
        exponent = float(fee_details["e"])
        min_order_shares = float(info["mos"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LookupError("CLOB execution rules are incomplete") from exc

    if rate < 0 or exponent < 0 or min_order_shares < 0:
        raise ValueError("CLOB execution parameters must be non-negative")
    return MarketExecutionRules(
        fee_rate=rate,
        fee_exponent=exponent,
        min_order_shares=min_order_shares,
    )


def market_fee_curve(condition_id: str) -> tuple[float, float]:
    rules = market_execution_rules(condition_id)
    return rules.fee_rate, rules.fee_exponent


def get_event_by_slug(slug: str):
    return _get_json(f"{GAMMA}/events/slug/{quote(slug, safe='')}")


def parse_jsonish_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return json.loads(value)


def _book_min_order_shares(book: dict) -> float:
    raw = book.get("min_order_size")
    if raw in (None, ""):
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid book min_order_size") from exc
    if value < 0:
        raise ValueError("book min_order_size must be non-negative")
    return value


def market_buy_quote(
    book: dict,
    stake_usd: float,
    *,
    fee_rate: float,
    fee_exponent: float = 1.0,
    min_order_shares: float = 0.0,
) -> MarketBuyQuote | None:
    """Full-stake executable BUY quote with price-level fee integration.

    Ask sizes are outcome shares. The quote is rejected if displayed depth
    cannot absorb the full stake or if the filled share count is below either
    the CLOB market-info minimum or the order-book minimum.

    Fees are integrated over consumed price levels. This matters because the
    Polymarket fee curve is nonlinear in price; applying it only to the final
    VWAP can misstate a multi-level fill.
    """
    if stake_usd <= 0:
        raise ValueError("stake_usd must be > 0")
    if fee_rate < 0 or fee_exponent < 0:
        raise ValueError("fee rate/exponent must be >= 0")
    if min_order_shares < 0:
        raise ValueError("min_order_shares must be >= 0")

    effective_min_shares = max(min_order_shares, _book_min_order_shares(book))
    levels: list[tuple[float, float]] = []
    for row in book.get("asks") or []:
        try:
            price = float(row["price"])
            size = float(row["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < price <= 1 and size > 0:
            levels.append((price, size))
    levels.sort()

    remaining = stake_usd
    shares = 0.0
    spent = 0.0
    fee = 0.0
    for price, available_shares in levels:
        max_cost = price * available_shares
        use_cost = min(remaining, max_cost)
        use_shares = use_cost / price
        shares += use_shares
        spent += use_cost
        fee += use_shares * fee_rate * ((price * (1.0 - price)) ** fee_exponent)
        remaining -= use_cost
        if remaining <= 1e-9:
            break

    if (
        remaining > 1e-7
        or shares <= 0
        or shares + 1e-12 < effective_min_shares
    ):
        return None

    return MarketBuyQuote(
        spent_usd=spent,
        shares=shares,
        average_price=spent / shares,
        fee_usd=round(fee, 5),
    )


def market_buy_vwap(
    book: dict,
    stake_usd: float,
    *,
    min_order_shares: float = 0.0,
) -> float | None:
    """Backward-compatible fee-free VWAP helper used by shadow scanners."""
    quote = market_buy_quote(
        book,
        stake_usd,
        fee_rate=0.0,
        min_order_shares=min_order_shares,
    )
    return None if quote is None else quote.average_price


def best_ask(book: dict) -> float | None:
    asks = book.get("asks") or []
    if not asks:
        return None
    return min(float(row["price"]) for row in asks)


def best_bid(book: dict) -> float | None:
    bids = book.get("bids") or []
    if not bids:
        return None
    return max(float(row["price"]) for row in bids)
