from __future__ import annotations

import json
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def _get_json(url: str, timeout: float = 15.0):
    req = Request(url, headers={"User-Agent": "prediction-market-tournament/0.1"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def list_events(*, active: bool = True, closed: bool = False, limit: int = 100, offset: int = 0):
    q = urlencode({
        "active": str(active).lower(),
        "closed": str(closed).lower(),
        "limit": limit,
        "offset": offset,
    })
    return _get_json(f"{GAMMA}/events?{q}")


def list_markets(*, active: bool = True, closed: bool = False, limit: int = 100, offset: int = 0):
    q = urlencode({
        "active": str(active).lower(),
        "closed": str(closed).lower(),
        "limit": limit,
        "offset": offset,
    })
    return _get_json(f"{GAMMA}/markets?{q}")


def get_market_by_id(market_id: str):
    if not str(market_id).strip():
        raise ValueError("market_id cannot be empty")
    return _get_json(f"{GAMMA}/markets/{quote(str(market_id), safe='')}")


def get_book(token_id: str):
    return _get_json(f"{CLOB}/book?{urlencode({'token_id': token_id})}")


def get_clob_market_info(condition_id: str):
    if not str(condition_id).strip():
        raise ValueError("condition_id cannot be empty")
    return _get_json(f"{CLOB}/clob-markets/{quote(str(condition_id), safe='')}")


def market_fee_curve(condition_id: str) -> tuple[float, float]:
    """Return the live CLOB fee curve (rate, exponent) for a market.

    Polymarket exposes the authoritative per-market curve in the CLOB market
    info `fd` object. We deliberately do not infer a fee from category names
    when scoring a forward signal.
    """
    info = get_clob_market_info(condition_id)
    fd = info.get("fd")
    if not isinstance(fd, dict):
        raise LookupError("CLOB market fee details (fd) missing")
    try:
        rate = float(fd["r"])
        exponent = float(fd["e"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LookupError("CLOB market fee curve is incomplete") from exc
    if rate < 0 or exponent < 0:
        raise ValueError("CLOB fee rate/exponent must be non-negative")
    return rate, exponent


def get_event_by_slug(slug: str):
    return _get_json(f"{GAMMA}/events/slug/{slug}")


def market_buy_vwap(book: dict, stake_usd: float) -> float | None:
    """Executable average ask for spending stake_usd before platform fees.

    CLOB ask sizes are outcome shares. Partial use of the last price level is
    allowed. Returns None if displayed ask depth cannot absorb the full stake.
    """
    if stake_usd <= 0:
        raise ValueError("stake_usd must be > 0")
    asks = book.get("asks") or []
    levels: list[tuple[float, float]] = []
    for row in asks:
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
    for price, available_shares in levels:
        max_cost = price * available_shares
        use_cost = min(remaining, max_cost)
        use_shares = use_cost / price
        shares += use_shares
        spent += use_cost
        remaining -= use_cost
        if remaining <= 1e-9:
            break
    if remaining > 1e-7 or shares <= 0:
        return None
    return spent / shares


def parse_jsonish_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return json.loads(value)


def best_ask(book: dict) -> float | None:
    asks = book.get("asks") or []
    if not asks:
        return None
    return min(float(x["price"]) for x in asks)


def best_bid(book: dict) -> float | None:
    bids = book.get("bids") or []
    if not bids:
        return None
    return max(float(x["price"]) for x in bids)
