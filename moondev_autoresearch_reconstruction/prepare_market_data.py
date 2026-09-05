"""Prepare daily OHLCV for the cross-market tournament.

Crypto uses Binance Data Vision checksum-verified monthly archives.
ETFs, FX and continuous futures use Yahoo's public chart endpoint. For symbols
with adjusted close, OHLC are adjusted by the same factor to avoid split
artifacts (important for leveraged ETFs such as TQQQ).
"""

import argparse
import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)
UA = "moondev-cross-market-autoresearch/1.0"


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def request_bytes(url, tries=4):
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


def next_month(x):
    return datetime(x.year + (x.month == 12), 1 if x.month == 12 else x.month + 1, 1, tzinfo=timezone.utc)


def prepare_binance(symbol, start, end, out):
    root = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1d"
    m = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    last = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    rows = []
    found_first = False
    while m <= last:
        stem = f"{symbol}-1d-{m.year:04d}-{m.month:02d}.zip"
        try:
            blob = request_bytes(f"{root}/{stem}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and not found_first:
                print(f"{stem}: not listed yet; skipping leading month")
                m = next_month(m)
                continue
            raise
        found_first = True
        check = request_bytes(f"{root}/{stem}.CHECKSUM").decode().strip().split()[0]
        got = hashlib.sha256(blob).hexdigest()
        if got.lower() != check.lower():
            raise RuntimeError(f"checksum mismatch for {stem}")
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if len(names) != 1:
                raise RuntimeError(f"unexpected archive contents: {stem}")
            data = zf.read(names[0]).decode()
        for row in csv.reader(io.StringIO(data)):
            if len(row) < 6:
                continue
            raw = int(row[0])
            stamp = datetime.fromtimestamp(
                raw / (1_000_000 if raw >= 10**15 else 1000),
                tz=timezone.utc,
            )
            if start <= stamp <= end:
                rows.append([stamp.isoformat(), row[1], row[2], row[3], row[4], row[5]])
        m = next_month(m)

    rows.sort(key=lambda x: x[0])
    if not rows:
        raise RuntimeError(f"no Binance daily rows found for {symbol}")
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date","Open","High","Low","Close","Volume"])
        w.writerows(rows)
    print(f"Binance {symbol}: {len(rows)} verified daily bars -> {out}")


def prepare_yahoo(symbol, start, end, out):
    p1 = int(start.timestamp())
    p2 = int((end + timedelta(days=1)).timestamp())
    enc = urllib.parse.quote(symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
        f"?period1={p1}&period2={p2}&interval=1d&events=history"
        f"&includeAdjustedClose=true"
    )
    payload = json.loads(request_bytes(url).decode())
    result = payload["chart"]["result"]
    if not result:
        raise RuntimeError(f"Yahoo returned no data for {symbol}")
    x = result[0]
    stamps = x["timestamp"]
    q = x["indicators"]["quote"][0]
    adj_block = x["indicators"].get("adjclose", [{}])[0]
    adj = adj_block.get("adjclose") or q["close"]

    rows = []
    for i, ts in enumerate(stamps):
        vals = [q[k][i] for k in ["open","high","low","close"]]
        if any(v is None for v in vals):
            continue
        close = float(q["close"][i])
        adjclose = adj[i]
        factor = 1.0
        if adjclose is not None and close:
            factor = float(adjclose) / close
        o,h,l,c = [float(v) * factor for v in vals]
        vol = q.get("volume", [None]*len(stamps))[i]
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc)
        rows.append([
            stamp.isoformat(), o, h, l, c,
            0.0 if vol is None else float(vol),
        ])

    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date","Open","High","Low","Close","Volume"])
        w.writerows(rows)
    print(f"Yahoo {symbol}: {len(rows)} adjusted daily bars -> {out}")


def sha256_path(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(out, source, symbol, ident, start, end):
    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError(f"cannot manifest empty file: {out}")
    manifest = {
        "version": 1,
        "source": source,
        "symbol": symbol,
        "id": ident,
        "requested_start": start.strftime("%Y-%m-%d"),
        "requested_end": end.strftime("%Y-%m-%d"),
        "rows": len(rows),
        "first": rows[0]["Date"],
        "last": rows[-1]["Date"],
        "csv_sha256": sha256_path(out),
        "provider_integrity": (
            "published monthly archive SHA256 verified"
            if source == "binance"
            else "provider response snapshotted by generated CSV SHA256; Yahoo publishes no archive checksum"
        ),
        "oos_included": False,
    }
    path = out.with_suffix(".manifest.json")
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"manifest -> {path} sha256={manifest['csv_sha256']}")
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["binance","yahoo"], required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--start", default="2017-08-17")
    ap.add_argument("--end", default="2022-12-31")
    ap.add_argument(
        "--output-dir",
        default="data",
        help="project-relative output directory (default: data)",
    )
    args = ap.parse_args()
    start = dt(args.start)
    end = dt(args.end).replace(hour=23, minute=59, second=59)
    out_dir = (BASE / args.output_dir).resolve()
    try:
        out_dir.relative_to(BASE.resolve())
    except ValueError as exc:
        raise RuntimeError("output directory must remain inside project root") from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.id}_1d.csv"
    if args.source == "binance":
        prepare_binance(args.symbol, start, end, out)
    else:
        prepare_yahoo(args.symbol, start, end, out)
    write_manifest(out, args.source, args.symbol, args.id, start, end)


if __name__ == "__main__":
    main()
