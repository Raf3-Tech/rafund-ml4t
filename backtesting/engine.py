"""Generic backtest engine for RAFund ML4T.

This engine enforces prop-firm rules in real time during the trading loop.
It supports single-asset signals and paired-leg positions when appropriate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import structlog

from backtesting.costs import TransactionCostModel

logger = structlog.get_logger(__name__)


@dataclass
class PropFirmState:
    daily_pnl: float = 0.0
    daily_halt: bool = False
    peak_equity: float = 0.0
    current_drawdown_pct: float = 0.0
    account_failed: bool = False
    step1_unlocked: bool = False
    step2_unlocked: bool = False
    halt_events: List[Dict[str, object]] = field(default_factory=list)


@dataclass
class TradeEvent:
    timestamp: pd.Timestamp
    event: str
    side: str
    qty_a: float
    qty_b: float
    price_a: Optional[float]
    price_b: Optional[float]
    pnl: Optional[float]
    commission: float
    slippage: float
    note: str


class BacktestEngine:
    def __init__(
        self,
        initial_capital: float = 5000.0,
        commission_pct: float = 0.001,
        slippage_model: str = "fixed",
        fixed_slippage_pct: float = 0.0005,
        volume_impact_factor: float = 0.1,
        leverage_limit: float = 5.0,
        daily_loss_limit_pct: float = 0.04,
        drawdown_limit_pct: float = 0.06,
        step1_profit_pct: float = 0.05,
        step2_profit_pct: float = 0.10,
        leg_allocation_pct: float = 0.18,
    ) -> None:
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_model = slippage_model
        self.fixed_slippage_pct = fixed_slippage_pct
        self.volume_impact_factor = volume_impact_factor
        self.leverage_limit = leverage_limit
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.drawdown_limit_pct = drawdown_limit_pct
        self.step1_profit_pct = step1_profit_pct
        self.step2_profit_pct = step2_profit_pct
        self.leg_allocation_pct = leg_allocation_pct

        self.reset()

    def reset(self) -> None:
        self.cash = self.initial_capital
        self.prop_firm_state = PropFirmState(
            peak_equity=self.initial_capital,
            current_drawdown_pct=0.0,
        )
        self.daily_start_equity: Optional[float] = None
        self.current_date: Optional[pd.Timestamp] = None
        self.position_a: float = 0.0
        self.position_b: float = 0.0
        self.entry_price_a: Optional[float] = None
        self.entry_price_b: Optional[float] = None
        self.entry_side: Optional[str] = None
        self.entry_timestamp: Optional[pd.Timestamp] = None
        self.trades: List[Dict[str, object]] = []
        self.equity_curve: List[Dict[str, object]] = []
        self.cost_model = TransactionCostModel(
            commission_pct=self.commission_pct,
            slippage_model=self.slippage_model,
            fixed_slippage_pct=self.fixed_slippage_pct,
            volume_impact_factor=self.volume_impact_factor,
            initial_capital=self.initial_capital,
        )

    def _compute_equity(self, price_a: Optional[float], price_b: Optional[float], price: Optional[float]) -> float:
        equity = self.cash
        if self.position_a and price_a is not None:
            equity += self.position_a * price_a
        if self.position_b and price_b is not None:
            equity += self.position_b * price_b
        if not self.position_a and not self.position_b and price is not None and hasattr(self, 'position'):
            # legacy single-asset support if necessary
            pass
        return equity

    def _current_notional(self, price_a: Optional[float], price_b: Optional[float]) -> float:
        notional = 0.0
        if self.position_a and price_a is not None:
            notional += abs(self.position_a * price_a)
        if self.position_b and price_b is not None:
            notional += abs(self.position_b * price_b)
        return notional

    def _record_halt(self, date: pd.Timestamp, reason: str, equity: float) -> None:
        self.prop_firm_state.halt_events.append(
            {"date": date.date().isoformat(), "reason": reason, "equity": equity}
        )

    def _update_propfirm(self, equity: float) -> None:
        if equity > self.prop_firm_state.peak_equity:
            self.prop_firm_state.peak_equity = equity

        if self.prop_firm_state.peak_equity > 0:
            self.prop_firm_state.current_drawdown_pct = (
                (self.prop_firm_state.peak_equity - equity) / self.prop_firm_state.peak_equity
            )

        if equity <= self.initial_capital * (1.0 - self.drawdown_limit_pct):
            self.prop_firm_state.account_failed = True
            self.prop_firm_state.daily_halt = True
            self._record_halt(self.current_date or pd.Timestamp.now(tz=timezone.utc), "DRAW_DOWN_FLOOR", equity)
            logger.warning("drawdown_floor_breach", equity=equity, threshold=self.initial_capital * (1.0 - self.drawdown_limit_pct))

        if equity - self.daily_start_equity if self.daily_start_equity is not None else 0.0 <= -self.initial_capital * self.daily_loss_limit_pct:
            self.prop_firm_state.daily_halt = True
            self._record_halt(self.current_date or pd.Timestamp.now(tz=timezone.utc), "DAILY_LOSS_LIMIT", equity)
            logger.warning("daily_loss_limit_reached", equity=equity, daily_pnl=equity - (self.daily_start_equity or equity))

        profit = equity - self.initial_capital
        if not self.prop_firm_state.step1_unlocked and profit >= self.initial_capital * self.step1_profit_pct:
            self.prop_firm_state.step1_unlocked = True
            logger.info("step1_unlocked", equity=equity, target=self.initial_capital * (1.0 + self.step1_profit_pct))
        if not self.prop_firm_state.step2_unlocked and profit >= self.initial_capital * self.step2_profit_pct:
            self.prop_firm_state.step2_unlocked = True
            logger.info("step2_unlocked", equity=equity, target=self.initial_capital * (1.0 + self.step2_profit_pct))

    def _apply_commission(self, cost: float) -> float:
        return max(cost * self.commission_pct, 0.0)

    def _open_position(
        self,
        side: str,
        price_a: Optional[float],
        price_b: Optional[float],
        target_notional: float,
        beta: Optional[float] = None,
    ) -> None:
        equity = self._compute_equity(price_a, price_b, None)
        if equity <= 0:
            return

        max_notional = equity * self.leverage_limit
        if target_notional > max_notional:
            logger.warning(
                "leverage_clip",
                requested_notional=target_notional,
                max_notional=max_notional,
                equity=equity,
            )
            target_notional = max_notional
            self.prop_firm_state.daily_halt = False

        if price_a is not None and price_b is not None:
            # Pair trading legs split evenly across both assets.
            notional_each = target_notional / 2.0
            self.position_a = (1.0 if side == "LONG" else -1.0) * notional_each / price_a
            self.position_b = (-1.0 if side == "LONG" else 1.0) * notional_each / price_b
            tx_a = self.cost_model.apply(
                price=price_a,
                size=self.position_a,
                side="buy" if self.position_a > 0 else "sell",
            )
            tx_b = self.cost_model.apply(
                price=price_b,
                size=self.position_b,
                side="buy" if self.position_b > 0 else "sell",
            )
            self.entry_price_a = tx_a["executed_price"]
            self.entry_price_b = tx_b["executed_price"]
            self.entry_side = side
            self.entry_timestamp = self.current_date
            self.cash -= tx_a["total_cost"] + tx_b["total_cost"]
            self.trades.append(
                {
                    "timestamp": self.current_date,
                    "event": "OPEN",
                    "side": side,
                    "qty_a": self.position_a,
                    "qty_b": self.position_b,
                    "price_a": self.entry_price_a,
                    "price_b": self.entry_price_b,
                    "commission": tx_a["commission"] + tx_b["commission"],
                    "slippage": tx_a["slippage_pct"] + tx_b["slippage_pct"],
                    "pnl": None,
                    "note": "opened pair position",
                }
            )
        elif price_a is not None:
            qty = (target_notional / price_a) * (1.0 if side == "LONG" else -1.0)
            self.position_a = qty
            tx = self.cost_model.apply(
                price=price_a,
                size=qty,
                side="buy" if qty > 0 else "sell",
            )
            self.entry_price_a = tx["executed_price"]
            self.entry_side = side
            self.entry_timestamp = self.current_date
            self.cash -= tx["total_cost"]
            self.trades.append(
                {
                    "timestamp": self.current_date,
                    "event": "OPEN",
                    "side": side,
                    "qty": qty,
                    "price": self.entry_price_a,
                    "commission": tx["commission"],
                    "slippage": tx["slippage_pct"],
                    "pnl": None,
                    "note": "opened single asset position",
                }
            )

    def _close_position(
        self,
        price_a: Optional[float],
        price_b: Optional[float],
    ) -> None:
        if self.position_a == 0 and self.position_b == 0:
            return

        pnl = 0.0
        commission = 0.0
        slippage_pct = 0.0
        side = self.entry_side or "UNKNOWN"
        if price_a is not None and self.position_a != 0.0:
            tx_a = self.cost_model.apply(
                price=price_a,
                size=-self.position_a,
                side="sell" if self.position_a > 0 else "buy",
            )
            commission += tx_a["commission"]
            slippage_pct += tx_a["slippage_pct"]
            pnl += self.position_a * (tx_a["executed_price"] - (self.entry_price_a or tx_a["executed_price"]))
            if self.position_a > 0:
                self.cash += self.position_a * tx_a["executed_price"]
            else:
                self.cash -= abs(self.position_a) * tx_a["executed_price"]
        if price_b is not None and self.position_b != 0.0:
            tx_b = self.cost_model.apply(
                price=price_b,
                size=-self.position_b,
                side="sell" if self.position_b > 0 else "buy",
            )
            commission += tx_b["commission"]
            slippage_pct += tx_b["slippage_pct"]
            pnl += self.position_b * (tx_b["executed_price"] - (self.entry_price_b or tx_b["executed_price"]))
            if self.position_b > 0:
                self.cash += self.position_b * tx_b["executed_price"]
            else:
                self.cash -= abs(self.position_b) * tx_b["executed_price"]

        self.cash -= commission

        self.trades.append(
            {
                "timestamp": self.current_date,
                "event": "CLOSE",
                "side": side,
                "qty_a": self.position_a,
                "qty_b": self.position_b,
                "price_a": price_a,
                "price_b": price_b,
                "commission": commission,
                "slippage": slippage_pct,
                "pnl": pnl,
                "note": "closed position",
            }
        )
        self.position_a = 0.0
        self.position_b = 0.0
        self.entry_price_a = None
        self.entry_price_b = None
        self.entry_side = None
        self.entry_timestamp = None

    def run(self, market_data: pd.DataFrame, signals: pd.DataFrame) -> Dict[str, object]:
        merged = pd.merge(
            market_data.copy(),
            signals.copy(),
            on="timestamp",
            how="inner",
            suffixes=("", "_signal"),
        )
        merged["timestamp"] = pd.to_datetime(merged["timestamp"], utc=True)
        merged = merged.sort_values("timestamp").reset_index(drop=True)

        if merged.empty:
            logger.error("backtest_empty_merged_data")
            return self._build_results()

        self.reset()
        self.daily_start_equity = self.initial_capital
        self.current_date = merged["timestamp"].iloc[0]

        prev_equity: Optional[float] = None
        for row in merged.itertuples(index=False):
            row_ts = pd.Timestamp(row.timestamp)
            if self.current_date is None or row_ts.date() != self.current_date.date():
                self.current_date = row_ts
                self.daily_start_equity = self._compute_equity(
                    getattr(row, "price_a", None), getattr(row, "price_b", None), getattr(row, "price", None)
                )
                self.prop_firm_state.daily_halt = False

            equity = self._compute_equity(
                getattr(row, "price_a", None), getattr(row, "price_b", None), getattr(row, "price", None)
            )
            self.prop_firm_state.daily_pnl = equity - self.daily_start_equity

            self._update_propfirm(equity)
            if self.prop_firm_state.account_failed:
                if self.position_a != 0.0 or self.position_b != 0.0:
                    self._close_position(getattr(row, "price_a", None), getattr(row, "price_b", None))
                break

            target_position_a = getattr(row, "position_a", 0.0)
            target_position_b = getattr(row, "position_b", 0.0)
            target_position = getattr(row, "position", 0.0)
            target_notional = getattr(row, "target_notional", None)
            if target_notional is None:
                target_notional = equity * self.leg_allocation_pct

            if target_position_a != 0.0 or target_position_b != 0.0:
                if self.position_a == 0 and self.position_b == 0:
                    if not self.prop_firm_state.daily_halt:
                        self._open_position(
                            "LONG" if target_position_a > 0 else "SHORT",
                            getattr(row, "price_a", None),
                            getattr(row, "price_b", None),
                            target_notional,
                        )
                elif np.sign(target_position_a) != np.sign(self.position_a) or np.sign(target_position_b) != np.sign(self.position_b):
                    self._close_position(getattr(row, "price_a", None), getattr(row, "price_b", None))
                    if not self.prop_firm_state.daily_halt:
                        self._open_position(
                            "LONG" if target_position_a > 0 else "SHORT",
                            getattr(row, "price_a", None),
                            getattr(row, "price_b", None),
                            target_notional,
                        )
            elif target_position != 0.0:
                if self.position_a == 0 and self.position_b == 0:
                    if not self.prop_firm_state.daily_halt:
                        self._open_position(
                            "LONG" if target_position > 0 else "SHORT",
                            getattr(row, "price", None),
                            None,
                            target_notional,
                        )
                else:
                    self._close_position(getattr(row, "price", None), None)
            else:
                if self.position_a != 0.0 or self.position_b != 0.0:
                    self._close_position(getattr(row, "price_a", None), getattr(row, "price_b", None))

            equity = self._compute_equity(
                getattr(row, "price_a", None), getattr(row, "price_b", None), getattr(row, "price", None)
            )
            daily_return = float(equity / prev_equity - 1.0) if prev_equity is not None and prev_equity > 0 else 0.0
            self.equity_curve.append(
                {
                    "timestamp": row_ts,
                    "equity": equity,
                    "daily_return": daily_return,
                    "daily_pnl": self.prop_firm_state.daily_pnl,
                    "drawdown_pct": self.prop_firm_state.current_drawdown_pct * 100,
                    "daily_halt": self.prop_firm_state.daily_halt,
                    "account_failed": self.prop_firm_state.account_failed,
                }
            )
            prev_equity = equity

        return self._build_results()

    def _build_results(self) -> Dict[str, object]:
        equity_series = pd.Series(
            [row["equity"] for row in self.equity_curve],
            index=[row["timestamp"] for row in self.equity_curve],
        )
        daily_returns = equity_series.pct_change().dropna()
        mean_return = daily_returns.mean() if len(daily_returns) else 0.0
        std_return = daily_returns.std() if len(daily_returns) else 0.0
        sharpe = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0.0
        worst_drawdown = ((equity_series - equity_series.cummax()) / equity_series.cummax()).min() if len(equity_series) else 0.0
        final_equity = equity_series.iloc[-1] if len(equity_series) else self.cash
        total_return = (final_equity - self.initial_capital) / self.initial_capital

        return {
            "initial_capital": self.initial_capital,
            "final_equity": final_equity,
            "total_return": total_return,
            "total_return_pct": total_return * 100,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": worst_drawdown * 100,
            "num_trades": len(self.trades),
            "win_rate": self._compute_win_rate(),
            "equity_curve": self.equity_curve,
            "trades": self.trades,
            "prop_firm_state": self.prop_firm_state,
        }

    def _compute_win_rate(self) -> float:
        closed = [t for t in self.trades if t.get("event") == "CLOSE" and t.get("pnl") is not None]
        if not closed:
            return 0.0
        wins = sum(1 for t in closed if t["pnl"] > 0)
        return wins / len(closed)
