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
    repeat_reward_cycles: int = 12
    repeat_expected_reward_pct: float = 0.0
    repeat_payout_efficiency_score: float = 0.0

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
            "repeat_reward_cycles": self.repeat_reward_cycles,
            "repeat_expected_reward_pct": self.repeat_expected_reward_pct,
            "repeat_payout_efficiency_score": self.repeat_payout_efficiency_score,
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


def _moving_block_sample_matrix(
    n: int,
    length: int,
    block: int,
    paths: int,
    rng: np.random.Generator,
) -> np.ndarray:
    if n < 1 or length < 1 or paths < 1:
        raise ValueError("positive n, length, and paths required")
    block = max(1, min(int(block), n))
    n_blocks = int(np.ceil(length / block))
    starts = rng.integers(
        0,
        max(n - block + 1, 1),
        size=(int(paths), n_blocks),
    )
    offsets = np.arange(block, dtype=int)
    idx = (starts[..., None] + offsets).reshape(int(paths), -1)
    return idx[:, : int(length)]


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
    r = np.asarray(returns, dtype=float)
    a = np.asarray(adverse_returns, dtype=float)
    opened = np.asarray(opened_trade_days, dtype=bool)
    good = np.isfinite(r) & np.isfinite(a)
    r, a, opened = r[good], a[good], opened[good]
    if len(r) < 50:
        raise ValueError("prop simulation requires >=50 aligned daily observations")

    horizon = int(rule.analysis_horizon_days)
    rng = np.random.default_rng(seed)
    idx = _moving_block_sample_matrix(
        len(r), horizon, int(block), int(paths), rng
    )
    rr = r[idx] * scale
    aa = a[idx] * scale
    oo = opened[idx]

    n = int(paths)
    balance = np.ones(n, dtype=float)
    highest_midnight = np.ones(n, dtype=float)
    trade_days = np.zeros(n, dtype=int)
    positive_sum = np.zeros(n, dtype=float)
    best_positive_day = np.zeros(n, dtype=float)

    # status: 0 active, 1 pass, 2 daily fail, 3 max-loss fail, 4 timeout
    status = np.zeros(n, dtype=np.int8)
    event_day = np.full(n, horizon, dtype=int)

    target = (
        None
        if rule.profit_target_pct is None
        else 1.0 + rule.profit_target_pct / 100.0
    )

    for day in range(horizon):
        active = status == 0
        if not np.any(active):
            break
        trade_days[active] += oo[active, day].astype(int)

        start = balance.copy()
        worst = start * (1.0 + aa[:, day])
        daily_floor = start - rule.max_daily_loss_pct / 100.0
        daily_fail = active & (worst <= daily_floor)
        status[daily_fail] = 2
        event_day[daily_fail] = day + 1

        active = status == 0
        if rule.trailing_max_loss:
            overall_floor = (
                np.maximum(1.0, highest_midnight)
                - rule.max_loss_pct / 100.0
            )
        else:
            overall_floor = np.full(
                n, 1.0 - rule.max_loss_pct / 100.0, dtype=float
            )
        max_fail = active & (worst <= overall_floor)
        status[max_fail] = 3
        event_day[max_fail] = day + 1

        active = status == 0
        if not np.any(active):
            continue
        new_balance = start * (1.0 + rr[:, day])
        profit = new_balance - start
        pos = np.maximum(profit, 0.0)
        positive_sum[active] += pos[active]
        best_positive_day[active] = np.maximum(
            best_positive_day[active], pos[active]
        )
        balance[active] = new_balance[active]
        highest_midnight[active] = np.maximum(
            highest_midnight[active], balance[active]
        )

        if target is not None:
            best_day_ok = np.ones(n, dtype=bool)
            if rule.best_day_rule_pct is not None:
                allowed = (
                    positive_sum * rule.best_day_rule_pct / 100.0
                )
                best_day_ok = (
                    (positive_sum > 0.0)
                    & (best_positive_day <= allowed + 1e-12)
                )
            passed = (
                (status == 0)
                & (balance >= target)
                & (trade_days >= rule.min_trading_days)
                & best_day_ok
            )
            status[passed] = 1
            event_day[passed] = day + 1

    status[status == 0] = 4

    pass_mask = status == 1
    daily_mask = status == 2
    max_mask = status == 3
    timeout_mask = status == 4
    pass_days = event_day[pass_mask]

    denom = float(n)
    return StageSimulation(
        stage_id=rule.id,
        exposure_scale=scale,
        paths=n,
        analysis_horizon_days=horizon,
        pass_probability=float(pass_mask.sum() / denom),
        fail_probability=float((daily_mask | max_mask).sum() / denom),
        timeout_probability=float(timeout_mask.sum() / denom),
        daily_loss_breach_probability=float(daily_mask.sum() / denom),
        max_loss_breach_probability=float(max_mask.sum() / denom),
        median_days_to_pass=(
            None if pass_days.size == 0 else float(np.median(pass_days))
        ),
        p75_days_to_pass=(
            None
            if pass_days.size == 0
            else float(np.quantile(pass_days, 0.75))
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
    r = np.asarray(returns, dtype=float)
    a = np.asarray(adverse_returns, dtype=float)
    opened = np.asarray(opened_trade_days, dtype=bool)
    good = np.isfinite(r) & np.isfinite(a)
    r, a, opened = r[good], a[good], opened[good]
    if len(r) < 50:
        raise ValueError("prop simulation requires >=50 aligned daily observations")

    days = int(program.first_reward_eligible_days)
    n = int(paths)
    rng = np.random.default_rng(seed)
    idx = _moving_block_sample_matrix(len(r), days, int(block), n, rng)
    rr = r[idx] * scale
    aa = a[idx] * scale

    balance = np.ones(n, dtype=float)
    highest_midnight = np.ones(n, dtype=float)
    positive_sum = np.zeros(n, dtype=float)
    best_positive_day = np.zeros(n, dtype=float)
    status = np.zeros(n, dtype=np.int8)  # 0 alive, 2 daily fail, 3 max fail

    for day in range(days):
        active = status == 0
        if not np.any(active):
            break
        start = balance.copy()
        worst = start * (1.0 + aa[:, day])
        daily_floor = (
            start - program.funded.max_daily_loss_pct / 100.0
        )
        daily_fail = active & (worst <= daily_floor)
        status[daily_fail] = 2

        active = status == 0
        if program.funded.trailing_max_loss:
            overall_floor = (
                np.maximum(1.0, highest_midnight)
                - program.funded.max_loss_pct / 100.0
            )
        else:
            overall_floor = np.full(
                n,
                1.0 - program.funded.max_loss_pct / 100.0,
                dtype=float,
            )
        max_fail = active & (worst <= overall_floor)
        status[max_fail] = 3

        active = status == 0
        if not np.any(active):
            continue
        new_balance = start * (1.0 + rr[:, day])
        profit = new_balance - start
        pos = np.maximum(profit, 0.0)
        positive_sum[active] += pos[active]
        best_positive_day[active] = np.maximum(
            best_positive_day[active], pos[active]
        )
        balance[active] = new_balance[active]
        highest_midnight[active] = np.maximum(
            highest_midnight[active], balance[active]
        )

    survived = status == 0
    best_day_ok = np.ones(n, dtype=bool)
    if program.funded.best_day_rule_pct is not None:
        allowed = (
            positive_sum * program.funded.best_day_rule_pct / 100.0
        )
        best_day_ok = (
            (positive_sum > 0.0)
            & (best_positive_day <= allowed + 1e-12)
        )
    eligible = survived & best_day_ok
    reward = np.zeros(n, dtype=float)
    reward[eligible] = (
        np.maximum(balance[eligible] - 1.0, 0.0)
        * program.reward_share
        * 100.0
    )
    positive = reward > 0.0

    denom = float(n)
    return FundedSimulation(
        exposure_scale=scale,
        paths=n,
        reward_window_days=days,
        survival_probability=float(survived.sum() / denom),
        reward_eligible_probability=float(eligible.sum() / denom),
        positive_reward_probability=float(positive.sum() / denom),
        expected_reward_pct=float(reward.mean()),
        median_positive_reward_pct=(
            None
            if not np.any(positive)
            else float(np.median(reward[positive]))
        ),
        daily_loss_breach_probability=float((status == 2).sum() / denom),
        max_loss_breach_probability=float((status == 3).sum() / denom),
        best_day_ineligible_probability=float(
            (survived & ~best_day_ok).sum() / denom
        ),
    )

def _candidate_within_risk_tier(
    candidate: PropOptimizationCandidate,
    *,
    evaluation_daily_breach_cap: float,
    evaluation_max_loss_breach_cap: float,
    funded_daily_breach_cap: float,
    funded_max_loss_breach_cap: float,
    funded_survival_floor: float,
) -> bool:
    """Require every modeled hard-loss path to fit the named risk tier."""
    evaluation_stages = [candidate.challenge]
    if candidate.verification is not None:
        evaluation_stages.append(candidate.verification)
    for stage in evaluation_stages:
        if (
            stage.daily_loss_breach_probability
            > evaluation_daily_breach_cap
        ):
            return False
        if (
            stage.max_loss_breach_probability
            > evaluation_max_loss_breach_cap
        ):
            return False
    if (
        candidate.funded.daily_loss_breach_probability
        > funded_daily_breach_cap
    ):
        return False
    if (
        candidate.funded.max_loss_breach_probability
        > funded_max_loss_breach_cap
    ):
        return False
    if candidate.funded.survival_probability < funded_survival_floor:
        return False
    return True

def repeat_payout_projection(
    *,
    expected_reward_pct: float,
    survival_probability: float,
    evaluation_pass_probability: float,
    evaluation_days: float,
    reward_cycle_days: int,
    cycles: int = 12,
) -> tuple[float, float]:
    """Finite repeated-payout proxy from existing funded-window evidence.

    Assumptions are deliberately conservative: each cycle has the same length
    as the first eligible reward window, the funded account must survive a
    cycle to reach the next one, profits are not compounded or rolled over,
    and challenge retries, fees, scaling upgrades, and rollover are excluded.
    """
    n = max(int(cycles), 1)
    s = min(max(float(survival_probability), 0.0), 1.0)
    if abs(1.0 - s) <= 1e-12:
        continuation = float(n)
    else:
        continuation = float((1.0 - s ** n) / (1.0 - s))
    expected = max(float(expected_reward_pct), 0.0) * continuation
    elapsed = max(
        float(evaluation_days) + float(n * max(int(reward_cycle_days), 1)),
        1.0,
    )
    score = (
        max(float(evaluation_pass_probability), 0.0)
        * expected
        / elapsed
    )
    return float(expected), float(score)

def _candidate_views(
    candidates: Sequence[PropOptimizationCandidate],
) -> dict[str, PropOptimizationCandidate | None]:
    if not candidates:
        return {
            "max_payout_efficiency": None,
            "max_repeat_payout_efficiency": None,
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
    max_repeat = max(
        candidates,
        key=lambda x: (
            x.repeat_payout_efficiency_score,
            x.funded.survival_probability,
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
            -x.funded.max_loss_breach_probability,
            x.funded.expected_reward_pct,
        ),
    )

    balanced_pool = [
        x for x in candidates
        if _candidate_within_risk_tier(
            x,
            evaluation_daily_breach_cap=0.15,
            evaluation_max_loss_breach_cap=0.15,
            funded_daily_breach_cap=0.10,
            funded_max_loss_breach_cap=0.05,
            funded_survival_floor=0.85,
        )
    ]
    conservative_pool = [
        x for x in candidates
        if _candidate_within_risk_tier(
            x,
            evaluation_daily_breach_cap=0.10,
            evaluation_max_loss_breach_cap=0.10,
            funded_daily_breach_cap=0.05,
            funded_max_loss_breach_cap=0.025,
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
        "max_repeat_payout_efficiency": max_repeat,
        "max_evaluation_pass": max_pass,
        "safest_funded": safest,
        "balanced": balanced,
        "conservative": conservative,
    }


def _simulate_stage_scale_table(
    returns: Sequence[float],
    adverse_returns: Sequence[float],
    opened_trade_days: Sequence[bool],
    rule: PropStageRule,
    *,
    exposure_scales: Sequence[float],
    paths: int,
    block: int,
    seed: int,
    prescaled_returns: np.ndarray | None = None,
    prescaled_adverse: np.ndarray | None = None,
) -> list[StageSimulation]:
    """Evaluate all exposure scales on identical bootstrap paths.

    Common random numbers remove scale-ranking noise and the batched state
    update avoids repeating the day loop once per scale.
    """
    scales = np.asarray([float(x) for x in exposure_scales], dtype=float)
    if scales.size < 1:
        raise ValueError("at least one exposure scale required")

    opened = np.asarray(opened_trade_days, dtype=bool)
    use_prescaled = prescaled_returns is not None or prescaled_adverse is not None
    if use_prescaled:
        if prescaled_returns is None or prescaled_adverse is None:
            raise ValueError("both prescaled return and adverse matrices are required")
        scaled_r = np.asarray(prescaled_returns, dtype=float)
        scaled_a = np.asarray(prescaled_adverse, dtype=float)
        if scaled_r.shape != scaled_a.shape:
            raise ValueError("prescaled return/adverse shape mismatch")
        if scaled_r.ndim != 2 or scaled_r.shape[0] != len(scales):
            raise ValueError("prescaled matrices must be scale x day")
        if scaled_r.shape[1] != len(opened):
            raise ValueError("prescaled day count must match opened_trade_days")
        good = np.all(np.isfinite(scaled_r), axis=0) & np.all(
            np.isfinite(scaled_a), axis=0
        )
        scaled_r = scaled_r[:, good]
        scaled_a = scaled_a[:, good]
        opened = opened[good]
        data_len = scaled_r.shape[1]
        r = a = None
    else:
        r = np.asarray(returns, dtype=float)
        a = np.asarray(adverse_returns, dtype=float)
        good = np.isfinite(r) & np.isfinite(a)
        r, a, opened = r[good], a[good], opened[good]
        data_len = len(r)

    if data_len < 50:
        raise ValueError("prop simulation requires >=50 aligned daily observations")

    horizon = int(rule.analysis_horizon_days)
    n = int(paths)
    rng = np.random.default_rng(seed)
    idx = _moving_block_sample_matrix(data_len, horizon, int(block), n, rng)
    sampled_opened = opened[idx]
    if not use_prescaled:
        sampled_r = r[idx]
        sampled_a = a[idx]
    scale_col = scales[:, None]
    m = int(scales.size)

    balance = np.ones((m, n), dtype=float)
    highest_midnight = np.ones((m, n), dtype=float)
    trade_days = np.zeros((m, n), dtype=int)
    positive_sum = np.zeros((m, n), dtype=float)
    best_positive_day = np.zeros((m, n), dtype=float)
    status = np.zeros((m, n), dtype=np.int8)
    event_day = np.full((m, n), horizon, dtype=int)

    target = (
        None
        if rule.profit_target_pct is None
        else 1.0 + rule.profit_target_pct / 100.0
    )

    for day in range(horizon):
        active = status == 0
        if not np.any(active):
            break
        trade_days += active * sampled_opened[:, day][None, :]

        start = balance.copy()
        adverse = (
            scaled_a[:, idx[:, day]]
            if use_prescaled
            else sampled_a[:, day][None, :] * scale_col
        )
        worst = start * (1.0 + adverse)
        daily_floor = start - rule.max_daily_loss_pct / 100.0
        daily_fail = active & (worst <= daily_floor)
        status[daily_fail] = 2
        event_day[daily_fail] = day + 1

        active = status == 0
        if rule.trailing_max_loss:
            overall_floor = (
                np.maximum(1.0, highest_midnight)
                - rule.max_loss_pct / 100.0
            )
        else:
            overall_floor = np.full(
                (m, n), 1.0 - rule.max_loss_pct / 100.0, dtype=float
            )
        max_fail = active & (worst <= overall_floor)
        status[max_fail] = 3
        event_day[max_fail] = day + 1

        active = status == 0
        if not np.any(active):
            continue
        ret = (
            scaled_r[:, idx[:, day]]
            if use_prescaled
            else sampled_r[:, day][None, :] * scale_col
        )
        new_balance = start * (1.0 + ret)
        profit = new_balance - start
        pos = np.maximum(profit, 0.0)
        positive_sum += np.where(active, pos, 0.0)
        best_positive_day = np.where(
            active, np.maximum(best_positive_day, pos), best_positive_day
        )
        balance = np.where(active, new_balance, balance)
        highest_midnight = np.where(
            active, np.maximum(highest_midnight, balance), highest_midnight
        )

        if target is not None:
            best_day_ok = np.ones((m, n), dtype=bool)
            if rule.best_day_rule_pct is not None:
                allowed = positive_sum * rule.best_day_rule_pct / 100.0
                best_day_ok = (
                    (positive_sum > 0.0)
                    & (best_positive_day <= allowed + 1e-12)
                )
            passed = (
                (status == 0)
                & (balance >= target)
                & (trade_days >= rule.min_trading_days)
                & best_day_ok
            )
            status[passed] = 1
            event_day[passed] = day + 1

    status[status == 0] = 4
    out: list[StageSimulation] = []
    for i, scale in enumerate(scales):
        row_status = status[i]
        pass_mask = row_status == 1
        daily_mask = row_status == 2
        max_mask = row_status == 3
        timeout_mask = row_status == 4
        pass_days = event_day[i][pass_mask]
        denom = float(n)
        out.append(StageSimulation(
            stage_id=rule.id,
            exposure_scale=float(scale),
            paths=n,
            analysis_horizon_days=horizon,
            pass_probability=float(pass_mask.sum() / denom),
            fail_probability=float((daily_mask | max_mask).sum() / denom),
            timeout_probability=float(timeout_mask.sum() / denom),
            daily_loss_breach_probability=float(daily_mask.sum() / denom),
            max_loss_breach_probability=float(max_mask.sum() / denom),
            median_days_to_pass=(
                None if pass_days.size == 0 else float(np.median(pass_days))
            ),
            p75_days_to_pass=(
                None if pass_days.size == 0
                else float(np.quantile(pass_days, 0.75))
            ),
        ))
    return out


def _simulate_funded_scale_table(
    returns: Sequence[float],
    adverse_returns: Sequence[float],
    opened_trade_days: Sequence[bool],
    program: PropFirmProgram,
    *,
    exposure_scales: Sequence[float],
    paths: int,
    block: int,
    seed: int,
    prescaled_returns: np.ndarray | None = None,
    prescaled_adverse: np.ndarray | None = None,
) -> list[FundedSimulation]:
    scales = np.asarray([float(x) for x in exposure_scales], dtype=float)
    if scales.size < 1:
        raise ValueError("at least one exposure scale required")

    opened = np.asarray(opened_trade_days, dtype=bool)
    use_prescaled = prescaled_returns is not None or prescaled_adverse is not None
    if use_prescaled:
        if prescaled_returns is None or prescaled_adverse is None:
            raise ValueError("both prescaled return and adverse matrices are required")
        scaled_r = np.asarray(prescaled_returns, dtype=float)
        scaled_a = np.asarray(prescaled_adverse, dtype=float)
        if scaled_r.shape != scaled_a.shape:
            raise ValueError("prescaled return/adverse shape mismatch")
        if scaled_r.ndim != 2 or scaled_r.shape[0] != len(scales):
            raise ValueError("prescaled matrices must be scale x day")
        if scaled_r.shape[1] != len(opened):
            raise ValueError("prescaled day count must match opened_trade_days")
        good = np.all(np.isfinite(scaled_r), axis=0) & np.all(
            np.isfinite(scaled_a), axis=0
        )
        scaled_r = scaled_r[:, good]
        scaled_a = scaled_a[:, good]
        opened = opened[good]
        data_len = scaled_r.shape[1]
        r = a = None
    else:
        r = np.asarray(returns, dtype=float)
        a = np.asarray(adverse_returns, dtype=float)
        good = np.isfinite(r) & np.isfinite(a)
        r, a, opened = r[good], a[good], opened[good]
        data_len = len(r)

    if data_len < 50:
        raise ValueError("prop simulation requires >=50 aligned daily observations")

    days = int(program.first_reward_eligible_days)
    n = int(paths)
    rng = np.random.default_rng(seed)
    idx = _moving_block_sample_matrix(data_len, days, int(block), n, rng)
    if not use_prescaled:
        sampled_r = r[idx]
        sampled_a = a[idx]
    scale_col = scales[:, None]
    m = int(scales.size)

    balance = np.ones((m, n), dtype=float)
    highest_midnight = np.ones((m, n), dtype=float)
    positive_sum = np.zeros((m, n), dtype=float)
    best_positive_day = np.zeros((m, n), dtype=float)
    status = np.zeros((m, n), dtype=np.int8)

    for day in range(days):
        active = status == 0
        if not np.any(active):
            break
        start = balance.copy()
        adverse = (
            scaled_a[:, idx[:, day]]
            if use_prescaled
            else sampled_a[:, day][None, :] * scale_col
        )
        worst = start * (1.0 + adverse)
        daily_floor = start - program.funded.max_daily_loss_pct / 100.0
        daily_fail = active & (worst <= daily_floor)
        status[daily_fail] = 2

        active = status == 0
        if program.funded.trailing_max_loss:
            overall_floor = (
                np.maximum(1.0, highest_midnight)
                - program.funded.max_loss_pct / 100.0
            )
        else:
            overall_floor = np.full(
                (m, n),
                1.0 - program.funded.max_loss_pct / 100.0,
                dtype=float,
            )
        max_fail = active & (worst <= overall_floor)
        status[max_fail] = 3

        active = status == 0
        if not np.any(active):
            continue
        ret = (
            scaled_r[:, idx[:, day]]
            if use_prescaled
            else sampled_r[:, day][None, :] * scale_col
        )
        new_balance = start * (1.0 + ret)
        profit = new_balance - start
        pos = np.maximum(profit, 0.0)
        positive_sum += np.where(active, pos, 0.0)
        best_positive_day = np.where(
            active, np.maximum(best_positive_day, pos), best_positive_day
        )
        balance = np.where(active, new_balance, balance)
        highest_midnight = np.where(
            active, np.maximum(highest_midnight, balance), highest_midnight
        )

    survived = status == 0
    best_day_ok = np.ones((m, n), dtype=bool)
    if program.funded.best_day_rule_pct is not None:
        allowed = positive_sum * program.funded.best_day_rule_pct / 100.0
        best_day_ok = (
            (positive_sum > 0.0)
            & (best_positive_day <= allowed + 1e-12)
        )
    eligible = survived & best_day_ok
    reward = np.where(
        eligible,
        np.maximum(balance - 1.0, 0.0) * program.reward_share * 100.0,
        0.0,
    )

    out: list[FundedSimulation] = []
    for i, scale in enumerate(scales):
        positive = reward[i] > 0.0
        denom = float(n)
        out.append(FundedSimulation(
            exposure_scale=float(scale),
            paths=n,
            reward_window_days=days,
            survival_probability=float(survived[i].sum() / denom),
            reward_eligible_probability=float(eligible[i].sum() / denom),
            positive_reward_probability=float(positive.sum() / denom),
            expected_reward_pct=float(reward[i].mean()),
            median_positive_reward_pct=(
                None if not np.any(positive)
                else float(np.median(reward[i][positive]))
            ),
            daily_loss_breach_probability=float((status[i] == 2).sum() / denom),
            max_loss_breach_probability=float((status[i] == 3).sum() / denom),
            best_day_ineligible_probability=float(
                (survived[i] & ~best_day_ok[i]).sum() / denom
            ),
        ))
    return out


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
    prescaled_returns_by_scale: Mapping[float, Sequence[float]] | None = None,
    prescaled_adverse_by_scale: Mapping[float, Sequence[float]] | None = None,
) -> PropOptimizationResult:
    """Optimize Challenge, Verification, and funded risk independently."""
    base_r = np.asarray(returns, dtype=float)
    base_a = np.asarray(adverse_returns, dtype=float)
    opened = np.asarray(opened_trade_days, dtype=bool)
    scales = [float(x) for x in exposure_scales]

    prescaled_r = prescaled_a = None
    if (
        prescaled_returns_by_scale is not None
        or prescaled_adverse_by_scale is not None
    ):
        if (
            prescaled_returns_by_scale is None
            or prescaled_adverse_by_scale is None
        ):
            raise ValueError(
                "both prescaled_returns_by_scale and "
                "prescaled_adverse_by_scale are required"
            )

        def scale_row(mapping, scale):
            if scale in mapping:
                return np.asarray(mapping[scale], dtype=float)
            for key, values in mapping.items():
                if abs(float(key) - float(scale)) <= 1e-12:
                    return np.asarray(values, dtype=float)
            raise KeyError(f"missing exact daily path for exposure scale {scale}")

        prescaled_r = np.vstack([
            scale_row(prescaled_returns_by_scale, scale)
            for scale in scales
        ])
        prescaled_a = np.vstack([
            scale_row(prescaled_adverse_by_scale, scale)
            for scale in scales
        ])

    challenge_table = _simulate_stage_scale_table(
        base_r,
        base_a,
        opened,
        program.challenge,
        exposure_scales=scales,
        paths=paths,
        block=block,
        seed=seed,
        prescaled_returns=prescaled_r,
        prescaled_adverse=prescaled_a,
    )

    verification_table = None
    if program.verification is not None:
        verification_table = _simulate_stage_scale_table(
            base_r,
            base_a,
            opened,
            program.verification,
            exposure_scales=scales,
            paths=paths,
            block=block,
            seed=seed + 100000,
            prescaled_returns=prescaled_r,
            prescaled_adverse=prescaled_a,
        )

    funded_table = _simulate_funded_scale_table(
        base_r,
        base_a,
        opened,
        program,
        exposure_scales=scales,
        paths=paths,
        block=block,
        seed=seed + 200000,
        prescaled_returns=prescaled_r,
        prescaled_adverse=prescaled_a,
    )

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

        score = (
            float(eval_pass)
            * float(funded.expected_reward_pct)
            / float(denominator)
        )
        repeat_expected_reward_pct, repeat_score = repeat_payout_projection(
            expected_reward_pct=funded.expected_reward_pct,
            survival_probability=funded.survival_probability,
            evaluation_pass_probability=eval_pass,
            evaluation_days=(
                eval_days
                if eval_days is not None
                else float(program.challenge.analysis_horizon_days)
            ),
            reward_cycle_days=program.first_reward_eligible_days,
            cycles=12,
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
                repeat_reward_cycles=12,
                repeat_expected_reward_pct=float(repeat_expected_reward_pct),
                repeat_payout_efficiency_score=float(repeat_score),
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

    # Risk-frontier views must see the complete Cartesian product. Truncating
    # to the top payout candidates first can silently discard the safest,
    # balanced, or conservative solution.
    views = _candidate_views(candidates)
    kept_candidates = candidates[: max(int(top_candidates), 1)]
    return PropOptimizationResult(
        program=program.to_dict(),
        selected=views["max_payout_efficiency"],
        candidates=kept_candidates,
        challenge_scale_table=challenge_table,
        verification_scale_table=verification_table,
        funded_scale_table=funded_table,
        input_precision=input_precision,
        objective=(
            "independently optimize Challenge, Verification, and funded exposure; "
            "use common bootstrap paths across scales; report first-reward payout, "
            "12-cycle survival-discounted repeat-payout, pass-probability, "
            "safest-funded, balanced, and conservative risk views"
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
