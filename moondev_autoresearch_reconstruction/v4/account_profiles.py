"""Independent optimization profiles for private capital and prop firms."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PrivateAccountProfile:
    id: str = "private_growth"
    max_drawdown_pct: float = 32.0
    objective: str = "maximize_sustainable_cagr"
    cost_stress_multiplier: float = 3.0
    max_gross_exposure: float = 2.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PropStageRule:
    id: str
    profit_target_pct: float | None
    max_daily_loss_pct: float
    max_loss_pct: float
    min_trading_days: int = 0
    trailing_max_loss: bool = False
    best_day_rule_pct: float | None = None
    analysis_horizon_days: int = 252

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PropFirmProgram:
    id: str
    label: str
    challenge: PropStageRule
    verification: PropStageRule | None
    funded: PropStageRule
    reward_share: float
    first_reward_eligible_days: int
    rule_source: str
    instrument_policy: str = "venue_symbols_only"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "challenge": self.challenge.to_dict(),
            "verification": None if self.verification is None else self.verification.to_dict(),
            "funded": self.funded.to_dict(),
            "reward_share": self.reward_share,
            "first_reward_eligible_days": self.first_reward_eligible_days,
            "rule_source": self.rule_source,
            "instrument_policy": self.instrument_policy,
        }


FTMO_2STEP = PropFirmProgram(
    id="ftmo_2step_2026",
    label="FTMO 2-Step",
    challenge=PropStageRule(
        id="challenge",
        profit_target_pct=10.0,
        max_daily_loss_pct=5.0,
        max_loss_pct=10.0,
        min_trading_days=4,
        trailing_max_loss=False,
        analysis_horizon_days=252,
    ),
    verification=PropStageRule(
        id="verification",
        profit_target_pct=5.0,
        max_daily_loss_pct=5.0,
        max_loss_pct=10.0,
        min_trading_days=4,
        trailing_max_loss=False,
        analysis_horizon_days=252,
    ),
    funded=PropStageRule(
        id="funded",
        profit_target_pct=None,
        max_daily_loss_pct=5.0,
        max_loss_pct=10.0,
        min_trading_days=0,
        trailing_max_loss=False,
        analysis_horizon_days=252,
    ),
    reward_share=0.80,
    first_reward_eligible_days=14,
    rule_source="https://ftmo.com/en/trading-objectives/",
)


FTMO_1STEP = PropFirmProgram(
    id="ftmo_1step_2026",
    label="FTMO 1-Step",
    challenge=PropStageRule(
        id="challenge",
        profit_target_pct=10.0,
        max_daily_loss_pct=3.0,
        max_loss_pct=10.0,
        min_trading_days=0,
        trailing_max_loss=True,
        best_day_rule_pct=50.0,
        analysis_horizon_days=252,
    ),
    verification=None,
    funded=PropStageRule(
        id="funded",
        profit_target_pct=None,
        max_daily_loss_pct=3.0,
        max_loss_pct=10.0,
        min_trading_days=0,
        trailing_max_loss=True,
        best_day_rule_pct=50.0,
        analysis_horizon_days=252,
    ),
    reward_share=0.90,
    first_reward_eligible_days=14,
    rule_source="https://ftmo.com/en/trading-objectives/",
)


PROP_PROGRAMS = {
    FTMO_2STEP.id: FTMO_2STEP,
    FTMO_1STEP.id: FTMO_1STEP,
}
