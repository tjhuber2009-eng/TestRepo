#!/usr/bin/env python3
"""
Data-only audit for QuantRocket's free usstock-learn-1d bundle.

This script intentionally does NOT train a model.
It writes /tmp/stage2c_quantrocket_audit.json and a Markdown summary.
"""

from __future__ import annotations

import glob
import io
import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd

BUNDLE = os.environ.get("MP_BUNDLE", "usstock-learn-1d")
OUT_JSON = Path("/tmp/stage2c_quantrocket_audit.json")
OUT_MD = Path("/tmp/stage2c_quantrocket_audit.md")


def safe_json(v):
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return v


def bundle_prices(sids, start, end):
    from quantrocket.zipline import download_bundle_file

    f = io.StringIO()
    download_bundle_file(
        BUNDLE,
        sids=list(sids),
        start_date=str(start),
        end_date=str(end),
        filepath_or_buffer=f,
    )
    f.seek(0)
    if not f.getvalue().strip():
        return pd.DataFrame()
    return pd.read_csv(f, parse_dates=["Date"], index_col=["Field", "Date"])


def sqlite_inventory(pattern):
    out = []
    for path in glob.glob(pattern):
        item = {"path": path, "tables": {}}
        try:
            con = sqlite3.connect(path)
            names = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", con
            )["name"].tolist()
            for name in names:
                try:
                    cols = pd.read_sql_query(f"PRAGMA table_info([{name}])", con)
                    count = con.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
                    item["tables"][name] = {
                        "rows": int(count),
                        "columns": cols["name"].tolist(),
                    }
                except Exception as exc:
                    item["tables"][name] = {"error": repr(exc)}
            con.close()
        except Exception as exc:
            item["error"] = repr(exc)
        out.append(item)
    return out


