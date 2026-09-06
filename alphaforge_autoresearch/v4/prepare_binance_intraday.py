"""Checksum-verified Binance Vision intraday downloader for v4 prop research."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
UA = "autoresearch-v4-prop-intraday/1.0"


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def request_bytes(url: str, tries: int = 4) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as exc:
            last = exc
            if i + 1 < tries:
                time.sleep(2 ** i)
    raise last


def next_month(x: datetime) -> datetime:
    return datetime(
        x.year + (x.month == 12),
        1 if x.month == 12 else x.month + 1,
        1,
        tzinfo=timezone.utc,
    )


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def prepare(symbol: str, interval: str, start: datetime, end: datetime, out: Path) -> dict:
    root = (
        f"https://data.binance.vision/data/spot/monthly/klines/"
        f"{symbol}/{interval}"
    )
    m = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    last = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    rows = []
    archive_hashes = {}
    found_first = False

    while m <= last:
        stem = f"{symbol}-{interval}-{m.year:04d}-{m.month:02d}.zip"
        try:
            blob = request_bytes(f"{root}/{stem}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and not found_first:
                m = next_month(m)
                continue
            raise
        found_first = True
        expected = (
            request_bytes(f"{root}/{stem}.CHECKSUM")
            .decode()
            .strip()
            .split()[0]
        )
        got = hashlib.sha256(blob).hexdigest()
        if got.lower() != expected.lower():
            raise RuntimeError(f"checksum mismatch for {stem}")
        archive_hashes[stem] = got

        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if len(names) != 1:
                raise RuntimeError(f"unexpected archive contents: {stem}")
            text = zf.read(names[0]).decode()

        for row in csv.reader(io.StringIO(text)):
            if len(row) < 6:
                continue
            raw = int(row[0])
            stamp = datetime.fromtimestamp(
                raw / (1_000_000 if raw >= 10**15 else 1000),
                tz=timezone.utc,
            )
            if start <= stamp <= end:
                rows.append(
                    [
                        stamp.isoformat(),
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                    ]
                )
        m = next_month(m)

    rows.sort(key=lambda x: x[0])
    if not rows:
        raise RuntimeError(f"no Binance {interval} rows for {symbol}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        w.writerows(rows)

    manifest = {
        "version": 1,
        "protocol": "alpha_generation_v4",
        "source": "binance_vision_spot",
        "symbol": symbol,
        "interval": interval,
        "requested_start": start.strftime("%Y-%m-%d"),
        "requested_end": end.strftime("%Y-%m-%d"),
        "rows": len(rows),
        "first": rows[0][0],
        "last": rows[-1][0],
        "csv_sha256": sha256_path(out),
        "archive_sha256": archive_hashes,
        "provider_integrity": "every monthly archive verified against Binance Vision CHECKSUM",
        "hidden_validation_included": False,
        "final_oos_included": False,
    }
    mpath = out.with_suffix(".manifest.json")
    mpath.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--interval", default="1h", choices=["1h"])
    ap.add_argument("--start", default="2017-08-17")
    ap.add_argument("--end", default="2020-12-31")
    ap.add_argument("--output-dir", default="v4_prop_intraday_data")
    args = ap.parse_args()

    start = dt(args.start)
    end = dt(args.end).replace(hour=23, minute=59, second=59)
    root = (BASE / args.output_dir).resolve()
    try:
        root.relative_to(BASE.resolve())
    except ValueError as exc:
        raise RuntimeError("output directory must remain inside project root") from exc
    out = root / f"{args.id}_{args.interval}.csv"
    manifest = prepare(args.symbol, args.interval, start, end, out)
    print(json.dumps({
        "symbol": args.symbol,
        "interval": args.interval,
        "rows": manifest["rows"],
        "last": manifest["last"],
        "sha256": manifest["csv_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
