from __future__ import annotations

import json
from urllib.parse import urlencode
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


def get_book(token_id: str):
    return _get_json(f"{CLOB}/book?{urlencode({'token_id': token_id})}")


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
