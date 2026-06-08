"""Base strategy contract for the Raf3nd walk-forward engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

import pandas as pd


class BaseStrategy(ABC):
    name: str = "BaseStrategy"
    min_bars: int = 1
    param_grid: Dict = {}

    def get_min_bars(self, params: Dict) -> int:
        """Return warmup bars required for the given params. Override when min_bars is param-dependent."""
        return self.min_bars

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        """Return a Series of 'BUY', 'SELL', or 'HOLD' aligned to df.index.
        The first get_min_bars(params) entries must always be 'HOLD'.
        df must contain at minimum a 'close' column (OHLCV frame).
        """

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        dd = results.get("max_drawdown_pct", 0.0)
        if trades < 10:
            return f"{self.name}: only {trades} trades — insufficient signals in this window."
        if sharpe <= 0:
            return f"{self.name}: negative Sharpe ({sharpe:.2f}) — signals did not produce consistent edge."
        return f"{self.name}: drawdown {dd:.1f}% exceeded limit — adverse price moves overwhelmed the strategy."


class BasePairsStrategy(BaseStrategy):
    """Extension for two-symbol strategies (e.g. stat-arb)."""

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        raise NotImplementedError("Use generate_signals_pair for pairs strategies.")

    @abstractmethod
    def generate_signals_pair(
        self, df_a: pd.DataFrame, df_b: pd.DataFrame, params: Dict
    ) -> pd.DataFrame:
        """Return a DataFrame with columns: timestamp, position_a, position_b."""
