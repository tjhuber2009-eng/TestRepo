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


def _post_json(url: str, payload, timeout: float = 15.0):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "User-Agent": "prediction-market-tournament/0.1",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_server_time() -> float:
    value = _get_json(f"{CLOB}/time")
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as exc:
        raise LookupError("invalid CLOB server time response") from exc
    if timestamp <= 0:
        raise LookupError("invalid CLOB server timestamp")
    return timestamp


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


def get_books(token_ids: list[str]):
    clean = [str(token_id).strip() for token_id in token_ids]
    if not clean or any(not token_id for token_id in clean):
        raise ValueError("token_ids must contain non-empty token IDs")
    rows = _post_json(
        f"{CLOB}/books",
        [{"token_id": token_id} for token_id in clean],
    )
    if not isinstance(rows, list):
        raise LookupError("CLOB batch book response must be a list")
    return rows


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


def validate_book_identity(
    book: dict,
    *,
    token_id: str,
    condition_id: str,
) -> None:
    """Fail closed unless a CLOB book belongs to the requested market/token."""
    market = str(book.get("market") or "").strip()
    asset_id = str(book.get("asset_id") or "").strip()
    if not market or market != str(condition_id):
        raise ValueError(
            f"order book market mismatch: expected={condition_id!r} got={market!r}"
        )
    if not asset_id or asset_id != str(token_id):
        raise ValueError(
            f"order book asset mismatch: expected={token_id!r} got={asset_id!r}"
        )


def market_buy_quote(
    book: dict,
    cash_budget_usd: float,
    *,
    fee_rate: float,
    fee_exponent: float = 1.0,
    min_order_shares: float = 0.0,
) -> MarketBuyQuote | None:
    """Executable BUY quote under a hard all-in cash budget.

    Ask sizes are outcome shares. Each consumed level uses its own fee curve,
    so the cash cost per share is::

        price + fee_rate * (price * (1-price)) ** fee_exponent

    The quote is rejected if displayed depth cannot absorb the entire cash
    budget or if the resulting share count is below either published minimum
    order size. This mirrors the V2 SDK principle that BUY notional must be
    reduced when fees would otherwise exceed available USDC.
    """
    if cash_budget_usd <= 0:
        raise ValueError("cash_budget_usd must be > 0")
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

    remaining_cash = cash_budget_usd
    shares = 0.0
    spent = 0.0
    fee = 0.0

    for price, available_shares in levels:
        fee_per_share = (
            fee_rate
            * ((price * (1.0 - price)) ** fee_exponent)
        )
        all_in_per_share = price + fee_per_share
        if all_in_per_share <= 0:
            continue

        max_all_in = available_shares * all_in_per_share
        use_all_in = min(remaining_cash, max_all_in)
        use_shares = use_all_in / all_in_per_share

        shares += use_shares
        spent += use_shares * price
        fee += use_shares * fee_per_share
        remaining_cash -= use_all_in

        if remaining_cash <= 1e-9:
            break

    if (
        remaining_cash > 1e-7
        or shares <= 0
        or shares + 1e-12 < effective_min_shares
    ):
        return None

    # Keep internal economics precise. USDC settlement itself is six-decimal,
    # but premature rounding here can create a paper account that spends more
    # than the frozen cash budget by a few micro-dollars.
    if spent + fee > cash_budget_usd + 1e-8:
        raise ArithmeticError("all-in BUY quote exceeded cash budget")

    return MarketBuyQuote(
        spent_usd=spent,
        shares=shares,
        average_price=spent / shares,
        fee_usd=fee,
    )


def market_buy_vwap(
    book: dict,
    cash_budget_usd: float,
    *,
    min_order_shares: float = 0.0,
) -> float | None:
    """Backward-compatible fee-free VWAP helper used by shadow scanners."""
    quote = market_buy_quote(
        book,
        cash_budget_usd,
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