def main():
    from quantrocket.master import get_securities
    from quantrocket.zipline import list_sids

    audit = {
        "schema_version": 1,
        "bundle": BUNDLE,
        "classification": "AUDIT_INCOMPLETE",
        "hard_gates": {},
        "warnings": [],
    }

    sids = list(list_sids(BUNDLE))
    audit["sid_count"] = len(sids)
    audit["hard_gates"]["bundle_has_large_cross_section"] = len(sids) >= 1000

    securities = get_securities(vendors="usstock", fields="usstock*")
    securities.index = securities.index.astype(str)
    sid_set = set(map(str, sids))
    securities = securities.loc[securities.index.intersection(sid_set)].copy()
    audit["master_rows_in_bundle"] = int(len(securities))

    expected_cols = [
        "usstock_Symbol",
        "usstock_SecurityType",
        "usstock_SecurityType2",
        "usstock_FirstPriceDate",
        "usstock_LastPriceDate",
        "usstock_DateDelisted",
        "usstock_CIK",
        "usstock_PrimaryShareSid",
    ]
    audit["master_columns_present"] = [c for c in expected_cols if c in securities.columns]
    audit["master_columns_missing"] = [c for c in expected_cols if c not in securities.columns]

    for c in ["usstock_FirstPriceDate", "usstock_LastPriceDate", "usstock_DateDelisted"]:
        if c in securities.columns:
            securities[c] = pd.to_datetime(securities[c], errors="coerce")

    delisted_mask = pd.Series(False, index=securities.index)
    if "usstock_DateDelisted" in securities.columns:
        delisted_mask |= securities["usstock_DateDelisted"].notna()
    if "usstock_LastPriceDate" in securities.columns:
        delisted_mask |= securities["usstock_LastPriceDate"].notna()

    audit["delisted_or_ended_count"] = int(delisted_mask.sum())
    audit["hard_gates"]["later_dead_securities_present"] = int(delisted_mask.sum()) >= 100

    common_mask = pd.Series(True, index=securities.index)
    if "usstock_SecurityType2" in securities.columns:
        common_mask = securities["usstock_SecurityType2"].eq("Common Stock")
    audit["common_stock_count"] = int(common_mask.sum())

    yearly = {}
    first = securities.get(
        "usstock_FirstPriceDate", pd.Series(pd.NaT, index=securities.index)
    )
    last = securities.get(
        "usstock_LastPriceDate", pd.Series(pd.NaT, index=securities.index)
    )
    for year in range(2007, 2012):
        asof = pd.Timestamp(f"{year}-12-31")
        year_start = pd.Timestamp(f"{year}-01-01")
        alive = (first.isna() | (first <= asof)) & (last.isna() | (last >= year_start))
        yearly[str(year)] = {
            "master_active_or_overlapping": int(alive.sum()),
            "common_active_or_overlapping": int((alive & common_mask).sum()),
        }
    audit["yearly_master_coverage"] = yearly
    audit["hard_gates"]["crisis_years_have_broad_coverage"] = (
        yearly["2008"]["master_active_or_overlapping"] >= 1000
        and yearly["2009"]["master_active_or_overlapping"] >= 1000
    )

    pipeline_counts = {}
    try:
        from zipline.pipeline import Pipeline, master
        from zipline.research import run_pipeline

        common = master.SecuritiesMaster.usstock_SecurityType2.latest.eq("Common Stock")
        for date in ["2008-01-02", "2009-01-02", "2010-01-04", "2011-01-03"]:
            df = run_pipeline(
                Pipeline(
                    columns={"is_common": common},
                    initial_universe=common,
                    screen=common,
                ),
                start_date=date,
                end_date=date,
                bundle=BUNDLE,
            )
            pipeline_counts[date] = int(len(df))
    except Exception as exc:
        audit["warnings"].append(f"pipeline_count_failed: {exc!r}")

    audit["point_in_time_common_counts"] = pipeline_counts
    audit["hard_gates"]["point_in_time_pipeline_works"] = (
        bool(pipeline_counts) and min(pipeline_counts.values()) > 100
    )

    known_candidates = ["LEH", "LEHMQ", "BSC", "WM", "WAMUQ", "WB"]
    hits = []
    if "usstock_Symbol" in securities.columns:
        tmp = securities[securities["usstock_Symbol"].isin(known_candidates)]
        for sid, row in tmp.iterrows():
            hits.append(
                {
                    "sid": sid,
                    "symbol": safe_json(row.get("usstock_Symbol")),
                    "last_price_date": safe_json(row.get("usstock_LastPriceDate")),
                    "date_delisted": safe_json(row.get("usstock_DateDelisted")),
                    "security_type2": safe_json(row.get("usstock_SecurityType2")),
                }
            )
    audit["known_failure_symbol_hits"] = hits

    terminal_samples = []
    candidates = securities[delisted_mask].copy()
    if "usstock_LastPriceDate" in candidates.columns:
        candidates = candidates[
            candidates["usstock_LastPriceDate"].between("2007-01-01", "2011-12-31")
        ]
        candidates = candidates.sort_index().head(25)
        for sid, row in candidates.iterrows():
            last_date = row.get("usstock_LastPriceDate")
            if pd.isna(last_date):
                continue
            item = {
                "sid": sid,
                "symbol": safe_json(row.get("usstock_Symbol")),
                "last_price_date_master": safe_json(last_date),
                "date_delisted": safe_json(row.get("usstock_DateDelisted")),
            }
            try:
                px = bundle_prices(
                    [sid],
                    (last_date - timedelta(days=7)).date(),
                    (last_date + timedelta(days=7)).date(),
                )
                if not px.empty:
                    dates = px.index.get_level_values("Date")
                    item["price_first"] = safe_json(dates.min())
                    item["price_last"] = safe_json(dates.max())
                    item["has_price_after_master_last"] = bool((dates > last_date).any())
                    key = ("Close", last_date)
                    if key in px.index:
                        item["last_close"] = safe_json(px.loc[key, sid])
                else:
                    item["empty_price_query"] = True
            except Exception as exc:
                item["price_query_error"] = repr(exc)
            terminal_samples.append(item)

    audit["terminal_samples"] = terminal_samples
    audit["terminal_samples_with_post_last_price"] = sum(
        1 for x in terminal_samples if x.get("has_price_after_master_last")
    )

    adjustment_test = {
        "symbol": "BIDU",
        "event": "2010 10-for-1 split",
        "expected_ratio_approx": 10.0,
    }
    try:
        symbol_series = securities.get(
            "usstock_Symbol", pd.Series(index=securities.index, dtype=object)
        )
        bidu_rows = securities[symbol_series.eq("BIDU")]
        if len(bidu_rows):
            bidu_sid = str(bidu_rows.index[0])
            pre = bundle_prices([bidu_sid], "2010-05-10", "2010-05-10")
            post = bundle_prices([bidu_sid], "2010-05-10", "2010-05-13")
            d = pd.Timestamp("2010-05-10")
            pre_close = float(pre.loc[("Close", d), bidu_sid])
            post_close = float(post.loc[("Close", d), bidu_sid])
            ratio = pre_close / post_close if post_close else None
            adjustment_test.update(
                {
                    "sid": bidu_sid,
                    "pre_query_close": pre_close,
                    "post_split_window_close": post_close,
                    "ratio": ratio,
                    "point_in_time_adjustment_verified": (
                        ratio is not None and 8.0 <= ratio <= 12.0
                    ),
                }
            )
        else:
            adjustment_test["error"] = "BIDU not found in bundle master"
    except Exception as exc:
        adjustment_test["error"] = repr(exc)

    audit["adjustment_timing_test"] = adjustment_test
    audit["hard_gates"]["point_in_time_adjustment_verified"] = bool(
        adjustment_test.get("point_in_time_adjustment_verified")
    )

    root = f"/var/lib/quantrocket/zipline/data/{BUNDLE}"
    audit["adjustments_sqlite_inventory"] = sqlite_inventory(
        f"{root}/*/adjustments.sqlite"
    )
    audit["assets_sqlite_inventory"] = sqlite_inventory(f"{root}/*/assets*.sqlite")

    adjustment_tables = set()
    for db in audit["adjustments_sqlite_inventory"]:
        adjustment_tables.update(db.get("tables", {}).keys())
    audit["adjustment_tables_seen"] = sorted(adjustment_tables)

    has_merger_table = any("merger" in t.lower() for t in adjustment_tables)
    audit["terminal_semantics"] = {
        "post_last_prices_seen": audit["terminal_samples_with_post_last_price"] > 0,
        "merger_like_adjustment_table_seen": has_merger_table,
        "bankruptcy_terminal_return_explicitly_verified": False,
        "defensible_for_unqualified_next_session_label": False,
        "reason": (
            "This audit does not yet prove an explicit economic terminal return "
            "for bankruptcy/other delistings. Do not silently drop terminal rows."
        ),
    }
    audit["hard_gates"]["terminal_return_semantics_defensible"] = False

    all_other = all(
        v
        for k, v in audit["hard_gates"].items()
        if k != "terminal_return_semantics_defensible"
    )
    if all_other and audit["hard_gates"]["terminal_return_semantics_defensible"]:
        audit["classification"] = "READY_TO_FREEZE_MP002F"
    elif all_other:
        audit["classification"] = "DATA_AUDIT_PASS_EXCEPT_TERMINAL_LABEL_BLOCKER"
    else:
        audit["classification"] = "MP-002F_DATA_NOT_DEFENSIBLE_OR_AUDIT_FAILED"

    OUT_JSON.write_text(
        json.dumps(audit, indent=2, default=safe_json), encoding="utf-8"
    )

    lines = [
        "# QuantRocket Stage 2C Audit",
        "",
        f"- Bundle: `{BUNDLE}`",
        f"- SIDs: **{audit['sid_count']}**",
        (
            "- Delisted/ended securities in bundle master: "
            f"**{audit['delisted_or_ended_count']}**"
        ),
        f"- Classification: **{audit['classification']}**",
        "",
        "## Hard gates",
        "",
    ]
    for key, value in audit["hard_gates"].items():
        lines.append(f"- {key}: **{value}**")

    lines += [
        "",
        "## Adjustment timing",
        "",
        "```json",
        json.dumps(adjustment_test, indent=2, default=safe_json),
        "```",
        "",
        "## Terminal-label status",
        "",
        audit["terminal_semantics"]["reason"],
        "",
        "No model was trained by this audit.",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(
        json.dumps(
            {
                "classification": audit["classification"],
                "sid_count": audit["sid_count"],
                "delisted_or_ended_count": audit["delisted_or_ended_count"],
                "hard_gates": audit["hard_gates"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
