"""Development-only prop-firm optimization on prop-compatible crypto proxies.

This track is deliberately separate from private-account optimization.  It
ranks strategies by challenge/verification pass probability and payout
efficiency under firm rules, not by CAGR.
"""
from __future__ import annotations

from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd

from .account_profiles import FTMO_1STEP, FTMO_2STEP
from .campaign import assert_v4_data_boundary
from .live_bootstrap import json_safe, read_market_csv
from .multi_asset_engine import AssetCost, MultiAssetBacktester, PortfolioLimits
from .prop_firm_engine import (
    active_day_proxy,
    daily_adverse_proxy,
    optimize_prop_exposure,
)
from .strategy_examples import cross_sectional_momentum_rotation


def load_prop_data(root: Path) -> dict[str, pd.DataFrame]:
    mapping = {
        "BTCUSDT": "btc_1d.csv",
        "ETHUSDT": "eth_1d.csv",
    }
    out = {}
    for symbol, name in mapping.items():
        p = root / name
        if p.exists():
            out[symbol] = read_market_csv(p)
    if set(out) != set(mapping):
        raise RuntimeError("BTC and ETH development data are required")
    assert_v4_data_boundary(out, stage="development")
    return out


def build_strategy(params, symbols):
    return cross_sectional_momentum_rotation(
        lookback=int(params["lookback"]),
        trend_window=int(params["trend"]),
        top_k=int(params["top_k"]),
        gross_weight=1.0,
        eligible_symbols=tuple(symbols),
    )


def evaluate_strategy(
    data,
    params,
    *,
    program,
    paths,
    seed,
):
    # FTMO published crypto commission is venue-specific; this proxy adds
    # commission plus slippage and then stresses total costs 3x.
    costs = {
        s: AssetCost(commission_bps=3.25, slippage_bps=2.0)
        for s in data
    }
    engine = MultiAssetBacktester(
        data,
        costs=costs,
        limits=PortfolioLimits(
            gross_leverage=1.0,
            net_min=0.0,
            net_max=1.0,
            per_asset_abs_weight=1.0,
        ),
        periods_per_year=365.0,
    )
    result = engine.run(
        build_strategy(params, tuple(data)),
        cost_multiplier=3.0,
    )
    adverse = daily_adverse_proxy(
        data,
        result.execution_weights,
        result.costs,
    ).reindex(result.returns.index)
    active = active_day_proxy(
        result.execution_weights
    ).reindex(result.returns.index).fillna(False)

    prop = optimize_prop_exposure(
        result.returns.to_numpy(dtype=float),
        adverse.to_numpy(dtype=float),
        active.to_numpy(dtype=bool),
        program,
        exposure_scales=tuple(np.round(np.arange(0.10, 1.01, 0.05), 2)),
        paths=paths,
        block=10,
        seed=seed,
        input_precision="daily_ohlc_conservative_proxy_binance_spot_to_ftmo_crypto_cfd",
    )
    return result, prop


def run(data_dir: str | Path, output: str | Path) -> dict:
    data = load_prop_data(Path(data_dir))
    params_grid = [
        {"lookback": lb, "trend": tr, "top_k": k}
        for lb in (20, 60, 126)
        for tr in (50, 100, 200)
        for k in (1, 2)
    ]

    programs = [FTMO_2STEP, FTMO_1STEP]
    program_results = {}
    for pidx, program in enumerate(programs):
        rows = []
        for i, params in enumerate(params_grid):
            base, prop = evaluate_strategy(
                data,
                params,
                program=program,
                paths=500,
                seed=20260905 + pidx * 100000 + i * 1000,
            )
            selected = prop.selected
            rows.append({
                "params": params,
                "base_cagr_pct": base.metrics.cagr_pct,
                "base_max_dd_pct": base.metrics.max_dd_pct,
                "selected": None if selected is None else selected.to_dict(),
            })

        rows.sort(
            key=lambda x: (
                -1e99 if x["selected"] is None else x["selected"]["payout_efficiency_score"],
                -1e99 if x["selected"] is None else x["selected"]["combined_evaluation_pass_probability"],
            ),
            reverse=True,
        )
        best = rows[0] if rows else None

        # Re-estimate the winner with materially more bootstrap paths.
        final = None
        if best is not None:
            _, refined = evaluate_strategy(
                data,
                best["params"],
                program=program,
                paths=4000,
                seed=20269999 + pidx * 100000,
            )
            final = {
                "params": best["params"],
                "optimization": refined.to_dict(),
            }

        program_results[program.id] = {
            "program": program.to_dict(),
            "parameter_candidates": len(rows),
            "development_leaderboard": rows,
            "refined_winner": final,
        }

    payload = {
        "protocol": "alpha_generation_v4",
        "track": "prop_firm",
        "stage": "development_only",
        "data_end": max(x.index.max().strftime("%Y-%m-%d") for x in data.values()),
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "objective": (
            "separate prop optimization: maximize evaluation pass probability "
            "and survival-adjusted reward efficiency"
        ),
        "market_mapping": {
            "BTCUSDT": {
                "research_source": "Binance spot development history",
                "intended_prop_symbol": "BTCUSD",
                "venue_execution_verified": False,
            },
            "ETHUSDT": {
                "research_source": "Binance spot development history",
                "intended_prop_symbol": "ETHUSD",
                "venue_execution_verified": False,
            },
        },
        "deployment_blockers": [
            "exact FTMO CFD spreads/swaps/session history not yet reconstructed",
            "daily-loss screening currently uses conservative daily OHLC adverse proxy",
            "intraday equity path aligned to FTMO reset timezone required before deployment",
        ],
        "private_track_independent": True,
        "programs": program_results,
    }

    safe = json_safe(payload)
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(safe, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return safe


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="v4_prop_data")
    ap.add_argument("--output", default="v4_state/prop-bootstrap.json")
    args = ap.parse_args()
    x = run(args.data_dir, args.output)
    summary = {}
    for key, row in x["programs"].items():
        winner = row["refined_winner"]
        sel = None if winner is None else winner["optimization"]["selected"]
        summary[key] = None if sel is None else {
            "params": winner["params"],
            "exposure_scale": sel["exposure_scale"],
            "combined_evaluation_pass_probability": sel["combined_evaluation_pass_probability"],
            "expected_reward_pct": sel["funded"]["expected_reward_pct"],
            "payout_efficiency_score": sel["payout_efficiency_score"],
        }
    print(json.dumps({
        "stage": x["stage"],
        "hidden_validation_opened": x["hidden_validation_opened"],
        "final_oos_opened": x["final_oos_opened"],
        "winners": summary,
    }, indent=2, allow_nan=False))
