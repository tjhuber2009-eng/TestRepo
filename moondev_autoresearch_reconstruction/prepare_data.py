"""Download public Binance 6-hour candles into the local data cache."""

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

BASE = "https://api.binance.com/api/v3/klines"
DEFAULT_START = "2017-08-17"


def ms(dt):
    return int(dt.timestamp() * 1000)


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def fetch(symbol, start_ms, end_ms=None):
    cursor = start_ms
    rows = []
    while True:
        params = {
            "symbol": symbol,
            "interval": "6h",
            "startTime": cursor,
            "limit": 1000,
        }
        if end_ms is not None:
            params["endTime"] = end_ms
        url = BASE + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "moondev-autoresearch-reconstruction/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            batch = json.loads(response.read().decode("utf-8"))
        if not batch:
            break

        rows.extend(batch)
        nxt = int(batch[-1][0]) + 6 * 60 * 60 * 1000
        if nxt <= cursor:
            break
        cursor = nxt

        print(
            f"{symbol}: {len(rows)} bars through "
            f"{datetime.fromtimestamp(batch[-1][0] / 1000, tz=timezone.utc)}"
        )
        if len(batch) < 1000:
            break
        time.sleep(0.15)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="ETH")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    asset = args.asset.upper()
    symbol = asset if asset.endswith("USDT") else asset + "USDT"
    rows = fetch(
        symbol,
        ms(parse_date(args.start)),
        ms(parse_date(args.end)) if args.end else None,
    )
    if not rows:
        raise SystemExit(f"No Binance candles returned for {symbol}")

    out = DATA / f"{asset.removesuffix('USDT')}_6h.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        for x in rows:
            dt = datetime.fromtimestamp(
                int(x[0]) / 1000, tz=timezone.utc
            ).isoformat()
            writer.writerow([dt, x[1], x[2], x[3], x[4], x[5]])

    print(f"wrote {len(rows)} bars -> {out}")


if __name__ == "__main__":
    main()
