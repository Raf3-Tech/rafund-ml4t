"""Transaction cost modelling for Raf3nd ML4T backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CostSummary:
    total_commission: float
    total_slippage: float
    total_cost: float
    avg_cost_per_trade_pct: float
    cost_drag_on_returns_pct: float


class TransactionCostModel:
    def __init__(
        self,
        commission_pct: float = 0.001,
        slippage_model: str = "fixed",
        fixed_slippage_pct: float = 0.0005,
        volume_impact_factor: float = 0.1,
        initial_capital: float = 5000.0,
    ) -> None:
        self.commission_pct = commission_pct
        self.slippage_model = slippage_model
        self.fixed_slippage_pct = fixed_slippage_pct
        self.volume_impact_factor = volume_impact_factor
        self.initial_capital = initial_capital
        self.total_commission = 0.0
        self.total_slippage = 0.0
        self.total_trades = 0

    def apply(
        self,
        price: float,
        size: float,
        side: str,
        bar_volume: Optional[float] = None,
    ) -> dict[str, float]:
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")

        notional = abs(price * size)
        commission = notional * self.commission_pct

        if self.slippage_model == "fixed":
            slippage_pct = self.fixed_slippage_pct
        elif self.slippage_model == "volume_proportional":
            if bar_volume is None or bar_volume <= 0:
                raise ValueError("bar_volume is required for volume_proportional slippage")
            slippage_pct = self.volume_impact_factor * (abs(size) / bar_volume)
            slippage_pct = min(slippage_pct, 0.01)
        else:
            raise ValueError(f"Unsupported slippage_model: {self.slippage_model}")

        executed_price = price * (1.0 + slippage_pct if side == "buy" else 1.0 - slippage_pct)
        slippage_cost = abs((executed_price - price) * size)
        total_cost = commission + slippage_cost

        self.total_commission += commission
        self.total_slippage += slippage_cost
        self.total_trades += 1

        return {
            "executed_price": executed_price,
            "commission": commission,
            "total_cost": total_cost,
            "slippage_pct": slippage_pct,
        }

    def summary(self) -> CostSummary:
        avg_cost_pct = (self.total_commission + self.total_slippage) / self.total_trades / self.initial_capital * 100 if self.total_trades else 0.0
        total_cost = self.total_commission + self.total_slippage
        return CostSummary(
            total_commission=self.total_commission,
            total_slippage=self.total_slippage,
            total_cost=total_cost,
            avg_cost_per_trade_pct=avg_cost_pct,
            cost_drag_on_returns_pct=total_cost / self.initial_capital * 100,
        )
