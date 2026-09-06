"""Native Dukascopy yearly BID D1 candle adapter for the stock/FX lane."""

import csv
import hashlib
import json
import lzma
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

D1_STRUCT = struct.Struct("!IIIIIf")


def price_divisor(symbol):
    symbol = str(symbol).upper()
    return 1000.0 if "JPY" in symbol else 100000.0


def _decompress(blob):
    if not blob:
        return b""
    remaining = bytes(blob)
    out = []
    while remaining:
        dec = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
        try:
            out.append(dec.decompress(remaining))
        except lzma.LZMAError as exc:
            raise RuntimeError("invalid Dukascopy LZMA payload") from exc
        unused = dec.unused_data
        if not unused:
            if not dec.eof:
                raise RuntimeError("truncated Dukascopy LZMA payload")
            break
        remaining = unused
    return b"".join(out)


def decode_daily_blob(blob, symbol, year):
    raw = _decompress(blob)
    if not raw:
        raise RuntimeError(f"Dukascopy {symbol} {year}: empty decoded archive")
    if len(raw) % D1_STRUCT.size:
        raise RuntimeError(
            f"Dukascopy {symbol} {year}: invalid decoded byte length {len(raw)}"
        )

    divisor = price_divisor(symbol)
    base = datetime(int(year), 1, 1, tzinfo=timezone.utc)
    rows = []
    previous = None
    filtered = 0
    all_zero = 0

    for offset in range(0, len(raw), D1_STRUCT.size):
        seconds, raw_open, raw_close, raw_low, raw_high, volume = (
            D1_STRUCT.unpack_from(raw, offset)
        )
        if raw_open == raw_close == raw_low == raw_high == 0:
            all_zero += 1
            continue

        signature = (
            raw_open, raw_close, raw_low, raw_high, float(volume)
        )
        if signature == previous:
            filtered += 1
            continue
        previous = signature

        stamp = base + timedelta(seconds=int(seconds))
        o = raw_open / divisor
        h = raw_high / divisor
        l = raw_low / divisor
        close = raw_close / divisor
        if h < max(o, l, close) or l > min(o, h, close):
            raise RuntimeError(
                f"Dukascopy {symbol}: malformed OHLC row "
                f"{stamp.date().isoformat()}"
            )
        rows.append(
            [stamp.isoformat(), o, h, l, close, float(volume)]
        )

    audit = {
        "decoded_records": len(raw) // D1_STRUCT.size,
        "retained_records": len(rows),
        "filtered_forward_fill_records": filtered,
        "all_zero_records": all_zero,
        "price_divisor": divisor,
    }
    return rows, audit


def prepare(symbol, start, end, out, request_bytes):
    symbol = str(symbol).upper().replace("/", "").replace("-", "")
    out = Path(out)
    rows = []
    seen_dates = set()
    archives = []

    for year in range(start.year, end.year + 1):
        url = (
            f"https://www.dukascopy.com/datafeed/{symbol}/{year}/"
            "BID_candles_day_1.bi5"
        )
        blob = request_bytes(url)
        decoded, stats = decode_daily_blob(blob, symbol, year)
        kept = 0
        for row in decoded:
            stamp = datetime.fromisoformat(row[0])
            if not (start <= stamp <= end):
                continue
            day = stamp.strftime("%Y-%m-%d")
            if day in seen_dates:
                raise RuntimeError(f"Dukascopy {symbol}: duplicate D1 date {day}")
            seen_dates.add(day)
            rows.append(row)
            kept += 1
        archives.append(
            {
                "year": year,
                "url": url,
                "compressed_bytes": len(blob),
                "compressed_sha256": hashlib.sha256(blob).hexdigest(),
                **stats,
                "retained_in_requested_window": kept,
            }
        )

    rows.sort(key=lambda row: row[0])
    if len(rows) < 100:
        raise RuntimeError(
            f"Dukascopy {symbol}: insufficient native D1 rows ({len(rows)})"
        )
    if rows[-1][0][:10] >= "2023-01-01":
        raise RuntimeError("refusing Dukascopy 2023+ final OOS data")

    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        writer.writerows(rows)

    audit = {
        "version": 1,
        "source": "dukascopy_bid_daily",
        "symbol": symbol,
        "price_side": "BID",
        "timeframe": "D1",
        "requested_start": start.strftime("%Y-%m-%d"),
        "requested_end": end.strftime("%Y-%m-%d"),
        "rows": len(rows),
        "archives": archives,
        "filtered_forward_fill_records": sum(
            x["filtered_forward_fill_records"] for x in archives
        ),
        "all_zero_records": sum(x["all_zero_records"] for x in archives),
        "final_oos_included": False,
    }
    out.with_suffix(".source.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Dukascopy BID {symbol}: {len(rows)} native daily bars -> {out}")
    return audit
