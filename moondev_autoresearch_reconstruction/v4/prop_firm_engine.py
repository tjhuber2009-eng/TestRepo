"""Path-dependent prop-firm optimizer for AUTORESEARCH v4.

Private-account optimization and prop optimization are intentionally separate.
Prop strategies are ranked by challenge/verification pass probability and
survival-adjusted reward potential, not by long-run CAGR.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .account_profiles import PropFirmProgram, PropStageRule


@dataclass
class StageSimulation:
    stage_id: str
    paths: int
    pass_probability: float
    fail_probability: float
    timeout_probability: float
    daily_loss_breach_probability: float
    max_loss_breach_probability: float
    median_days_to_pass: float | None
    p75_days_to_pass: float | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FundedSimulation:
    paths: int
    reward_window_days: int
    survival_probability: float
    positive_reward_probability: float
    expected_reward_pct: float
    median_positive_reward_pct: float | None
    daily_loss_breach_probability: float
    max_loss_breach_probability: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PropOptimizationCandidate:
    exposure_scale: float
    challenge: StageSimulation
    verification: StageSimulation | None
    funded: FundedSimulation
    combined_evaluation_pass_probability: float
    expected_evaluation_days_if_passed: float | None
    payout_efficiency_score: float

    def to_dict(self) -> dict:
        return {
            "exposure_scale": self.exposure_scale,
            "challenge": self.challenge.to_dict(),
            "verification": None if self.verification is None else self.verification.to_dict(),
            "funded": self.funded.to_dict(),
            "combined_evaluation_pass_probability": self.combined_evaluation_pass_probability,
            "expected_evaluation_days_if_passed": self.expected_evaluation_days_if_passed,
            "payout_efficiency_score": self.payout_efficiency_score,
        }


@dataclass
class PropOptimizationResult:
    program: dict
    selected: PropOptimizationCandidate | None
    candidates: list[PropOptimizationCandidate]
    input_precision: str
    objective: str

    def to_dict(self) -> dict:
        return {
            "program": self.program,
            "selected": None if self.selected is None else self.selected.to_dict(),
            "candidates": [x.to_dict() for x in self.candidates],
            "input_precision": self.input_precision,
            "objective": self.objective,
        }


def _moving_block_sample(
    n: int,
    length: int,
    block: int,
    rng: np.random.Generator,
) -> np.ndarray:
    out: list[int] = []
    while len(out) < length:
        start = int(rng.integers(0, max(n - block + 1, 1)))
        out.extend(range(start, min(start + block, n)))
    return np.asarray(out[:length], dtype=int)


def _simulate_one_stage(
    daily_returns: np.ndarray,
    daily_adverse: np.ndarray,
    active: np.ndarray,
    rule: PropStageRule,
) -> tuple[str, int, str | None]:
    balance = 1.0
    peak_eod = 1.0
    trade_days = 0
    target = None if rule.profit_target_pct is None else 1.0 + rule.profit_target_pct / 100.0

    for day in range(min(len(daily_returns), rule.horizon_days)):
        start_balance = balance
        if bool(active[day]):
            trade_days += 1

        worst_equity = start_balance * (1.0 + float(daily_adverse[day]))
        daily_floor = start_balance * (1.0 - rule.max_daily_loss_pct / 100.0)
        if worst_equity <= daily_floor:
            return "fail", day + 1, "daily_loss"

        if rule.trailing_max_loss:
            overall_floor = max(1.0, peak_eod) - rule.max_loss_pct / 100.0
        else:
            overall_floor = 1.0 - rule.max_loss_pct / 100.0
        if worst_equity <= overall_floor:
            return "fail", day + 1, "max_loss"

        balance = start_balance * (1.0 + float(daily_returns[day]))
        peak_eod = max(peak_eod, balance)

        if target is not None and balance >= target and trade_days >= rule.min_trading_days:
            return "pass", day + 1, None

    return "timeout", min(len(daily_returns), rule.horizon_days), None


def simulate_stage(
    returns: Sequence[float],
    adverse_returns: Sequence[float],
    active_days: Sequence[bool],
    rule: PropStageRule,
    *,
    paths: int = 2000,
    block: int = 10,
    seed: int = 20260905,
) -> StageSimulation:
    r = np.asarray(returns, dtype=float)
    a = np.asarray(adverse_returns, dtype=float)
    active = np.asarray(active_days, dtype=bool)
    good = np.isfinite(r) & np.isfinite(a)
    r, a, active = r[good], a[good], active[good]
    if len(r) < 50:
        raise ValueError("prop simulation requires >=50 aligned daily observations")

    rng = np.random.default_rng(seed)
    passed = failed = timed = daily_breach = max_breach = 0
    pass_days: list[int] = []
    for _ in range(int(paths)):
        idx = _moving_block_sample(len(r), rule.horizon_days, int(block), rng)
        status, days, reason = _simulate_one_stage(r[idx], a[idx], active[idx], rule)
        if status == "pass":
            passed += 1
            pass_days.append(days)
        elif status == "fail":
            failed += 1
            daily_breach += int(reason == "daily_loss")
            max_breach += int(reason == "max_loss")
        else:
            timed += 1

    p = float(paths)
    return StageSimulation(
        stage_id=rule.id,
        paths=int(paths),
        pass_probability=passed / p,
        fail_probability=failed / p,
        timeout_probability=timed / p,
        daily_loss_breach_probability=daily_breach / p,
        max_loss_breach_probability=max_breach / p,
        median_days_to_pass=(None if not pass_days else float(np.median(pass_days))),
        p75_days_to_pass=(None if not pass_days else float(np.quantile(pass_days, 0.75))),
    )


def simulate_funded_reward(
    returns: Sequence[float],
    adverse_returns: Sequence[float],
    active_days: Sequence[bool],
    program: PropFirmProgram,
    *,
    paths: int = 2000,
    block: int = 10,
    seed: int = 20260906,
) -> FundedSimulation:
    r = np.asarray(returns, dtype=float)
    a = np.asarray(adverse_returns, dtype=float)
    active = np.asarray(active_days, dtype=bool)
    good = np.isfinite(r) & np.isfinite(a)
    r, a, active = r[good], a[good], active[good]
    days = int(program.first_reward_eligible_days)
    rng = np.random.default_rng(seed)

    survived = positive = daily_breach = max_breach = 0
    reward_pcts: list[float] = []
    for _ in range(int(paths)):
        idx = _moving_block_sample(len(r), days, int(block), rng)
        balance = 1.0
        peak_eod = 1.0
        failed_reason = None
        for j in idx:
            start = balance
            worst = start * (1.0 + float(a[j]))
            daily_floor = start * (1.0 - program.funded.max_daily_loss_pct / 100.0)
            if worst <= daily_floor:
                failed_reason = "daily_loss"
                break
            if program.funded.trailing_max_loss:
                floor = max(1.0, peak_eod) - program.funded.max_loss_pct / 100.0
            else:
                floor = 1.0 - program.funded.max_loss_pct / 100.0
            if worst <= floor:
                failed_reason = "max_loss"
                break
            balance = start * (1.0 + float(r[j]))
            peak_eod = max(peak_eod, balance)

        if failed_reason is not None:
            daily_breach += int(failed_reason == "daily_loss")
            max_breach += int(failed_reason == "max_loss")
            continue
        survived += 1
        reward = max(balance - 1.0, 0.0) * program.reward_share * 100.0
        reward_pcts.append(float(reward))
        positive += int(reward > 0.0)

    rewards = np.asarray(reward_pcts, dtype=float)
    return FundedSimulation(
        paths=int(paths),
        reward_window_days=days,
        survival_probability=survived / float(paths),
        positive_reward_probability=positive / float(paths),
        expected_reward_pct=(0.0 if rewards.size == 0 else float(rewards.sum() / paths)),
        median_positive_reward_pct=(
            None if not np.any(rewards > 0.0) else float(np.median(rewards[rewards > 0.0]))
        ),
        daily_loss_breach_probability=daily_breach / float(paths),
        max_loss_breach_probability=max_breach / float(paths),
    )


def optimize_prop_exposure(
    returns: Sequence[float],
    adverse_returns: Sequence[float],
    active_days: Sequence[bool],
    program: PropFirmProgram,
    *,
    exposure_scales: Sequence[float] = tuple(np.round(np.arange(0.10, 1.51, 0.05), 2)),
    paths: int = 1500,
    block: int = 10,
    seed: int = 20260905,
    input_precision: str = "daily_ohlc_conservative_proxy",
) -> PropOptimizationResult:
    base_r = np.asarray(returns, dtype=float)
    base_a = np.asarray(adverse_returns, dtype=float)
    active = np.asarray(active_days, dtype=bool)
    candidates: list[PropOptimizationCandidate] = []

    for i, scale in enumerate(exposure_scales):
        s = float(scale)
        r = base_r * s
        a = base_a * s
        challenge = simulate_stage(
            r, a, active, program.challenge,
            paths=paths, block=block, seed=seed + 1000 * i,
        )
        verification = None
        if program.verification is not None:
            verification = simulate_stage(
                r, a, active, program.verification,
                paths=paths, block=block, seed=seed + 1000 * i + 1,
            )
        funded = simulate_funded_reward(
            r, a, active, program,
            paths=paths, block=block, seed=seed + 1000 * i + 2,
        )

        eval_pass = challenge.pass_probability
        eval_days = challenge.median_days_to_pass
        if verification is not None:
            eval_pass *= verification.pass_probability
            if eval_days is not None and verification.median_days_to_pass is not None:
                eval_days += verification.median_days_to_pass
            else:
                eval_days = None

        denominator = max(
            (eval_days or float(program.challenge.horizon_days))
            + program.first_reward_eligible_days,
            1.0,
        )
        score = (
            eval_pass
            * funded.survival_probability
            * funded.expected_reward_pct
            / denominator
        )
        candidates.append(
            PropOptimizationCandidate(
                exposure_scale=s,
                challenge=challenge,
                verification=verification,
                funded=funded,
                combined_evaluation_pass_probability=float(eval_pass),
                expected_evaluation_days_if_passed=eval_days,
                payout_efficiency_score=float(score),
            )
        )

    candidates.sort(
        key=lambda x: (
            x.payout_efficiency_score,
            x.combined_evaluation_pass_probability,
            x.funded.expected_reward_pct,
            -x.exposure_scale,
        ),
        reverse=True,
    )
    return PropOptimizationResult(
        program=program.to_dict(),
        selected=(candidates[0] if candidates else None),
        candidates=candidates,
        input_precision=input_precision,
        objective=(
            "maximize combined evaluation-pass probability x funded survival x "
            "expected first-reward percent per expected evaluation+reward day"
        ),
    )


def daily_adverse_proxy(
    market_data: Mapping[str, pd.DataFrame],
    execution_weights: pd.DataFrame,
    costs: pd.Series | None = None,
) -> pd.Series:
    """Conservative daily OHLC adverse P&L proxy for prop rule screening.

    Long positions use Low/Open; shorts use High/Open. This is suitable for
    development screening only. Exact firm daily-loss compliance requires
    intraday equity paths aligned to the firm's reset timezone.
    """
    idx = execution_weights.index
    out = pd.Series(0.0, index=idx)
    for symbol in execution_weights.columns:
        if symbol not in market_data:
            continue
        frame = market_data[symbol].reindex(idx)
        w = execution_weights[symbol].astype(float)
        open_ = pd.to_numeric(frame["Open"], errors="coerce")
        low = pd.to_numeric(frame["Low"], errors="coerce")
        high = pd.to_numeric(frame["High"], errors="coerce")
        long_bad = low / open_ - 1.0
        short_bad = high / open_ - 1.0
        contribution = pd.Series(
            np.where(w >= 0.0, w * long_bad, w * short_bad),
            index=idx,
        )
        out += contribution.fillna(0.0)
    if costs is not None:
        out -= costs.reindex(idx).fillna(0.0)
    return out


def active_day_proxy(execution_weights: pd.DataFrame) -> pd.Series:
    return execution_weights.abs().sum(axis=1) > 1e-12
