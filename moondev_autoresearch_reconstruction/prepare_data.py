"""Download real Binance 6-hour candles from the public Data Vision archive.

The normal Binance REST API returns HTTP 451 from some GitHub-hosted runners.
Data Vision is Binance's public historical archive and requires no API key.

For research integrity, each monthly ZIP is verified against Binance's published
SHA-256 checksum before its rows are accepted.
"""

import argparse
import csv
import hashlib
import io
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

BASE = "https://data.binance.vision/data/spot/monthly/klines"
DEFAULT_START = "2017-08-17"
INTERVAL = "6h"
USER_AGENT = "moondev-autoresearch-reconstruction/1.0"


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def month_start(dt):
    return datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)


def next_month(dt):
    if dt.month == 12:
        return datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)


def get_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def archive_urls(symbol, month):
    stem = f"{symbol}-{INTERVAL}-{month.year:04d}-{month.month:02d}.zip"
    root = f"{BASE}/{symbol}/{INTERVAL}"
    return f"{root}/{stem}", f"{root}/{stem}.CHECKSUM", stem


def verify_checksum(blob, checksum_text, filename):
    expected = checksum_text.decode("utf-8").strip().split()[0].lower()
    actual = hashlib.sha256(blob).hexdigest().lower()
    if actual != expected:
        raise RuntimeError(
            f"SHA-256 mismatch for {filename}: expected {expected}, got {actual}"
        )


def timestamp_to_datetime(value):
    n = int(value)
    # Binance historical archives use milliseconds. Newer archive datasets can
    # use microseconds, so detect by magnitude instead of assuming one unit.
    if n >= 10**15:
        return datetime.fromtimestamp(n / 1_000_000, tz=timezone.utc)
    return datetime.fromtimestamp(n / 1000, tz=timezone.utc)


def read_archive(blob, filename):
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if len(names) != 1:
            raise RuntimeError(
                f"Expected one CSV inside {filename}, found {len(names)}"
            )
        raw = zf.read(names[0]).decode("utf-8")
    return list(csv.reader(io.StringIO(raw)))


def fetch_month(symbol, month):
    zip_url, checksum_url, filename = archive_urls(symbol, month)
    try:
        blob = get_bytes(zip_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    checksum = get_bytes(checksum_url)
    verify_checksum(blob, checksum, filename)
    rows = read_archive(blob, filename)
    print(f"{filename}: {len(rows)} rows, checksum OK")
    return rows


def fetch(symbol, start_dt, end_dt):
    month = month_start(start_dt)
    last_month = month_start(end_dt)
    rows = []

    while month <= last_month:
        batch = fetch_month(symbol, month)
        if batch is None:
            raise RuntimeError(
                f"Missing Binance Data Vision archive for "
                f"{symbol} {month.year:04d}-{month.month:02d}"
            )
        rows.extend(batch)
        month = next_month(month)

    filtered = []
    for row in rows:
        if len(row) < 6:
            raise RuntimeError(f"Malformed kline row with {len(row)} columns")
        dt = timestamp_to_datetime(row[0])
        if start_dt <= dt <= end_dt:
            filtered.append((dt, row))

    filtered.sort(key=lambda item: item[0])
    return filtered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="ETH")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument(
        "--end",
        default=None,
        help=(
            "inclusive UTC date. For sealed IS research use 2022-12-31. "
            "If omitted, uses the last day of the previous UTC month."
        ),
    )
    args = ap.parse_args()

    asset = args.asset.upper()
    symbol = asset if asset.endswith("USDT") else asset + "USDT"
    start_dt = parse_date(args.start)

    if args.end:
        end_dt = parse_date(args.end).replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
    else:
        now = datetime.now(timezone.utc)
        first_this_month = month_start(now)
        end_dt = first_this_month.replace(microsecond=0) - __import__(
            "datetime"
        ).timedelta(microseconds=1)

    rows = fetch(symbol, start_dt, end_dt)
    if not rows:
        raise SystemExit(f"No Binance Data Vision candles returned for {symbol}")

    out = DATA / f"{asset.removesuffix('USDT')}_{INTERVAL}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        for dt, row in rows:
            writer.writerow(
                [dt.isoformat(), row[1], row[2], row[3], row[4], row[5]]
            )

    print(
        f"wrote {len(rows)} verified real bars -> {out} "
        f"({rows[0][0].isoformat()} .. {rows[-1][0].isoformat()})"
    )


if __name__ == "__main__":
    main()
