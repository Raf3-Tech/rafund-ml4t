"""
Evaluation backtest engine: statistical arbitrage (pairs) + prop-firm rules.

Strategy:
  - Trade one cointegrated pair (spread mean-reversion)
  - Fixed 60-day training window for spread mean/std
  - Long spread when z < -entry; short spread when z > +entry; exit near 0
  - Dollar-neutral legs with leverage, daily-loss, and drawdown limits
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backtesting.evaluation_rules import (
    EvaluationStatus,
    EvaluationTracker,
    PropFirmRules,
)
from strategies.stat_arb import StatArbStrategy

logger = logging.getLogger(__name__)


def _hedge_ratio_from_training(log_a: pd.Series, log_b: pd.Series, end: int) -> float:
    """OLS hedge ratio beta: log_a ~ beta * log_b on training slice."""
    la = log_a.iloc[:end].values
    lb = log_b.iloc[:end].values
    if len(la) < 10 or np.std(lb) == 0:
        return 1.0
    beta = np.cov(la, lb)[0, 1] / np.var(lb)
    return float(beta) if np.isfinite(beta) else 1.0


def prepare_pair_prices(
    prices: pd.DataFrame, symbol_a: str, symbol_b: str
) -> pd.DataFrame:
    """Align two symbols on timestamp with close prices."""
    pa = prices[prices["symbol"] == symbol_a][["timestamp", "close"]].rename(
        columns={"close": "close_a"}
    )
    pb = prices[prices["symbol"] == symbol_b][["timestamp", "close"]].rename(
        columns={"close": "close_b"}
    )
    merged = pa.merge(pb, on="timestamp", how="inner").sort_values("timestamp")
    return merged.reset_index(drop=True)


class EvaluationBacktestEngine:
    """Pairs stat-arb backtest with strict prop-firm evaluation rules."""

    def __init__(
        self,
        rules: Optional[PropFirmRules] = None,
        symbol_a: str = "BTC/USDT",
        symbol_b: str = "ETH/USDT",
        entry_threshold: float = 2.0,
        exit_threshold: float = 0.5,
        lookback: int = 60,
        leg_allocation_pct: float = 0.18,
        commission: float = 0.001,
        stop_loss_spread_pct: float = 0.03,
        max_holding_days: int = 30,
    ):
        self.rules = rules or PropFirmRules()
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.lookback = lookback
        self.leg_allocation_pct = leg_allocation_pct
        self.commission = commission
        self.stop_loss_spread_pct = stop_loss_spread_pct
        self.max_holding_days = max_holding_days

        self.strategy = StatArbStrategy(
            entry_threshold=entry_threshold,
            exit_threshold=exit_threshold,
            use_fixed_window=True,
            fixed_window_size=lookback,
        )

        self.cash = self.rules.account_size
        self.tracker = EvaluationTracker(self.rules)
        self.trades: List[dict] = []
        self.daily_equity: List[Tuple[pd.Timestamp, float]] = []

        # Open spread position: +1 long spread, -1 short spread, 0 flat
        self.position: int = 0
        self.qty_a = 0.0
        self.qty_b = 0.0
        self.entry_equity = 0.0
        self.entry_date = None

    def generate_pair_signals(self, pair_df: pd.DataFrame) -> pd.DataFrame:
        """Build daily signal series from spread z-score."""
        training_end = min(self.lookback, len(pair_df))
        log_a = np.log(pair_df["close_a"])
        log_b = np.log(pair_df["close_b"])
        beta = _hedge_ratio_from_training(log_a, log_b, training_end)

        spread = self.strategy.calculate_spread(
            pair_df["close_a"], pair_df["close_b"], beta
        )
        z = self.strategy.calculate_z_score(spread, training_end_idx=training_end)
        train_spread = spread.iloc[:training_end]
        spread_mean = float(train_spread.mean())
        spread_std = float(train_spread.std()) if train_spread.std() > 0 else 1.0

        out = pair_df[["timestamp", "close_a", "close_b"]].copy()
        out["z_score"] = z.values
        out["spread"] = spread.values
        out["spread_mean"] = spread_mean
        out["spread_std"] = spread_std
        out["hedge_ratio"] = beta
        return out

    @staticmethod
    def signals_for_database(signals_df: pd.DataFrame, symbol_a: str, symbol_b: str) -> pd.DataFrame:
        """Map z-scores to DB signal_type enum rows."""
        rows = []
        for _, row in signals_df.iterrows():
            z = row["z_score"]
            if pd.isna(z):
                sig = "HOLD"
            elif z < -2.0:
                sig = "BUY"
            elif z > 2.0:
                sig = "SELL"
            elif abs(z) <= 0.5:
                sig = "CLOSE"
            else:
                sig = "HOLD"
            pos_a = 1 if sig == "BUY" else (-1 if sig == "SELL" else 0)
            pos_b = int(round(-pos_a * row["hedge_ratio"])) if pos_a else 0
            rows.append(
                {
                    "symbol_a": symbol_a,
                    "symbol_b": symbol_b,
                    "timestamp": row["timestamp"],
                    "signal": sig,
                    "z_score": float(z) if pd.notna(z) else None,
                    "position_a": pos_a,
                    "position_b": pos_b,
                }
            )
        return pd.DataFrame(rows)

    def _equity(self, price_a: float, price_b: float) -> float:
        eq = self.cash
        if self.qty_a:
            eq += self.qty_a * price_a
        if self.qty_b:
            eq += self.qty_b * price_b
        return eq

    def _notional(self, price_a: float, price_b: float) -> float:
        return abs(self.qty_a * price_a) + abs(self.qty_b * price_b)

    def _open_spread(
        self, direction: int, price_a: float, price_b: float, ts, beta: float
    ) -> bool:
        """Open dollar-neutral pair: long spread (+1) or short spread (-1)."""
        equity = self._equity(price_a, price_b)
        leg_value = equity * self.leg_allocation_pct
        if leg_value <= 0:
            return False

        # direction 1: buy A, sell B; direction -1: sell A, buy B
        qty_a = (leg_value / price_a) * direction
        qty_b = -(leg_value / price_b) * direction * beta
        notional = abs(qty_a * price_a) + abs(qty_b * price_b)
        if not self.tracker.check_leverage(equity, notional):
            return False

        cost = (abs(qty_a) * price_a + abs(qty_b) * price_b) * self.commission
        if self.cash < cost:
            return False

        self.cash -= cost
        self.qty_a = qty_a
        self.qty_b = qty_b
        self.position = direction
        self.entry_equity = equity
        self.entry_date = ts

        self.trades.append(
            {
                "date": ts,
                "symbol_a": self.symbol_a,
                "symbol_b": self.symbol_b,
                "side": "LONG_SPREAD" if direction == 1 else "SHORT_SPREAD",
                "price_a": price_a,
                "price_b": price_b,
                "qty_a": qty_a,
                "qty_b": qty_b,
                "status": "OPEN",
            }
        )
        return True

    def _close_spread(self, price_a: float, price_b: float, ts, reason: str = "EXIT") -> None:
        if self.position == 0:
            return

        proceeds_a = self.qty_a * price_a * (1 - self.commission)
        proceeds_b = self.qty_b * price_b * (1 - self.commission)
        self.cash += proceeds_a + proceeds_b

        pnl = self._equity(price_a, price_b) - self.entry_equity
        self.trades.append(
            {
                "date": ts,
                "symbol_a": self.symbol_a,
                "symbol_b": self.symbol_b,
                "side": reason,
                "price_a": price_a,
                "price_b": price_b,
                "status": "CLOSED",
                "pnl": pnl,
            }
        )
        self.qty_a = 0.0
        self.qty_b = 0.0
        self.position = 0
        self.entry_equity = 0.0
        self.entry_date = None

    def run(self, prices: pd.DataFrame) -> Dict:
        pair_df = prepare_pair_prices(prices, self.symbol_a, self.symbol_b)
        if len(pair_df) < self.lookback + 5:
            logger.error(
                "Insufficient overlapping bars for %s / %s (%d rows)",
                self.symbol_a,
                self.symbol_b,
                len(pair_df),
            )
            return self._build_results()

        signals_df = self.generate_pair_signals(pair_df)
        self._signals_df = signals_df
        logger.info(
            "Pair %s / %s | %d days | hedge_ratio=%.4f",
            self.symbol_a,
            self.symbol_b,
            len(signals_df),
            signals_df["hedge_ratio"].iloc[0],
        )

        prev_date = None
        train_end = min(self.lookback, len(signals_df))

        for bar_idx, row in enumerate(signals_df.itertuples(index=False)):
            if bar_idx < train_end:
                continue
            ts = row.timestamp
            price_a, price_b = row.close_a, row.close_b
            z = row.z_score
            beta = row.hedge_ratio
            if pd.isna(z):
                continue

            # New trading day
            if ts != prev_date:
                if prev_date is not None:
                    eq = self._equity(price_a, price_b)
                    self.daily_equity.append((prev_date, eq))
                self.tracker.on_new_day(self._equity(price_a, price_b))
                prev_date = ts

            equity = self._equity(price_a, price_b)
            self.tracker.update_intraday(equity)

            if self.tracker.status in (
                EvaluationStatus.FAILED_DRAWDOWN,
                EvaluationStatus.FAILED_DAILY_LOSS,
                EvaluationStatus.PASSED,
            ):
                if self.position != 0:
                    self._close_spread(price_a, price_b, ts, "EVAL_STOP")
                break

            if not self.tracker.can_trade():
                continue

            # Stop-loss on open spread (mark-to-market vs entry equity)
            if self.position != 0 and self.entry_equity > 0:
                loss_pct = (equity - self.entry_equity) / self.entry_equity
                if loss_pct <= -self.stop_loss_spread_pct:
                    self._close_spread(price_a, price_b, ts, "STOP_LOSS")
                    continue
                if self.entry_date is not None:
                    hold_days = (pd.Timestamp(ts) - pd.Timestamp(self.entry_date)).days
                    if hold_days >= self.max_holding_days:
                        self._close_spread(price_a, price_b, ts, "MAX_HOLD")
                        continue

            # Exit: spread reverted toward mean
            if self.position != 0 and abs(z) <= self.exit_threshold:
                self._close_spread(price_a, price_b, ts, "EXIT")
                continue

            # Entry: mean-reversion on spread z-score
            if self.position == 0:
                if z < -self.entry_threshold:
                    self._open_spread(1, price_a, price_b, ts, beta)
                elif z > self.entry_threshold:
                    self._open_spread(-1, price_a, price_b, ts, beta)

        # Final mark
        if len(signals_df) > 0:
            last = signals_df.iloc[-1]
            eq = self._equity(last["close_a"], last["close_b"])
            self.daily_equity.append((last["timestamp"], eq))
            self.tracker.update_intraday(eq)

        return self._build_results()

    def _build_results(self) -> Dict:
        eval_summary = self.tracker.summary()
        if self.daily_equity:
            final_equity = self.daily_equity[-1][1]
        else:
            final_equity = self.cash

        equity_series = pd.Series(
            [e for _, e in self.daily_equity],
            index=[d for d, _ in self.daily_equity],
        )
        daily_returns = equity_series.pct_change().dropna()
        mean_dr = daily_returns.mean() if len(daily_returns) else 0.0
        std_dr = daily_returns.std() if len(daily_returns) else 0.0
        sharpe = (mean_dr / std_dr * np.sqrt(252)) if std_dr > 0 else 0.0

        if len(equity_series) >= 2:
            cummax = equity_series.cummax()
            max_dd = ((equity_series - cummax) / cummax).min()
        else:
            max_dd = 0.0

        closed = [t for t in self.trades if t.get("status") == "CLOSED"]
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        win_rate = wins / len(closed) if closed else 0.0

        total_return = (final_equity - self.rules.account_size) / self.rules.account_size

        return {
            "initial_capital": self.rules.account_size,
            "final_value": final_equity,
            "total_return": total_return,
            "total_return_pct": total_return * 100,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd * 100,
            "num_trades": len(self.trades),
            "num_closed_trades": len(closed),
            "win_rate": win_rate,
            "win_rate_pct": win_rate * 100,
            "mean_daily_return": mean_dr,
            "daily_volatility": std_dr,
            "trades": self.trades,
            "daily_equity": self.daily_equity,
            "signals_df": getattr(self, "_signals_df", pd.DataFrame()),
            "evaluation": eval_summary,
            "symbol_a": self.symbol_a,
            "symbol_b": self.symbol_b,
            "strategy": "pairs_stat_arb_mean_reversion",
        }
