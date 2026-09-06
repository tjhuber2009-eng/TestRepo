"""Prepare daily OHLCV for the cross-market tournament.

Crypto uses Binance Data Vision checksum-verified monthly archives.
ETFs/stocks and continuous futures can use Yahoo's public chart endpoint.
Daily FX expansion uses Stooq currency-history snapshots because the previously
attempted Yahoo FX rows failed OHLC integrity checks. For symbols with adjusted
close, OHLC are adjusted by the same factor to avoid split artifacts.
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

BASE = Path(__file__).resolve().parent
OUT = BASE / "data"
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


def prepare_bitstamp(symbol, start, end, out):
    pair = str(symbol).lower().replace("/", "").replace("-", "")
    if pair not in {"btcusd", "ethusd"}:
        raise RuntimeError(f"unsupported Bitstamp daily pair: {symbol}")
    rows = []
    seen = set()
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=899), end)
        params = urllib.parse.urlencode({
            "step": 86400,
            "start": int(cur.timestamp()),
            "end": int(chunk_end.timestamp()),
            "limit": 1000,
        })
        url = f"https://www.bitstamp.net/api/v2/ohlc/{pair}/?{params}"
        payload = json.loads(request_bytes(url).decode())
        data = (payload.get("data") or {}).get("ohlc") or []
        for raw in data:
            ts = int(raw["timestamp"])
            stamp = datetime.fromtimestamp(ts, tz=timezone.utc)
            if not (start <= stamp <= end):
                continue
            day = stamp.strftime("%Y-%m-%d")
            if day in seen:
                continue
            vals = [float(raw[k]) for k in ("open","high","low","close")]
            o,h,l,cl = vals
            if h < max(o,l,cl) or l > min(o,h,cl):
                raise RuntimeError(
                    f"Bitstamp {symbol}: malformed OHLC row {day}"
                )
            rows.append([
                stamp.isoformat(), o, h, l, cl,
                float(raw.get("volume") or 0.0),
            ])
            seen.add(day)
        cur = chunk_end + timedelta(days=1)
    rows.sort(key=lambda x: x[0])
    if len(rows) < 100:
        raise RuntimeError(f"Bitstamp {symbol}: insufficient daily rows ({len(rows)})")
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Date","Open","High","Low","Close","Volume"])
        w.writerows(rows)
    print(f"Bitstamp {symbol}: {len(rows)} daily bars -> {out}")


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


def normalize_yahoo_futures_proxy(out):
    """Make Yahoo continuous-futures proxy OHLC internally coherent.

    Yahoo continuous futures occasionally publish settlement-like Close/Open
    values outside the reported High/Low envelope around contract rolls or
    provider anomalies. For development-only proxy screening, preserve Open
    and Close exactly and widen High/Low to contain all four OHLC values.
    Every changed row is recorded; no value is silently dropped or clipped.
    """
    rows = []
    changed = []
    with out.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            o = float(row["Open"])
            h = float(row["High"])
            l = float(row["Low"])
            cl = float(row["Close"])
            new_h = max(h, o, l, cl)
            new_l = min(l, o, h, cl)
            if new_h != h or new_l != l:
                changed.append({
                    "Date": row["Date"],
                    "original_high": h,
                    "original_low": l,
                    "normalized_high": new_h,
                    "normalized_low": new_l,
                    "open": o,
                    "close": cl,
                })
                row["High"] = repr(new_h)
                row["Low"] = repr(new_l)
            rows.append(row)
    if len(changed) > max(50, int(len(rows) * 0.02)):
        raise RuntimeError(
            f"Yahoo futures proxy requires excessive OHLC normalization: "
            f"{len(changed)}/{len(rows)} rows"
        )
    fields = ["Date","Open","High","Low","Close","Volume"]
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: row.get(k, "") for k in fields} for row in rows)
    meta = {
        "policy": "explicit_settlement_envelope_v1",
        "changed_rows": len(changed),
        "total_rows": len(rows),
        "changed_fraction": 0.0 if not rows else len(changed) / len(rows),
        "changes": changed,
        "interpretation": (
            "Open and Close are preserved exactly; High/Low are widened only "
            "where required to contain provider-reported OHLC. Development-only "
            "continuous-futures proxy, not contract-exact evidence."
        ),
    }
    out.with_suffix(".normalization.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Yahoo futures proxy normalization: {len(changed)}/{len(rows)} "
        f"OHLC envelopes widened -> {out}"
    )
    return meta


def prepare_yahoo_futures_proxy(symbol, start, end, out):
    prepare_yahoo(symbol, start, end, out)
    return normalize_yahoo_futures_proxy(out)


def prepare_stooq(symbol, start, end, out):
    enc = urllib.parse.quote(symbol.lower(), safe="")
    d1 = start.strftime("%Y%m%d")
    d2 = end.strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={enc}&i=d&d1={d1}&d2={d2}"
    text = request_bytes(url).decode("utf-8", errors="replace").strip()
    if not text or "Date" not in text.splitlines()[0]:
        raise RuntimeError(f"Stooq returned no CSV data for {symbol}")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    seen = set()
    for raw in reader:
        date = (raw.get("Date") or raw.get("date") or "").strip()
        if not date or date in seen:
            continue
        vals = []
        for key in ("Open","High","Low","Close"):
            value = raw.get(key)
            if value is None or str(value).strip() in {"", "-"}:
                vals = []
                break
            vals.append(float(value))
        if not vals:
            continue
        o,h,l,cl = vals
        if h < max(o,l,cl) or l > min(o,h,cl):
            raise RuntimeError(
                f"Stooq {symbol}: malformed OHLC row {date}; refusing silent repair"
            )
        vol_raw = raw.get("Volume")
        try:
            vol = 0.0 if vol_raw in (None, "", "-") else float(vol_raw)
        except Exception:
            vol = 0.0
        stamp = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if start <= stamp <= end:
            rows.append([stamp.isoformat(), o, h, l, cl, vol])
            seen.add(date)
    rows.sort(key=lambda x: x[0])
    if len(rows) < 100:
        raise RuntimeError(f"Stooq {symbol}: insufficient daily rows ({len(rows)})")
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Date","Open","High","Low","Close","Volume"])
        w.writerows(rows)
    print(f"Stooq {symbol}: {len(rows)} daily bars -> {out}")


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
            else (
                "provider response snapshotted by generated CSV SHA256; "
                "Bitstamp public OHLC endpoint has no archive checksum"
                if source == "bitstamp"
                else (
                "provider response snapshotted by generated CSV SHA256; "
                "Stooq public historical endpoint has no archive checksum"
                if source in {"stooq", "stooq_fx"}
                else "provider response snapshotted by generated CSV SHA256; Yahoo publishes no archive checksum"
                )
            )
        ),
        "oos_included": False,
    }
    if source == "yahoo_futures_proxy":
        norm_path = out.with_suffix(".normalization.json")
        if not norm_path.exists():
            raise RuntimeError("futures-proxy normalization audit missing")
        manifest["normalization"] = json.loads(
            norm_path.read_text(encoding="utf-8")
        )
    path = out.with_suffix(".manifest.json")
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"manifest -> {path} sha256={manifest['csv_sha256']}")
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["binance","bitstamp","yahoo","yahoo_futures_proxy","stooq","stooq_fx"], required=True)
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
    elif args.source == "bitstamp":
        prepare_bitstamp(args.symbol, start, end, out)
    elif args.source in {"stooq", "stooq_fx"}:
        prepare_stooq(args.symbol, start, end, out)
    elif args.source == "yahoo_futures_proxy":
        prepare_yahoo_futures_proxy(args.symbol, start, end, out)
    else:
        prepare_yahoo(args.symbol, start, end, out)
    write_manifest(out, args.source, args.symbol, args.id, start, end)


if __name__ == "__main__":
    main()
