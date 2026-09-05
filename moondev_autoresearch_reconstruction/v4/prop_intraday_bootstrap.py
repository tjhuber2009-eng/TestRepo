"""Intraday prop-firm development optimizer aligned to Prague reset days.

Uses checksum-verified Binance 1h BTC/ETH spot history as a price proxy for
FTMO BTCUSD/ETHUSD CFDs.  This is materially more faithful than the daily
screen because it reconstructs intraday equity paths and resets FTMO daily
limits at midnight Europe/Prague.
"""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from .account_profiles import FTMO_1STEP, FTMO_2STEP
from .live_bootstrap import json_safe
from .multi_asset_engine import AssetCost, MultiAssetBacktester, PortfolioLimits
from .prop_firm_engine import active_day_proxy, optimize_prop_exposure
from .risk_overlays import volatility_target_overlay


PRAGUE = "Europe/Prague"
PROP_SCALES = tuple(np.round(np.arange(0.05, 1.01, 0.05), 2))


def research_commit_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def read_hourly(path: Path) -> pd.DataFrame:
    x = pd.read_csv(path)
    if "Date" not in x:
        raise ValueError(f"{path}: Date required")
    idx = pd.to_datetime(x.pop("Date"), utc=True, format="mixed")
    x.index = pd.DatetimeIndex(idx)
    x.index.name = "Date"
    if x.index.has_duplicates or not x.index.is_monotonic_increasing:
        raise ValueError(f"{path}: invalid timestamp ordering")
    for c in ("Open", "High", "Low", "Close", "Volume"):
        if c in x:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    if x[["Open", "High", "Low", "Close"]].isna().any().any():
        raise ValueError(f"{path}: invalid OHLC")
    if len(x) and x.index.max().tz_convert("UTC").tz_localize(None) >= pd.Timestamp("2021-01-01"):
        raise RuntimeError("intraday prop development data crosses sealed boundary")
    return x


def load_data(root: Path) -> dict[str, pd.DataFrame]:
    mapping = {
        "BTCUSDT": "btc_1h.csv",
        "ETHUSDT": "eth_1h.csv",
        "BNBUSDT": "bnb_1h.csv",
        "LTCUSDT": "ltc_1h.csv",
    }
    required = {"BTCUSDT", "ETHUSDT"}
    out = {}
    for symbol, name in mapping.items():
        p = root / name
        if p.exists():
            out[symbol] = read_hourly(p)
        elif symbol in required:
            raise FileNotFoundError(p)
    common = None
    for frame in out.values():
        common = frame.index if common is None else common.intersection(frame.index)
    if common is None or len(common) < 24 * 300:
        raise RuntimeError("insufficient common hourly history")
    return {s: x.loc[common].copy() for s, x in out.items()}


def hourly_rotation_strategy(params, symbols):
    lookback = int(params["lookback"])
    trend = int(params["trend"])
    top_k = int(params["top_k"])

    def strategy(data, features=None):
        index = next(iter(data.values())).index
        out = pd.DataFrame(0.0, index=index, columns=sorted(data))
        momentum = pd.DataFrame({
            s: data[s]["Close"] / data[s]["Close"].shift(lookback) - 1.0
            for s in symbols
        })
        healthy = pd.DataFrame({
            s: data[s]["Close"] > data[s]["Close"].rolling(
                trend, min_periods=trend
            ).mean()
            for s in symbols
        })

        for ts in index:
            row = momentum.loc[ts].where(healthy.loc[ts]).dropna()
            row = row[row > 0.0].sort_values(ascending=False).head(top_k)
            if len(row):
                out.loc[ts, row.index] = 1.0 / len(row)

        # Force exposure to zero for execution at the first hourly bar of each
        # new Prague day. The next bar may reopen. This makes the midnight
        # balance/equity reset explicit and realizes daily P/L.
        local_dates = pd.Series(
            index.tz_convert(PRAGUE).date,
            index=index,
        )
        next_dates = local_dates.shift(-1)
        reset_target_rows = next_dates.notna() & (next_dates != local_dates)
        out.loc[reset_target_rows] = 0.0
        return out

    return strategy


def intraday_bar_adverse(
    data: dict[str, pd.DataFrame],
    weights: pd.DataFrame,
    costs: pd.Series,
) -> pd.Series:
    idx = weights.index
    out = pd.Series(0.0, index=idx)
    for symbol in weights.columns:
        frame = data[symbol].reindex(idx)
        w = weights[symbol].astype(float)
        open_ = frame["Open"]
        low = frame["Low"]
        high = frame["High"]
        long_bad = low / open_ - 1.0
        short_bad = high / open_ - 1.0
        out += pd.Series(
            np.where(w >= 0.0, w * long_bad, w * short_bad),
            index=idx,
        ).fillna(0.0)
    out -= costs.reindex(idx).fillna(0.0)
    return out


