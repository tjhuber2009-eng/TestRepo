from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import AsyncIterator

from websockets.asyncio.client import connect

RTDS_URL = "wss://ws-live-data.polymarket.com"
TOPIC_RAW = "crypto_prices_chainlink"
TOPIC_TWAP60 = "crypto_prices_twap_sixty"
E18 = Decimal(10**18)


@dataclass(frozen=True)
class RtdsTick:
    topic: str
    symbol: str
    source_timestamp_ms: float
    value: Decimal
    receive_timestamp_ms: float
    window_seconds: int | None = None


def subscribe_frame(symbol: str = "btc/usd") -> str:
    """Build the production RTDS subscription using compact-string filters.

    RTDS requires filters for these topics to be a JSON STRING containing the
    compact lowercase symbol object, not a nested JSON object. This distinction
    is load-bearing for crypto_prices_twap_sixty.
    """
    wanted = symbol.strip().lower()
    if not wanted:
        raise ValueError("symbol cannot be empty")
    filters = json.dumps(
        {"symbol": wanted},
        separators=(",", ":"),
    )
    return json.dumps(
        {
            "action": "subscribe",
            "subscriptions": [
                {
                    "topic": TOPIC_RAW,
                    "type": "update",
                    "filters": filters,
                },
                {
                    "topic": TOPIC_TWAP60,
                    "type": "update",
                    "filters": filters,
                },
            ],
        },
        separators=(",", ":"),
    )


def server_error_message(raw) -> str | None:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str) or not raw.strip().startswith("{"):
        return None
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(message, dict):
        return None
    error = message.get("message") or message.get("error")
    if error and message.get("type") != "update":
        return str(error)
    return None


def _twap_value(payload: dict) -> Decimal:
    full = payload.get("full_accuracy_value")
    if not isinstance(full, str) or not full.lstrip("-").isdigit():
        raise ValueError("TWAP full_accuracy_value missing or invalid")
    return Decimal(int(full)) / E18


def parse_frame(raw, *, receive_timestamp_ms: float | None = None) -> RtdsTick | None:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str) or raw.strip() in {"", "PONG"}:
        return None
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("type") != "update":
        return None
    topic = msg.get("topic")
    if topic not in {TOPIC_RAW, TOPIC_TWAP60}:
        return None
    payload = msg.get("payload")
    if not isinstance(payload, dict):
        return None
    symbol = payload.get("symbol")
    if not isinstance(symbol, str):
        return None
    try:
        ts = float(payload["timestamp"])
        if topic == TOPIC_TWAP60:
            if payload.get("window_s") != 60:
                return None
            value = _twap_value(payload)
            window = 60
        else:
            value = Decimal(str(payload["value"]))
            window = None
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    if not math.isfinite(float(value)):
        return None
    return RtdsTick(
        topic=topic,
        symbol=symbol.lower(),
        source_timestamp_ms=ts,
        value=value,
        receive_timestamp_ms=receive_timestamp_ms or time.time() * 1000.0,
        window_seconds=window,
    )


async def stream_ticks(
    *,
    symbol: str = "btc/usd",
    ping_interval_s: float = 5.0,
    stall_timeout_s: float = 30.0,
) -> AsyncIterator[RtdsTick]:
    wanted = symbol.lower()
    attempt = 0
    while True:
        try:
            async with connect(
                RTDS_URL,
                ping_interval=None,
                open_timeout=15,
                close_timeout=5,
            ) as ws:
                await ws.send(subscribe_frame(wanted))
                next_ping = time.monotonic() + ping_interval_s
                last_data = time.monotonic()
                attempt = 0
                while True:
                    now = time.monotonic()
                    if now - last_data > stall_timeout_s:
                        raise ConnectionError("RTDS silent stall")
                    if now >= next_ping:
                        await ws.send("PING")
                        next_ping = now + ping_interval_s
                    timeout = max(0.05, min(1.0, next_ping - now))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        continue
                    server_error = server_error_message(raw)
                    if server_error is not None:
                        raise ConnectionError(
                            f"RTDS server error: {server_error}"
                        )
                    tick = parse_frame(raw)
                    if tick is None or tick.symbol != wanted:
                        continue
                    last_data = time.monotonic()
                    yield tick
        except asyncio.CancelledError:
            raise
        except Exception:
            attempt += 1
            await asyncio.sleep(min(30.0, 0.5 * (2 ** min(attempt, 6))))
