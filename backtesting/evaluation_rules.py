"""
Prop-firm evaluation rules — 1-phase $5k challenge.

Enforced bar-by-bar during backtest:
- Profit target : +$450 (9% of $5k) — single phase, same target as funded
- Max daily loss: 3% of account ($150 on $5k)
- Max drawdown  : 3% static from initial balance (floor $4,850)
- Max leverage  : 5x notional vs equity
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EvaluationStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    PASSED_STEP1 = "PASSED_STEP1"
    PASSED = "PASSED"
    FAILED_DAILY_LOSS = "FAILED_DAILY_LOSS"
    FAILED_DRAWDOWN = "FAILED_DRAWDOWN"


@dataclass(frozen=True)
class PropFirmRules:
    """1-phase prop challenge: 9% profit target, 3% daily loss, 3% static max drawdown."""
    account_size: float = 5000.0
    step1_profit: float = 450.0   # 9% of $5k — phase 1 (and only phase) target
    step2_profit: float = 450.0   # same as step1: 1-phase challenge
    max_daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.03
    max_leverage: float = 5.0
    evaluation_fee: float = 49.99

    @property
    def step1_equity(self) -> float:
        return self.account_size + self.step1_profit

    @property
    def step2_equity(self) -> float:
        return self.account_size + self.step2_profit

    @property
    def max_daily_loss_amount(self) -> float:
        return self.account_size * self.max_daily_loss_pct

    @property
    def min_equity(self) -> float:
        return self.account_size * (1.0 - self.max_drawdown_pct)


class EvaluationTracker:
    """Tracks challenge state bar-by-bar (daily)."""

    def __init__(self, rules: PropFirmRules):
        self.rules = rules
        self.status = EvaluationStatus.IN_PROGRESS
        self.phase = 1
        self.day_start_equity: Optional[float] = None
        self.worst_daily_loss = 0.0
        self.worst_drawdown = 0.0
        self.peak_equity = rules.account_size
        self.days_traded = 0
        self.halt_trading_today = False

    def on_new_day(self, equity: float) -> None:
        self.day_start_equity = equity
        self.halt_trading_today = False
        self.days_traded += 1

    def update_intraday(self, equity: float) -> EvaluationStatus:
        if self.status not in (
            EvaluationStatus.IN_PROGRESS,
            EvaluationStatus.PASSED_STEP1,
        ):
            return self.status

        # Peak-to-trough drawdown (for reporting)
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity > 0:
            dd = (equity - self.peak_equity) / self.peak_equity
            if dd < self.worst_drawdown:
                self.worst_drawdown = dd

        # Static drawdown floor from initial balance (hard fail)
        if equity <= self.rules.min_equity:
            self.status = EvaluationStatus.FAILED_DRAWDOWN
            return self.status

        if self.day_start_equity is not None:
            daily_pnl = equity - self.day_start_equity
            daily_loss = min(daily_pnl, 0.0)
            if daily_loss < self.worst_daily_loss:
                self.worst_daily_loss = daily_loss
            if daily_pnl <= -self.rules.max_daily_loss_amount:
                self.status = EvaluationStatus.FAILED_DAILY_LOSS
                self.halt_trading_today = True
                return self.status

        # Phase progression (profit measured from initial account)
        profit = equity - self.rules.account_size
        if self.phase == 1 and profit >= self.rules.step1_profit:
            self.phase = 2
            self.status = EvaluationStatus.PASSED_STEP1
        if profit >= self.rules.step2_profit:
            self.phase = 2
            self.status = EvaluationStatus.PASSED

        return self.status

    def can_trade(self) -> bool:
        if self.status in (
            EvaluationStatus.FAILED_DAILY_LOSS,
            EvaluationStatus.FAILED_DRAWDOWN,
            EvaluationStatus.PASSED,
        ):
            return False
        return not self.halt_trading_today

    def check_leverage(self, equity: float, notional: float) -> bool:
        if equity <= 0:
            return False
        return (notional / equity) <= self.rules.max_leverage

    def summary(self) -> dict:
        return {
            "status": self.status.value,
            "phase": self.phase,
            "account_size": self.rules.account_size,
            "step1_target_equity": self.rules.step1_equity,
            "step2_target_equity": self.rules.step2_equity,
            "min_equity_floor": self.rules.min_equity,
            "max_daily_loss_limit": self.rules.max_daily_loss_amount,
            "max_leverage": self.rules.max_leverage,
            "evaluation_fee": self.rules.evaluation_fee,
            "days_traded": self.days_traded,
            "worst_daily_loss": self.worst_daily_loss,
            "worst_drawdown_pct": self.worst_drawdown * 100,
        }