def aggregate_prague_days(
    bar_returns: pd.Series,
    bar_adverse: pd.Series,
    weights: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    idx = bar_returns.index
    local_date = pd.Index(idx.tz_convert(PRAGUE).date, name="PragueDate")
    groups = pd.Series(np.arange(len(idx)), index=idx).groupby(local_date)

    daily_return = {}
    daily_adverse = {}
    opened_day = {}

    opened_bar = active_day_proxy(weights).reindex(idx).fillna(False)

    for day, positions in groups:
        pos = positions.to_numpy(dtype=int)
        eq = 1.0
        worst = 1.0
        for i in pos:
            # Worst intrabar equity occurs before the bar's marked close.
            worst = min(worst, eq * (1.0 + float(bar_adverse.iloc[i])))
            eq *= 1.0 + float(bar_returns.iloc[i])
        daily_return[day] = eq - 1.0
        daily_adverse[day] = worst - 1.0
        opened_day[day] = bool(opened_bar.iloc[pos].any())

    index = pd.to_datetime(list(daily_return.keys()))
    return (
        pd.Series(list(daily_return.values()), index=index, name="return"),
        pd.Series(list(daily_adverse.values()), index=index, name="adverse"),
        pd.Series(list(opened_day.values()), index=index, name="opened").astype(bool),
    )



def aggregate_prague_days_scaled(
    bar_returns: pd.Series,
    bar_adverse: pd.Series,
    weights: pd.DataFrame,
    scales=PROP_SCALES,
) -> tuple[
    dict[float, pd.Series],
    dict[float, pd.Series],
    pd.Series,
]:
    """Apply each prop exposure scale before intraday/day compounding."""
    idx = bar_returns.index
    local_date = pd.Index(idx.tz_convert(PRAGUE).date, name="PragueDate")
    groups = pd.Series(np.arange(len(idx)), index=idx).groupby(local_date)
    opened_bar = active_day_proxy(weights).reindex(idx).fillna(False)
    scales = tuple(float(x) for x in scales)

    daily_r = {scale: {} for scale in scales}
    daily_a = {scale: {} for scale in scales}
    opened_day = {}

    for day, positions in groups:
        pos = positions.to_numpy(dtype=int)
        opened_day[day] = bool(opened_bar.iloc[pos].any())
        for scale in scales:
            eq = 1.0
            worst = 1.0
            for i in pos:
                worst = min(
                    worst,
                    eq * (1.0 + scale * float(bar_adverse.iloc[i])),
                )
                eq *= 1.0 + scale * float(bar_returns.iloc[i])
            daily_r[scale][day] = eq - 1.0
            daily_a[scale][day] = worst - 1.0

    days = pd.to_datetime(list(opened_day.keys()))
    returns = {
        scale: pd.Series(
            [daily_r[scale][day] for day in opened_day],
            index=days,
            name="return",
        )
        for scale in scales
    }
    adverse = {
        scale: pd.Series(
            [daily_a[scale][day] for day in opened_day],
            index=days,
            name="adverse",
        )
        for scale in scales
    }
    opened = pd.Series(
        list(opened_day.values()),
        index=days,
        name="opened",
    ).astype(bool)
    return returns, adverse, opened


def evaluate_strategy(data, params):
    """Build one causal hourly strategy path, reusable across prop programs."""
    symbols = tuple(sorted(data))
    costs = {
        s: AssetCost(commission_bps=3.25, slippage_bps=2.0)
        for s in symbols
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
        periods_per_year=365.0 * 24.0,
    )
    base_strategy = hourly_rotation_strategy(params, symbols)
    strategy = volatility_target_overlay(
        base_strategy,
        target_vol=float(params["vol_target"]),
        periods_per_year=365.0 * 24.0,
        lookback=int(params["vol_lookback"]),
        max_gross=1.0,
        max_scale=1.0,
    )
    result = engine.run(strategy, cost_multiplier=3.0)
    bar_adverse = intraday_bar_adverse(
        data,
        result.execution_weights,
        result.costs,
    ).reindex(result.returns.index)
    aligned_weights = result.execution_weights.reindex(result.returns.index)
    daily_ret, daily_adv, opened = aggregate_prague_days(
        result.returns,
        bar_adverse,
        aligned_weights,
    )
    scaled_ret, scaled_adv, scaled_opened = aggregate_prague_days_scaled(
        result.returns,
        bar_adverse,
        aligned_weights,
        PROP_SCALES,
    )
    if not opened.equals(scaled_opened):
        raise RuntimeError("scaled intraday aggregation changed trading-day flags")
    return (
        result,
        daily_ret,
        daily_adv,
        opened,
        scaled_ret,
        scaled_adv,
    )


def evaluate_family(data, params, program, *, paths, seed):
    (
        result,
        daily_ret,
        daily_adv,
        opened,
        scaled_ret,
        scaled_adv,
    ) = evaluate_strategy(data, params)
    prop = optimize_prop_exposure(
        daily_ret.to_numpy(dtype=float),
        daily_adv.to_numpy(dtype=float),
        opened.to_numpy(dtype=bool),
        program,
        exposure_scales=PROP_SCALES,
        paths=paths,
        block=10,
        seed=seed,
        input_precision=(
            "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
            "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
        ),
        prescaled_returns_by_scale=scaled_ret,
        prescaled_adverse_by_scale=scaled_adv,
    )
    return result, daily_ret, daily_adv, prop


def _frontier_rank(view_name, candidate):
    if candidate is None:
        return (-1e99,)
    if view_name == "max_evaluation_pass":
        days = (
            1e99
            if candidate.expected_evaluation_days_if_passed is None
            else float(candidate.expected_evaluation_days_if_passed)
        )
        return (
            float(candidate.combined_evaluation_pass_probability),
            -days,
            float(candidate.payout_efficiency_score),
            float(candidate.funded.survival_probability),
        )
    if view_name == "safest_funded":
        return (
            float(candidate.funded.survival_probability),
            -float(candidate.funded.daily_loss_breach_probability),
            -float(candidate.funded.max_loss_breach_probability),
            float(candidate.funded.expected_reward_pct),
        )
    return (
        float(candidate.payout_efficiency_score),
        float(candidate.combined_evaluation_pass_probability),
        float(candidate.funded.survival_probability),
    )


def run(data_dir: str | Path, output: str | Path) -> dict:
    data = load_data(Path(data_dir))

    params_grid = [
        {
            "lookback": lb,
            "trend": tr,
            "top_k": k,
            "vol_target": vt,
            "vol_lookback": vl,
        }
        for lb in (72, 168, 336)
        for tr in (168, 336)
        for k in (1, 2)
        for vt in (0.40, 0.60, 0.80)
        for vl in (72, 168)
    ]

    programs = [FTMO_2STEP, FTMO_1STEP]
    view_names = (
        "max_payout_efficiency",
        "max_evaluation_pass",
        "safest_funded",
        "balanced",
        "conservative",
    )
    rows_by_program = {program.id: [] for program in programs}
    leaders = {
        program.id: {name: None for name in view_names}
        for program in programs
    }

    # Strategy construction is independent of prop-program rules. Build it
    # once per parameter set, then evaluate 1-Step and 2-Step on the same
    # daily return/adverse path. The same bootstrap seed is also reused across
    # parameter sets within a program to reduce Monte Carlo ranking noise.
    for i, params in enumerate(params_grid):
        (
            base,
            dret,
            dadv,
            opened,
            scaled_ret,
            scaled_adv,
        ) = evaluate_strategy(data, params)
        for pidx, program in enumerate(programs):
            prop = optimize_prop_exposure(
                dret.to_numpy(dtype=float),
                dadv.to_numpy(dtype=float),
                opened.to_numpy(dtype=bool),
                program,
                exposure_scales=PROP_SCALES,
                paths=400,
                block=10,
                seed=20261000 + pidx * 100000,
                input_precision=(
                    "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
                    "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
                ),
                prescaled_returns_by_scale=scaled_ret,
                prescaled_adverse_by_scale=scaled_adv,
            )
            sel = prop.selected
            rows_by_program[program.id].append({
                "params": params,
                "hourly_base_cagr_pct": base.metrics.cagr_pct,
                "hourly_base_max_dd_pct": base.metrics.max_dd_pct,
                "daily_worst_adverse_pct": float(dadv.min() * 100.0),
                "selected": None if sel is None else sel.to_dict(),
            })

            for view_name in view_names:
                candidate = prop.views.get(view_name)
                if candidate is None:
                    continue
                current = leaders[program.id][view_name]
                if (
                    current is None
                    or _frontier_rank(view_name, candidate)
                    > _frontier_rank(view_name, current["candidate"])
                ):
                    leaders[program.id][view_name] = {
                        "params": dict(params),
                        "candidate": candidate,
                    }

    program_results = {}
    for pidx, program in enumerate(programs):
        rows = rows_by_program[program.id]
        rows.sort(
            key=lambda x: (
                -1e99
                if x["selected"] is None
                else x["selected"]["payout_efficiency_score"],
                -1e99
                if x["selected"] is None
                else x["selected"]["combined_evaluation_pass_probability"],
            ),
            reverse=True,
        )

        refined_cache = {}
        refined_frontiers = {}
        for view_name in view_names:
            leader = leaders[program.id][view_name]
            if leader is None:
                refined_frontiers[view_name] = None
                continue
            params = leader["params"]
            key = tuple(sorted(params.items()))
            if key not in refined_cache:
                (
                    base,
                    d_ret,
                    d_adv,
                    opened,
                    scaled_ret,
                    scaled_adv,
                ) = evaluate_strategy(data, params)
                final_prop = optimize_prop_exposure(
                    d_ret.to_numpy(dtype=float),
                    d_adv.to_numpy(dtype=float),
                    opened.to_numpy(dtype=bool),
                    program,
                    exposure_scales=PROP_SCALES,
                    paths=4000,
                    block=10,
                    seed=20269900 + pidx * 100000,
                    input_precision=(
                        "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
                        "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
                    ),
                    prescaled_returns_by_scale=scaled_ret,
                    prescaled_adverse_by_scale=scaled_adv,
                )
                refined_cache[key] = {
                    "base": base,
                    "daily_ret": d_ret,
                    "daily_adv": d_adv,
                    "optimization": final_prop,
                }

            ref = refined_cache[key]
            candidate = ref["optimization"].views.get(view_name)
            refined_frontiers[view_name] = {
                "params": params,
                "days": int(len(ref["daily_ret"])),
                "worst_prague_day_adverse_pct": float(
                    ref["daily_adv"].min() * 100.0
                ),
                "view": (
                    None if candidate is None else candidate.to_dict()
                ),
            }

        max_payout = refined_frontiers["max_payout_efficiency"]
        refined_winner = None
        if max_payout is not None:
            key = tuple(sorted(max_payout["params"].items()))
            ref = refined_cache[key]
            refined_winner = {
                "params": max_payout["params"],
                "days": max_payout["days"],
                "worst_prague_day_adverse_pct": (
                    max_payout["worst_prague_day_adverse_pct"]
                ),
                "optimization": ref["optimization"].to_dict(),
            }

        program_results[program.id] = {
            "program": program.to_dict(),
            "parameter_candidates": len(rows),
            "development_leaderboard": rows,
            "coarse_frontier_leaders": {
                name: (
                    None
                    if leaders[program.id][name] is None
                    else {
                        "params": leaders[program.id][name]["params"],
                        "view": leaders[program.id][name][
                            "candidate"
                        ].to_dict(),
                    }
                )
                for name in view_names
            },
            "refined_frontiers": refined_frontiers,
            "refined_winner": refined_winner,
        }

    payload = {
        "protocol": "alpha_generation_v4",
        "track": "prop_firm_intraday",
        "stage": "development_only",
        "research_commit_sha": research_commit_sha(),
        "exposure_scaling_method": (
            "stage exposure applied to each hourly portfolio return/adverse "
            "before Prague-day compounding"
        ),
        "data_end": max(
            frame.index.max().tz_convert("UTC").strftime("%Y-%m-%d")
            for frame in data.values()
        ),
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "reset_timezone": PRAGUE,
        "policy": "force flat for execution at each Prague midnight reset",
        "market_mapping": {
            symbol: {
                "research_source": (
                    "Binance spot 1h, monthly archive checksums verified"
                ),
                "intended_prop_symbol": {
                    "BTCUSDT": "BTCUSD",
                    "ETHUSDT": "ETHUSD",
                    "BNBUSDT": "BNBUSD",
                    "LTCUSDT": "LTCUSD",
                }[symbol],
                "venue_execution_verified": False,
            }
            for symbol in sorted(data)
        },
        "deployment_blockers": [
            "FTMO CFD tick/spread/slippage history still differs from Binance spot",
            "FTMO-specific swap history not reconstructed",
            "exact platform execution must be forward-tested before funded deployment",
        ],
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
    ap.add_argument("--data-dir", default="v4_prop_intraday_data")
    ap.add_argument("--output", default="v4_state/prop-intraday-bootstrap.json")
    args = ap.parse_args()
    x = run(args.data_dir, args.output)
    summary = {}
    for key, row in x["programs"].items():
        ref = row["refined_winner"]
        sel = None if ref is None else ref["optimization"]["selected"]
        summary[key] = None if sel is None else {
            "params": ref["params"],
            "challenge_scale": sel["challenge_exposure_scale"],
            "verification_scale": sel["verification_exposure_scale"],
            "funded_scale": sel["funded_exposure_scale"],
            "combined_pass_probability": sel["combined_evaluation_pass_probability"],
            "expected_reward_pct": sel["funded"]["expected_reward_pct"],
            "payout_efficiency_score": sel["payout_efficiency_score"],
        }
    print(json.dumps({
        "track": x["track"],
        "data_end": x["data_end"],
        "hidden_validation_opened": x["hidden_validation_opened"],
        "final_oos_opened": x["final_oos_opened"],
        "winners": summary,
    }, indent=2, allow_nan=False))
