"""Path-dependent prop-firm optimizer for AUTORESEARCH v4.

Private-account optimization and prop optimization are intentionally separate.
Prop strategies are ranked by evaluation pass probability and expected payout
efficiency under the firm's path-dependent rules, not by long-run CAGR.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .account_profiles import PropFirmProgram, PropStageRule


@dataclass
class StageSimulation:
    stage_id: str
    exposure_scale: float
    paths: int
    analysis_horizon_days: int
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
    exposure_scale: float
    paths: int
    reward_window_days: int
    survival_probability: float
    reward_eligible_probability: float
    positive_reward_probability: float
    expected_reward_pct: float
    median_positive_reward_pct: float | None
    daily_loss_breach_probability: float
    max_loss_breach_probability: float
    best_day_ineligible_probability: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PropOptimizationCandidate:
    challenge_exposure_scale: float
    verification_exposure_scale: float | None
    funded_exposure_scale: float
    challenge: StageSimulation
    verification: StageSimulation | None
    funded: FundedSimulation
    combined_evaluation_pass_probability: float
    expected_evaluation_days_if_passed: float | None
    payout_efficiency_score: float

    def to_dict(self) -> dict:
        return {
            "challenge_exposure_scale": self.challenge_exposure_scale,
            "verification_exposure_scale": self.verification_exposure_scale,
            "funded_exposure_scale": self.funded_exposure_scale,
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
    challenge_scale_table: list[StageSimulation]
    verification_scale_table: list[StageSimulation] | None
    funded_scale_table: list[FundedSimulation]
    input_precision: str
    objective: str
    views: dict[str, PropOptimizationCandidate | None]

    def to_dict(self) -> dict:
        return {
            "program": self.program,
            "selected": None if self.selected is None else self.selected.to_dict(),
            "candidates": [x.to_dict() for x in self.candidates],
            "challenge_scale_table": [x.to_dict() for x in self.challenge_scale_table],
            "verification_scale_table": (
                None if self.verification_scale_table is None
                else [x.to_dict() for x in self.verification_scale_table]
            ),
            "funded_scale_table": [x.to_dict() for x in self.funded_scale_table],
            "input_precision": self.input_precision,
            "objective": self.objective,
            "views": {
                key: (None if value is None else value.to_dict())
                for key, value in self.views.items()
            },
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


def _best_day_ok(
    daily_closed_profit: Sequence[float],
    rule_pct: float | None,
) -> bool:
    if rule_pct is None:
        return True
    pos = np.asarray([max(float(x), 0.0) for x in daily_closed_profit], dtype=float)
    total = float(pos.sum())
    if total <= 0.0:
        return False
    return float(pos.max()) <= total * float(rule_pct) / 100.0 + 1e-12


def _simulate_one_stage(
    daily_returns: np.ndarray,
    daily_adverse: np.ndarray,
    opened_trade_day: np.ndarray,
    rule: PropStageRule,
) -> tuple[str, int, str | None]:
    balance = 1.0
    highest_midnight_balance = 1.0
    trading_days = 0
    closed_daily_profit: list[float] = []
    target = (
        None
        if rule.profit_target_pct is None
        else 1.0 + rule.profit_target_pct / 100.0
    )

    horizon = min(len(daily_returns), int(rule.analysis_horizon_days))
    for day in range(horizon):
        midnight_balance = balance
        if bool(opened_trade_day[day]):
            trading_days += 1

        worst_equity = midnight_balance * (1.0 + float(daily_adverse[day]))

        # FTMO defines the daily loss AMOUNT as a fixed percentage of the
        # initial simulated capital. The daily floor therefore moves with the
        # midnight balance by subtraction, not multiplication.
        daily_floor = midnight_balance - rule.max_daily_loss_pct / 100.0
        if worst_equity <= daily_floor:
            return "fail", day + 1, "daily_loss"

        if rule.trailing_max_loss:
            overall_floor = (
                max(1.0, highest_midnight_balance)
                - rule.max_loss_pct / 100.0
            )
        else:
            overall_floor = 1.0 - rule.max_loss_pct / 100.0
        if worst_equity <= overall_floor:
            return "fail", day + 1, "max_loss"

        balance = midnight_balance * (1.0 + float(daily_returns[day]))
        closed_daily_profit.append(balance - midnight_balance)
        highest_midnight_balance = max(highest_midnight_balance, balance)

        if (
            target is not None
            and balance >= target
            and trading_days >= rule.min_trading_days
            and _best_day_ok(closed_daily_profit, rule.best_day_rule_pct)
        ):
            # Passing assumes the strategy force-closes any remaining position
            # once all objectives are satisfied, as FTMO requires all positions
            # closed before the phase is completed.
            return "pass", day + 1, None

    # FTMO has no maximum evaluation period. "timeout" here only means the
    # target was not reached within our analysis horizon; it is not a firm rule
    # violation.
    return "timeout", horizon, None


def simulate_stage(
    returns: Sequence[float],
    adverse_returns: Sequence[float],
    opened_trade_days: Sequence[bool],
    rule: PropStageRule,
    *,
    exposure_scale: float = 1.0,
    paths: int = 2000,
    block: int = 10,
    seed: int = 20260905,
) -> StageSimulation:
    scale = float(exposure_scale)
    r = np.asarray(returns, dtype=float) * scale
    a = np.asarray(adverse_returns, dtype=float) * scale
    opened = np.asarray(opened_trade_days, dtype=bool)
    good = np.isfinite(r) & np.isfinite(a)
    r, a, opened = r[good], a[good], opened[good]
    if len(r) < 50:
        raise ValueError("prop simulation requires >=50 aligned daily observations")

    rng = np.random.default_rng(seed)
    passed = failed = timed = daily_breach = max_breach = 0
    pass_days: list[int] = []
    for _ in range(int(paths)):
        idx = _moving_block_sample(
            len(r), int(rule.analysis_horizon_days), int(block), rng
        )
        status, days, reason = _simulate_one_stage(
            r[idx], a[idx], opened[idx], rule
        )
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
        exposure_scale=scale,
        paths=int(paths),
        analysis_horizon_days=int(rule.analysis_horizon_days),
        pass_probability=passed / p,
        fail_probability=failed / p,
        timeout_probability=timed / p,
        daily_loss_breach_probability=daily_breach / p,
        max_loss_breach_probability=max_breach / p,
        median_days_to_pass=(
            None if not pass_days else float(np.median(pass_days))
        ),
        p75_days_to_pass=(
            None if not pass_days else float(np.quantile(pass_days, 0.75))
        ),
    )


def simulate_funded_reward(
    returns: Sequence[float],
    adverse_returns: Sequence[float],
    opened_trade_days: Sequence[bool],
    program: PropFirmProgram,
    *,
    exposure_scale: float = 1.0,
    paths: int = 2000,
    block: int = 10,
    seed: int = 20260906,
) -> FundedSimulation:
    scale = float(exposure_scale)
    r = np.asarray(returns, dtype=float) * scale
    a = np.asarray(adverse_returns, dtype=float) * scale
    opened = np.asarray(opened_trade_days, dtype=bool)
    good = np.isfinite(r) & np.isfinite(a)
    r, a, opened = r[good], a[good], opened[good]
    days = int(program.first_reward_eligible_days)
    rng = np.random.default_rng(seed)

    survived = eligible = positive = daily_breach = max_breach = best_day_block = 0
    rewards_all_paths = np.zeros(int(paths), dtype=float)
    positive_rewards: list[float] = []

    for path_i in range(int(paths)):
        idx = _moving_block_sample(len(r), days, int(block), rng)
        balance = 1.0
        highest_midnight_balance = 1.0
        closed_daily_profit: list[float] = []
        failed_reason = None

        for j in idx:
            midnight_balance = balance
            worst = midnight_balance * (1.0 + float(a[j]))
            daily_floor = (
                midnight_balance
                - program.funded.max_daily_loss_pct / 100.0
            )
            if worst <= daily_floor:
                failed_reason = "daily_loss"
                break

            if program.funded.trailing_max_loss:
                floor = (
                    max(1.0, highest_midnight_balance)
                    - program.funded.max_loss_pct / 100.0
                )
            else:
                floor = 1.0 - program.funded.max_loss_pct / 100.0
            if worst <= floor:
                failed_reason = "max_loss"
                break

            balance = midnight_balance * (1.0 + float(r[j]))
            closed_daily_profit.append(balance - midnight_balance)
            highest_midnight_balance = max(highest_midnight_balance, balance)

        if failed_reason is not None:
            daily_breach += int(failed_reason == "daily_loss")
            max_breach += int(failed_reason == "max_loss")
            continue

        survived += 1
        best_day_ok = _best_day_ok(
            closed_daily_profit, program.funded.best_day_rule_pct
        )
        if not best_day_ok:
            best_day_block += 1
            continue

        eligible += 1
        reward = max(balance - 1.0, 0.0) * program.reward_share * 100.0
        rewards_all_paths[path_i] = float(reward)
        if reward > 0.0:
            positive += 1
            positive_rewards.append(float(reward))

    return FundedSimulation(
        exposure_scale=scale,
        paths=int(paths),
        reward_window_days=days,
        survival_probability=survived / float(paths),
        reward_eligible_probability=eligible / float(paths),
        positive_reward_probability=positive / float(paths),
        expected_reward_pct=float(rewards_all_paths.mean()),
        median_positive_reward_pct=(
            None
            if not positive_rewards
            else float(np.median(np.asarray(positive_rewards)))
        ),
        daily_loss_breach_probability=daily_breach / float(paths),
        max_loss_breach_probability=max_breach / float(paths),
        best_day_ineligible_probability=best_day_block / float(paths),
    )


def _candidate_within_risk_tier(
    candidate: PropOptimizationCandidate,
    *,
    evaluation_daily_breach_cap: float,
    funded_daily_breach_cap: float,
    funded_survival_floor: float,
) -> bool:
    if candidate.challenge.daily_loss_breach_probability > evaluation_daily_breach_cap:
        return False
    if (
        candidate.verification is not None
        and candidate.verification.daily_loss_breach_probability
        > evaluation_daily_breach_cap
    ):
        return False
    if candidate.funded.daily_loss_breach_probability > funded_daily_breach_cap:
        return False
    if candidate.funded.survival_probability < funded_survival_floor:
        return False
    return True


def _candidate_views(
    candidates: Sequence[PropOptimizationCandidate],
) -> dict[str, PropOptimizationCandidate | None]:
    if not candidates:
        return {
            "max_payout_efficiency": None,
            "max_evaluation_pass": None,
            "safest_funded": None,
            "balanced": None,
            "conservative": None,
        }

    max_payout = max(
        candidates,
        key=lambda x: (
            x.payout_efficiency_score,
            x.combined_evaluation_pass_probability,
        ),
    )
    max_pass = max(
        candidates,
        key=lambda x: (
            x.combined_evaluation_pass_probability,
            x.funded.expected_reward_pct,
            x.funded.survival_probability,
        ),
    )
    safest = max(
        candidates,
        key=lambda x: (
            x.funded.survival_probability,
            -x.funded.daily_loss_breach_probability,
            x.funded.expected_reward_pct,
        ),
    )

    balanced_pool = [
        x for x in candidates
        if _candidate_within_risk_tier(
            x,
            evaluation_daily_breach_cap=0.15,
            funded_daily_breach_cap=0.10,
            funded_survival_floor=0.85,
        )
    ]
    conservative_pool = [
        x for x in candidates
        if _candidate_within_risk_tier(
            x,
            evaluation_daily_breach_cap=0.10,
            funded_daily_breach_cap=0.05,
            funded_survival_floor=0.90,
        )
    ]
    balanced = (
        None
        if not balanced_pool
        else max(
            balanced_pool,
            key=lambda x: (
                x.payout_efficiency_score,
                x.combined_evaluation_pass_probability,
            ),
        )
    )
    conservative = (
        None
        if not conservative_pool
        else max(
            conservative_pool,
            key=lambda x: (
                x.payout_efficiency_score,
                x.combined_evaluation_pass_probability,
            ),
        )
    )
    return {
        "max_payout_efficiency": max_payout,
        "max_evaluation_pass": max_pass,
        "safest_funded": safest,
        "balanced": balanced,
        "conservative": conservative,
    }


def optimize_prop_exposure(
    returns: Sequence[float],
    adverse_returns: Sequence[float],
    opened_trade_days: Sequence[bool],
    program: PropFirmProgram,
    *,
    exposure_scales: Sequence[float] = tuple(
        np.round(np.arange(0.05, 1.51, 0.05), 2)
    ),
    paths: int = 1500,
    block: int = 10,
    seed: int = 20260905,
    input_precision: str = "daily_ohlc_conservative_proxy",
    top_candidates: int = 100,
) -> PropOptimizationResult:
    """Optimize challenge, verification, and funded risk independently."""
    base_r = np.asarray(returns, dtype=float)
    base_a = np.asarray(adverse_returns, dtype=float)
    opened = np.asarray(opened_trade_days, dtype=bool)
    scales = [float(x) for x in exposure_scales]

    challenge_table = [
        simulate_stage(
            base_r, base_a, opened, program.challenge,
            exposure_scale=s,
            paths=paths,
            block=block,
            seed=seed + 1000 * i,
        )
        for i, s in enumerate(scales)
    ]

    verification_table = None
    if program.verification is not None:
        verification_table = [
            simulate_stage(
                base_r, base_a, opened, program.verification,
                exposure_scale=s,
                paths=paths,
                block=block,
                seed=seed + 100000 + 1000 * i,
            )
            for i, s in enumerate(scales)
        ]

    funded_table = [
        simulate_funded_reward(
            base_r, base_a, opened, program,
            exposure_scale=s,
            paths=paths,
            block=block,
            seed=seed + 200000 + 1000 * i,
        )
        for i, s in enumerate(scales)
    ]

    candidates: list[PropOptimizationCandidate] = []
    verification_choices = (
        [None] if verification_table is None else verification_table
    )
    for challenge, verification, funded in product(
        challenge_table, verification_choices, funded_table
    ):
        eval_pass = challenge.pass_probability
        eval_days = challenge.median_days_to_pass
        verification_scale = None
        if verification is not None:
            eval_pass *= verification.pass_probability
            verification_scale = verification.exposure_scale
            if (
                eval_days is not None
                and verification.median_days_to_pass is not None
            ):
                eval_days += verification.median_days_to_pass
            else:
                eval_days = None

        denominator = max(
            (
                eval_days
                if eval_days is not None
                else float(program.challenge.analysis_horizon_days)
            )
            + program.first_reward_eligible_days,
            1.0,
        )

        # funded.expected_reward_pct is already unconditional across funded
        # paths, so it already incorporates funded rule failures and Best Day
        # ineligibility. Do not multiply survival a second time.
        score = (
            float(eval_pass)
            * float(funded.expected_reward_pct)
            / float(denominator)
        )

        candidates.append(
            PropOptimizationCandidate(
                challenge_exposure_scale=challenge.exposure_scale,
                verification_exposure_scale=verification_scale,
                funded_exposure_scale=funded.exposure_scale,
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
            x.funded.survival_probability,
        ),
        reverse=True,
    )
    candidates = candidates[: max(int(top_candidates), 1)]

    views = _candidate_views(candidates)
    return PropOptimizationResult(
        program=program.to_dict(),
        selected=views["max_payout_efficiency"],
        candidates=candidates,
        challenge_scale_table=challenge_table,
        verification_scale_table=verification_table,
        funded_scale_table=funded_table,
        input_precision=input_precision,
        objective=(
            "independently optimize Challenge, Verification, and funded exposure; "
            "report payout, pass-probability, balanced, and conservative risk views"
        ),
        views=views,
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
    """FTMO trading-day proxy: at least one position is newly opened/increased."""
    w = execution_weights.fillna(0.0).astype(float)
    prev = w.shift(1).fillna(0.0)
    same_direction_increase = (
        (w * prev >= 0.0)
        & (w.abs() > prev.abs() + 1e-12)
    )
    direction_change = (
        (w.abs() > 1e-12)
        & (prev.abs() > 1e-12)
        & (np.sign(w) != np.sign(prev))
    )
    new_from_flat = (w.abs() > 1e-12) & (prev.abs() <= 1e-12)
    return (same_direction_increase | direction_change | new_from_flat).any(axis=1)
