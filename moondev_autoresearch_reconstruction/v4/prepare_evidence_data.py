"""Prepare evidence-bearing daily market data for AUTORESEARCH v4.

Primary development sources:
- Tiingo EOD for US equities/ETFs. Raw OHLCV is restated only for splits
  occurring inside the requested sample; dividends remain explicit cash flows.
- Cboe official VIX daily history for the VIX context.

Provider-adjusted Tiingo prices are never used by the backtester. They are used
only as an independent return-accounting diagnostic against our raw+actions
reconstruction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import statistics
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
UA = "moondev-autoresearch-v4-evidence-data/1.0"
TIINGO_ROOT = "https://api.tiingo.com/tiingo/daily"
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"


def _parse_date(value: str) -> datetime:
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        stamp = datetime.strptime(raw, "%m/%d/%Y")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _request_bytes(url: str, *, headers: dict[str, str] | None = None, tries: int = 4) -> bytes:
    merged = {"User-Agent": UA}
    if headers:
        merged.update(headers)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.read()
        except Exception as exc:  # pragma: no cover - live network path
            last = exc
            if i + 1 < tries:
                time.sleep(2 ** i)
    raise last


def tiingo_rows_from_prices(prices: list[dict]) -> list[list]:
    """Convert Tiingo raw EOD rows to a stable split-adjusted cash-dividend basis.

    A row's raw OHLC is divided by the product of *future* split factors inside
    the requested sample. The current row's split factor is applied only to
    earlier rows because the ex-date row is already on the post-split basis.
    Volumes are multiplied by that same future split product. Dividends stay as
    explicit cash flows, restated only for later splits.
    """
    ordered = sorted(prices, key=lambda row: _parse_date(row["date"]))
    future_split_product = 1.0
    converted = []
    for row in reversed(ordered):
        required = ("open", "high", "low", "close")
        if any(row.get(key) is None for key in required):
            continue
        if future_split_product <= 0 or not math.isfinite(future_split_product):
            raise ValueError("invalid future Tiingo split product")
        stamp = _parse_date(row["date"])
        price_factor = 1.0 / future_split_product
        volume = float(row.get("volume") or 0.0) * future_split_product
        dividend = float(row.get("divCash") or 0.0) * price_factor
        converted.append([
            stamp.isoformat(),
            float(row["open"]) * price_factor,
            float(row["high"]) * price_factor,
            float(row["low"]) * price_factor,
            float(row["close"]) * price_factor,
            volume,
            dividend,
        ])
        split = float(row.get("splitFactor") or 1.0)
        if split <= 0 or not math.isfinite(split):
            raise ValueError(f"invalid Tiingo splitFactor={split} on {stamp.date()}")
        future_split_product *= split
    converted.reverse()
    return converted


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    x = sorted(float(v) for v in values)
    if len(x) == 1:
        return x[0]
    pos = (len(x) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return x[lo]
    frac = pos - lo
    return x[lo] * (1.0 - frac) + x[hi] * frac


def tiingo_adjusted_open_diagnostics(prices: list[dict]) -> dict:
    """Compare our raw+actions total returns with Tiingo adjusted-open returns."""
    ordered = sorted(prices, key=lambda row: _parse_date(row["date"]))
    canonical = tiingo_rows_from_prices(ordered)
    canon_by_date = {row[0][:10]: row for row in canonical}
    provider = {
        _parse_date(row["date"]).date().isoformat(): float(row["adjOpen"])
        for row in ordered
        if row.get("adjOpen") not in (None, 0)
    }
    dates = sorted(set(canon_by_date) & set(provider))
    diffs_bp = []
    for current, nxt in zip(dates, dates[1:]):
        a = canon_by_date[current]
        b = canon_by_date[nxt]
        if float(a[1]) == 0 or provider[current] == 0:
            continue
        reconstructed = (float(b[1]) + float(b[6])) / float(a[1]) - 1.0
        adjusted = provider[nxt] / provider[current] - 1.0
        if math.isfinite(reconstructed) and math.isfinite(adjusted):
            diffs_bp.append(abs(reconstructed - adjusted) * 10000.0)
    p95 = _percentile(diffs_bp, 0.95)
    max_abs = max(diffs_bp) if diffs_bp else None
    return {
        "observations": len(diffs_bp),
        "median_abs_diff_bp": statistics.median(diffs_bp) if diffs_bp else None,
        "p95_abs_diff_bp": p95,
        "max_abs_diff_bp": max_abs,
        "parity_pass": bool(
            diffs_bp
            and p95 is not None
            and max_abs is not None
            and p95 <= 1.0
            and max_abs <= 10.0
        ),
        "policy": {
            "p95_abs_diff_bp_max": 1.0,
            "max_abs_diff_bp_max": 10.0,
        },
    }


def prepare_tiingo(symbol: str, start: datetime, end: datetime, out: Path, token: str) -> dict:
    if not token:
        raise RuntimeError(
            "Tiingo API token required. Set TIINGO_API_TOKEN or pass --tiingo-token."
        )
    enc = urllib.parse.quote(symbol, safe="")
    query = urllib.parse.urlencode({
        "startDate": start.strftime("%Y-%m-%d"),
        "endDate": end.strftime("%Y-%m-%d"),
    })
    url = f"{TIINGO_ROOT}/{enc}/prices?{query}"
    payload = json.loads(_request_bytes(
        url,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        },
    ).decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"Tiingo returned no EOD rows for {symbol}")
    rows = tiingo_rows_from_prices(payload)
    if not rows:
        raise RuntimeError(f"Tiingo rows for {symbol} contained no usable OHLC")
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume", "Dividend"])
        writer.writerows(rows)
    diag = tiingo_adjusted_open_diagnostics(payload)
    print(
        f"Tiingo {symbol}: {len(rows)} daily bars -> {out}; "
        f"adj-open parity p95={diag['p95_abs_diff_bp']} bp"
    )
    return diag


def cboe_vix_rows_from_csv(blob: bytes, start: datetime, end: datetime) -> list[list]:
    text = blob.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for raw in reader:
        row = {str(k).strip().upper(): v for k, v in raw.items() if k is not None}
        date_raw = row.get("DATE") or row.get("TRADE DATE")
        if not date_raw:
            continue
        stamp = _parse_date(date_raw)
        if not (start.date() <= stamp.date() <= end.date()):
            continue
        close_raw = row.get("CLOSE")
        if close_raw in (None, ""):
            continue
        close = float(close_raw)
        open_ = float(row.get("OPEN") or close)
        high = float(row.get("HIGH") or close)
        low = float(row.get("LOW") or close)
        rows.append([
            stamp.isoformat(), open_, high, low, close, 0.0, 0.0,
        ])
    rows.sort(key=lambda x: x[0])
    return rows


def prepare_cboe_vix(start: datetime, end: datetime, out: Path) -> None:
    rows = cboe_vix_rows_from_csv(_request_bytes(CBOE_VIX_URL), start, end)
    if not rows:
        raise RuntimeError("Cboe returned no VIX daily rows")
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume", "Dividend"])
        writer.writerows(rows)
    print(f"Cboe VIX: {len(rows)} daily bars -> {out}")


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(
    out: Path,
    *,
    source: str,
    symbol: str,
    ident: str,
    start: datetime,
    end: datetime,
    provider_diagnostic: dict | None = None,
) -> dict:
    with out.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"cannot manifest empty file: {out}")
    if source == "tiingo":
        integrity = (
            "authenticated provider response; canonical CSV SHA256 recorded; "
            "provider adjusted prices used only for independent return parity diagnostic"
        )
        method = "raw_ohlcv_future_split_restatement_explicit_divcash_v1"
    elif source == "cboe_vix":
        integrity = "official Cboe VIX history response; canonical CSV SHA256 recorded"
        method = "official_cboe_vix_daily_ohlc_v1"
    else:
        raise ValueError(source)
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
        "csv_sha256": _sha256_path(out),
        "adjustment_method": method,
        "dividends_explicit": source == "tiingo",
        "provider_integrity": integrity,
        "hidden_validation_included": False,
        "final_oos_included": False,
    }
    if provider_diagnostic is not None:
        manifest["provider_adjusted_return_diagnostic"] = provider_diagnostic
    path = out.with_suffix(".manifest.json")
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"manifest -> {path} sha256={manifest['csv_sha256']}")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["tiingo", "cboe_vix"], required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--output-dir", default="v4_data")
    ap.add_argument("--tiingo-token", default=None)
    args = ap.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end)
    out_dir = (BASE / args.output_dir).resolve()
    try:
        out_dir.relative_to(BASE.resolve())
    except ValueError as exc:
        raise RuntimeError("output directory must remain inside project root") from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.id}_1d.csv"

    diagnostic = None
    if args.source == "tiingo":
        token = args.tiingo_token or os.environ.get("TIINGO_API_TOKEN", "")
        diagnostic = prepare_tiingo(args.symbol, start, end, out, token)
    else:
        prepare_cboe_vix(start, end, out)

    write_manifest(
        out,
        source=args.source,
        symbol=args.symbol,
        ident=args.id,
        start=start,
        end=end,
        provider_diagnostic=diagnostic,
    )


if __name__ == "__main__":
    main()
