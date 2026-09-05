"""Reconcile AUTORESEARCH v4 evidence data at the return level.

Cross-source comparison intentionally uses open-to-open PRICE returns because
Yahoo OHLC are split-adjusted while its dividend cash events can use a different
split basis. Total-return integrity is checked independently inside Tiingo by
comparing raw OHLC + explicit corporate actions with Tiingo adjusted prices.
No hidden-validation or final-OOS data is permitted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def open_to_open_total_returns(path: Path) -> pd.Series:
    x = pd.read_csv(path)
    if "Date" not in x or "Open" not in x:
        raise ValueError(f"{path}: Date and Open required")
    idx = pd.DatetimeIndex(pd.to_datetime(x["Date"], utc=True)).normalize().tz_localize(None)
    open_ = pd.Series(pd.to_numeric(x["Open"], errors="coerce").to_numpy(), index=idx, dtype=float)
    dividend = pd.Series(
        pd.to_numeric(x.get("Dividend", 0.0), errors="coerce").to_numpy()
        if "Dividend" in x else np.zeros(len(x)),
        index=idx,
        dtype=float,
    )
    if idx.has_duplicates:
        raise ValueError(f"{path}: duplicate normalized dates")
    ret = (open_.shift(-1) + dividend.shift(-1)) / open_ - 1.0
    return ret.iloc[:-1].replace([np.inf, -np.inf], np.nan).dropna()


def _cagr(ret: pd.Series, periods_per_year: float = 252.0) -> float | None:
    if len(ret) == 0 or (ret <= -1.0).any():
        return None
    years = len(ret) / periods_per_year
    if years <= 0:
        return None
    growth = float(np.exp(np.log1p(ret.to_numpy(dtype=float)).sum()))
    return (growth ** (1.0 / years) - 1.0) * 100.0


def open_to_open_price_returns(path: Path) -> pd.Series:
    x = pd.read_csv(path)
    if "Date" not in x or "Open" not in x:
        raise ValueError(f"{path}: Date and Open required")
    idx = pd.DatetimeIndex(pd.to_datetime(x["Date"], utc=True)).normalize().tz_localize(None)
    open_ = pd.Series(pd.to_numeric(x["Open"], errors="coerce").to_numpy(), index=idx, dtype=float)
    if idx.has_duplicates:
        raise ValueError(f"{path}: duplicate normalized dates")
    ret = open_.shift(-1) / open_ - 1.0
    return ret.iloc[:-1].replace([np.inf, -np.inf], np.nan).dropna()


def compare_return_files(left: Path, right: Path) -> dict:
    a = open_to_open_price_returns(left).rename("primary")
    b = open_to_open_price_returns(right).rename("reference")
    joined = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(joined) < 20:
        raise RuntimeError(f"insufficient return overlap: {left.name} vs {right.name}")
    diff_bp = (joined["primary"] - joined["reference"]).abs() * 10000.0
    primary_cagr = _cagr(joined["primary"])
    reference_cagr = _cagr(joined["reference"])
    cagr_gap = None if primary_cagr is None or reference_cagr is None else abs(primary_cagr - reference_cagr)
    top = diff_bp.nlargest(min(10, len(diff_bp)))
    p95 = float(diff_bp.quantile(0.95))
    result = {
        "overlap_start": joined.index.min().strftime("%Y-%m-%d"),
        "overlap_end": joined.index.max().strftime("%Y-%m-%d"),
        "observations": int(len(joined)),
        "median_abs_diff_bp": float(diff_bp.median()),
        "p95_abs_diff_bp": p95,
        "max_abs_diff_bp": float(diff_bp.max()),
        "days_over_10bp": int((diff_bp > 10.0).sum()),
        "days_over_50bp": int((diff_bp > 50.0).sum()),
        "primary_cagr_pct": primary_cagr,
        "reference_cagr_pct": reference_cagr,
        "abs_cagr_gap_pct_points": cagr_gap,
        "largest_difference_dates": [
            {"date": idx.strftime("%Y-%m-%d"), "abs_diff_bp": float(value)}
            for idx, value in top.items()
        ],
    }
    result["cross_source_pass"] = bool(
        p95 <= 5.0
        and cagr_gap is not None
        and cagr_gap <= 1.0
    )
    return result


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reconcile(primary_dir: Path, reference_dir: Path, ids: list[str]) -> dict:
    symbols = {}
    for ident in ids:
        left = primary_dir / f"{ident}_1d.csv"
        right = reference_dir / f"{ident}_1d.csv"
        left_manifest = primary_dir / f"{ident}_1d.manifest.json"
        right_manifest = reference_dir / f"{ident}_1d.manifest.json"
        if not left.exists() or not right.exists():
            raise FileNotFoundError(f"missing reconciliation input for {ident}")
        comp = compare_return_files(left, right)
        provider_diag = None
        if left_manifest.exists():
            provider_diag = _load_manifest(left_manifest).get("provider_adjusted_return_diagnostic")
        symbols[ident] = {
            **comp,
            "provider_self_parity": provider_diag,
            "provider_self_parity_pass": bool(provider_diag and provider_diag.get("parity_pass")),
            "primary_manifest_sha256": _load_manifest(left_manifest).get("csv_sha256") if left_manifest.exists() else None,
            "reference_manifest_sha256": _load_manifest(right_manifest).get("csv_sha256") if right_manifest.exists() else None,
        }
    overall = all(
        row["cross_source_pass"] and row["provider_self_parity_pass"]
        for row in symbols.values()
    )
    return {
        "protocol": "alpha_generation_v4",
        "stage": "development_only",
        "comparison": "cross_source_open_to_open_price_return_plus_tiingo_total_return_self_parity",
        "primary_source": "tiingo_raw_plus_explicit_actions",
        "reference_source": "yahoo_provider_split_adjusted_price_shadow",
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "policy": {
            "cross_source_p95_abs_diff_bp_max": 5.0,
            "cross_source_abs_cagr_gap_pct_points_max": 1.0,
            "provider_adjusted_open_parity_required": True,
        },
        "symbols": symbols,
        "reconciliation_pass": overall,
        "promotion_blocked": not overall,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--primary-dir", required=True)
    ap.add_argument("--reference-dir", required=True)
    ap.add_argument("--ids", default="qqq,tqqq,spy,ief,gld,shy")
    ap.add_argument("--output", required=True)
    ap.add_argument("--require-pass", action="store_true")
    args = ap.parse_args()
    payload = reconcile(
        Path(args.primary_dir),
        Path(args.reference_dir),
        [x.strip() for x in args.ids.split(",") if x.strip()],
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "reconciliation_pass": payload["reconciliation_pass"],
        "promotion_blocked": payload["promotion_blocked"],
        "symbols": {
            k: {
                "p95_abs_diff_bp": v["p95_abs_diff_bp"],
                "cagr_gap_pp": v["abs_cagr_gap_pct_points"],
                "provider_self_parity_pass": v["provider_self_parity_pass"],
            }
            for k, v in payload["symbols"].items()
        },
    }, indent=2))
    if args.require_pass and not payload["reconciliation_pass"]:
        raise SystemExit("data reconciliation failed; private-result promotion remains blocked")


if __name__ == "__main__":
    main()
