"""
Statistical Arbitrage Strategy.

This module implements the core statistical arbitrage strategy
based on mean-reverting spreads between correlated assets.

Mathematical Framework:
    Spread = log(P_A) - β * log(P_B)
    Z-score = (Spread - μ) / σ
    
Trading Rules:
    - Long spread (buy A, sell B) when z-score < -2
    - Short spread (sell A, buy B) when z-score > 2
    - Exit when z-score reverts to 0
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple

from strategies.base import BasePairsStrategy
from strategies.registry import StrategyRegistry


class StatArbStrategy:
    """Statistical Arbitrage Strategy Implementation."""
    
    def __init__(self, entry_threshold: float = 2.0, exit_threshold: float = 0.5, 
                 use_fixed_window: bool = True, fixed_window_size: int = 60):
        """
        Initialize statistical arbitrage strategy.
        
        Args:
            entry_threshold: Z-score threshold for entry (default: 2.0 std deviations)
            exit_threshold: Z-score threshold for exit (default: 0.5 std deviations)
            use_fixed_window: If True, use fixed training window; if False, use rolling (deprecated)
            fixed_window_size: Size of fixed training window (default: 60 days)
        """
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.use_fixed_window = use_fixed_window
        self.fixed_window_size = fixed_window_size
        self.signals = None
        self.fixed_mean = None  # Will store fixed mean from training period
        self.fixed_std = None   # Will store fixed std from training period
        
    def calculate_spread(self, price_a: pd.Series, price_b: pd.Series, hedge_ratio: float) -> pd.Series:
        """
        Calculate the spread between two assets.
        
        Formula: spread = log(P_A) - β * log(P_B)
        
        Args:
            price_a: Price series for asset A
            price_b: Price series for asset B
            hedge_ratio: Hedge ratio (β) from regression
            
        Returns:
            Series of spread values
        """
        log_a = np.log(price_a)
        log_b = np.log(price_b)
        return log_a - hedge_ratio * log_b
    
    def calculate_z_score(self, spread: pd.Series, window: int = 20, training_end_idx: int = None) -> pd.Series:
        """
        Calculate Z-score for the spread.
        
        Supports two modes:
        1. Fixed window (RECOMMENDED): Uses statistics from training period only
           - Eliminates window drift
           - More stable baseline
           
        2. Rolling window (DEPRECATED): Updates baseline daily
           - Can cause false signals (window chasing)
           - Use only for comparison
        
        Formula: z = (spread - mean) / std
        
        Args:
            spread: Series of spread values
            window: Window size (used only if use_fixed_window=False)
            training_end_idx: End index of training period (for fixed window mode)
            
        Returns:
            Series of Z-score values
        """
        if self.use_fixed_window:
            # FIXED WINDOW MODE (recommended)
            # Use only the training period to calculate mean and std
            if training_end_idx is None:
                training_end_idx = min(self.fixed_window_size, len(spread))
            
            training_spread = spread.iloc[:training_end_idx]
            mean = training_spread.mean()
            std = training_spread.std()
            
            # Store for later reference
            self.fixed_mean = mean
            self.fixed_std = std
            
            # Calculate z-score using FIXED stats (no rolling)
            z_score = (spread - mean) / std
            
        else:
            # ROLLING WINDOW MODE (deprecated, problematic)
            # This is the old approach - kept for backwards compatibility
            mean = spread.rolling(window).mean()
            std = spread.rolling(window).std()
            z_score = (spread - mean) / std
        
        return z_score
    
    def generate_signals(self, z_score: pd.Series, spread: pd.Series = None) -> pd.DataFrame:
        """
        Generate trading signals based on Z-score.
        
        Signal Rules:
            - 1: Long spread (buy A, short B) when z < -entry_threshold
            - -1: Short spread (short A, buy B) when z > entry_threshold
            - 0: Close when |z| < exit_threshold AND spread has reverted
            
        Args:
            z_score: Series of Z-score values
            spread: Series of spread values (required for reversion validation)
            
        Returns:
            DataFrame with signals and positions
        """
        signals = pd.DataFrame(index=z_score.index)
        signals['z_score'] = z_score
        if spread is not None:
            signals['spread'] = spread

        # Stateful entry/exit carry.
        #
        # BUG FIX: the previous implementation set signal=+/-1 only on the exact
        # threshold-crossing bar and relied on `ffill()` to hold the position —
        # but the column contained 0s (not NaN) between crossings, so ffill was a
        # no-op and exit_threshold was never used. Positions therefore never held
        # and never closed at the exit band. This carries the target position from
        # entry (|z| > entry_threshold) until mean reversion (|z| < exit_threshold).
        target = 0
        entry_spread = None
        signal_col: list[int] = []
        entry_spread_col: list[float] = []

        for idx in signals.index:
            z = signals.loc[idx, 'z_score']
            sp = signals.loc[idx, 'spread'] if spread is not None else np.nan

            if target == 0:
                if z > self.entry_threshold:
                    target = -1            # spread rich -> short A / long B
                    entry_spread = sp
                elif z < -self.entry_threshold:
                    target = 1             # spread cheap -> long A / short B
                    entry_spread = sp
            elif pd.notna(z) and abs(z) < self.exit_threshold:
                target = 0                 # reverted -> flat
                entry_spread = None

            signal_col.append(target)
            entry_spread_col.append(entry_spread if target != 0 else np.nan)

        signals['signal'] = signal_col
        signals['entry_spread'] = entry_spread_col

        # Position sizes (normalized): long spread = long A / short B.
        signals['position_a'] = signals['signal']
        signals['position_b'] = -signals['signal']

        self.signals = signals
        return signals
    
    def get_trades(self, signals: pd.DataFrame) -> pd.DataFrame:
        """
        Extract trade entries and exits from signals.
        
        For each trade, validates whether actual spread reversion occurred
        (not just z-score threshold crossing).
        
        Args:
            signals: DataFrame with signals
            
        Returns:
            DataFrame with trade information including reversion validation
        """
        trades = []
        prev_signal = 0
        entry_date = None
        entry_z = None
        entry_spread = None
        position_type = None
        
        for date, row in signals.iterrows():
            current_signal = row['signal']
            current_z = row['z_score']
            current_spread = row.get('spread', np.nan)
            
            # Entry
            if prev_signal == 0 and current_signal != 0:
                entry_date = date
                entry_z = current_z
                entry_spread = current_spread
                position_type = 'Long' if current_signal == 1 else 'Short'
            
            # Exit
            elif prev_signal != 0 and current_signal == 0:
                # Validate whether spread actually reverted
                spread_reverted = False
                reversion_distance = np.nan
                
                if not np.isnan(entry_spread) and not np.isnan(current_spread):
                    # For long positions: spread should decrease (improve)
                    if position_type == 'Long':
                        spread_reverted = (current_spread < entry_spread)
                        reversion_distance = entry_spread - current_spread
                    # For short positions: spread should increase
                    elif position_type == 'Short':
                        spread_reverted = (current_spread > entry_spread)
                        reversion_distance = current_spread - entry_spread
                
                trades.append({
                    'entry_date': entry_date,
                    'exit_date': date,
                    'entry_z': entry_z,
                    'exit_z': current_z,
                    'entry_spread': entry_spread,
                    'exit_spread': current_spread,
                    'position_type': position_type,
                    'duration_days': (date - entry_date).days,
                    'spread_reverted': spread_reverted,
                    'reversion_distance': reversion_distance,
                    'signal_validity': 'VALID' if spread_reverted else 'FALSE_SIGNAL (window drift)'
                })
                entry_date = None
                entry_z = None
                entry_spread = None
                position_type = None

            prev_signal = current_signal

        return pd.DataFrame(trades)

    # ------------------------------------------------------------------
    # Single signal authority (Rule 1).
    #
    # ``signals_from_pair_prices`` and ``to_db_signals`` are the ONLY place
    # pairs signals are produced and mapped to the ``signals`` table. The
    # backtest engine (``EvaluationBacktestEngine.generate_pair_signals`` /
    # ``signals_for_database``) and the ``python main.py signals`` command both
    # delegate here, so the signal distribution is identical by construction.
    # ------------------------------------------------------------------

    @staticmethod
    def _hedge_ratio_from_training(log_a: pd.Series, log_b: pd.Series, end: int) -> float:
        """OLS hedge ratio beta: log_a ~ beta * log_b on the training slice."""
        la = log_a.iloc[:end].values
        lb = log_b.iloc[:end].values
        if len(la) < 10 or np.std(lb) == 0:
            return 1.0
        beta = np.cov(la, lb)[0, 1] / np.var(lb)
        return float(beta) if np.isfinite(beta) else 1.0

    def signals_from_pair_prices(self, pair_df: pd.DataFrame) -> pd.DataFrame:
        """Aligned pair prices -> canonical signal frame.

        Args:
            pair_df: DataFrame with ``timestamp``, ``close_a`` and ``close_b``
                columns (already aligned on timestamp, ascending).

        Returns:
            DataFrame with ``timestamp``, ``close_a``, ``close_b``, ``spread``,
            ``spread_mean``, ``spread_std``, ``z_score``, ``hedge_ratio`` and the
            stateful ``signal`` (-1/0/1) plus ``position_a`` / ``position_b``.
            The hedge ratio, spread baseline and z-score are computed from the
            fixed training window only (``fixed_window_size``), matching the
            backtest engine exactly.
        """
        pair_df = pair_df.reset_index(drop=True)
        training_end = min(self.fixed_window_size, len(pair_df))
        log_a = np.log(pair_df["close_a"])
        log_b = np.log(pair_df["close_b"])
        beta = self._hedge_ratio_from_training(log_a, log_b, training_end)

        spread = self.calculate_spread(pair_df["close_a"], pair_df["close_b"], beta)
        z = self.calculate_z_score(spread, training_end_idx=training_end)
        train_spread = spread.iloc[:training_end]
        spread_mean = float(train_spread.mean())
        spread_std = float(train_spread.std()) if train_spread.std() > 0 else 1.0

        sig = self.generate_signals(
            z.reset_index(drop=True), spread.reset_index(drop=True)
        )

        out = pair_df[["timestamp", "close_a", "close_b"]].copy()
        out["spread"] = spread.values
        out["spread_mean"] = spread_mean
        out["spread_std"] = spread_std
        out["z_score"] = z.values
        out["hedge_ratio"] = beta
        out["signal"] = sig["signal"].values
        out["position_a"] = sig["position_a"].values
        out["position_b"] = sig["position_b"].values
        return out

    @staticmethod
    def to_db_signals(
        signals_df: pd.DataFrame, symbol_a: str, symbol_b: str
    ) -> pd.DataFrame:
        """Map the stateful integer ``signal`` (-1/0/1) to ``signals`` enum rows.

        Long spread (+1) -> BUY, short spread (-1) -> SELL, flat (0) -> HOLD.
        This is the single mapper used by both the signals command and the
        backtest persistence path.
        """
        sig_map = {1: "BUY", -1: "SELL", 0: "HOLD"}
        rows = []
        for _, row in signals_df.iterrows():
            raw = row.get("signal")
            s = int(raw) if pd.notna(raw) else 0
            sig = sig_map.get(s, "HOLD")
            pos_a = 1 if sig == "BUY" else (-1 if sig == "SELL" else 0)
            hedge = row.get("hedge_ratio", 1.0)
            pos_b = int(round(-pos_a * hedge)) if pos_a else 0
            z = row.get("z_score")
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


class WalkForwardStatArb:
    """Walk-forward-compatible adapter for the stat-arb pair strategy.

    Implements the contract expected by
    :class:`~backtesting.walk_forward.WalkForwardRunner`:

      - ``prepare(train_df)``: fit the hedge ratio and the fixed spread baseline
        (mean/std) on the in-sample training fold.
      - ``generate_signals(test_df)``: z-score the out-of-sample spread against
        the *frozen* baseline and emit per-bar target positions.

    Input frames must contain ``timestamp``, ``price_a`` and ``price_b`` columns
    (the aligned pair format used by :class:`backtesting.engine.BacktestEngine`).
    Output is a DataFrame of ``timestamp``/``position_a``/``position_b`` holding
    the carried target position on every bar (not just at crossings), so the
    engine keeps the position open between entry and exit thresholds.
    """

    def __init__(
        self,
        entry_threshold: float = 2.0,
        exit_threshold: float = 0.5,
        require_cointegration: bool = False,
        coint_pvalue: float = 0.05,
        entropy_threshold: Optional[float] = None,
        entropy_window: int = 30,
        entropy_embedding: int = 3,
    ):
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        # Phase 1 permutation-entropy gate. When entropy_threshold is set, NEW
        # entries are suppressed on bars whose trailing-window spread entropy is
        # >= the threshold (noisy regime). Exits are never gated, so an open
        # position can always reach its mean-reversion target. The estimate is
        # strictly causal (trailing window), so it is safe in the OOS window.
        self.entropy_threshold = entropy_threshold
        self.entropy_window = entropy_window
        self.entropy_embedding = entropy_embedding
        # Causal pair-selection gate. When enabled, a fold only trades if the
        # spread is stationary on the TRAINING window (ADF p < coint_pvalue).
        # This removes look-ahead selection bias: the decision to trade a pair in
        # the OOS test window uses only in-sample (train-fold) information,
        # instead of accepting pairs via a full-sample stationarity test.
        self.require_cointegration = require_cointegration
        self.coint_pvalue = coint_pvalue
        self.is_cointegrated: bool = True
        self.hedge_ratio: float = 1.0
        self.baseline_mean: float = 0.0
        self.baseline_std: float = 1.0

    @staticmethod
    def _spread(price_a: pd.Series, price_b: pd.Series, hedge_ratio: float) -> pd.Series:
        return np.log(price_a) - hedge_ratio * np.log(price_b)

    def prepare(self, train_df: pd.DataFrame) -> None:
        log_a = np.log(train_df["price_a"].astype(float))
        log_b = np.log(train_df["price_b"].astype(float))
        # OLS hedge ratio of log(A) on log(B); fall back to 1.0 if degenerate.
        if log_b.std() > 0:
            self.hedge_ratio = float(np.polyfit(log_b, log_a, 1)[0])
        else:
            self.hedge_ratio = 1.0
        spread = log_a - self.hedge_ratio * log_b
        self.baseline_mean = float(spread.mean())
        std = float(spread.std())
        self.baseline_std = std if std > 0 else 1.0

        # Causal cointegration check on the TRAIN-fold spread only.
        if self.require_cointegration:
            from features.price_features import test_stationarity

            stationary, _ = test_stationarity(spread, pair_name="walk_forward")
            self.is_cointegrated = bool(stationary)
        else:
            self.is_cointegrated = True

    def generate_signals(self, test_df: pd.DataFrame) -> pd.DataFrame:
        # If the pair failed the causal (train-fold) cointegration gate, stay flat
        # for the whole OOS window rather than trading a non-mean-reverting spread.
        if self.require_cointegration and not self.is_cointegrated:
            out = pd.DataFrame({"timestamp": test_df["timestamp"].reset_index(drop=True)})
            out["position_a"] = 0
            out["position_b"] = 0
            return out

        spread = self._spread(
            test_df["price_a"].astype(float),
            test_df["price_b"].astype(float),
            self.hedge_ratio,
        )
        z = (spread - self.baseline_mean) / self.baseline_std

        # Causal entropy gate: True where the regime is calm enough to OPEN a
        # position. Computed on the OOS spread itself (trailing window only).
        if self.entropy_threshold is not None:
            from features.permutation_entropy import tradeable_mask

            gate = tradeable_mask(
                spread.reset_index(drop=True),
                threshold=self.entropy_threshold,
                window=self.entropy_window,
                embedding_dimension=self.entropy_embedding,
            ).to_numpy()
        else:
            gate = np.ones(len(z), dtype=bool)

        positions = []
        target = 0  # carried target spread position: +1 long spread, -1 short spread
        for i, z_val in enumerate(z):
            if target == 0:
                if gate[i]:  # only enter when the regime is tradeable
                    if z_val > self.entry_threshold:
                        target = -1   # spread rich -> short A / long B
                    elif z_val < -self.entry_threshold:
                        target = 1    # spread cheap -> long A / short B
            elif abs(z_val) < self.exit_threshold:
                target = 0
            positions.append(target)

        out = pd.DataFrame({"timestamp": test_df["timestamp"].reset_index(drop=True)})
        out["position_a"] = positions
        out["position_b"] = [-p for p in positions]
        return out


@StrategyRegistry.register(
    description="Z-score mean-reversion on cointegrated crypto pairs (long/short legs)",
    tier_hints=["CONSERVATIVE", "STANDARD"],
    tags=["pairs", "stat-arb", "market-neutral"],
)
class StatArbPairsStrategy(BasePairsStrategy):
    """BaseStrategy-compatible wrapper for statistical arbitrage (pairs).

    Implements generate_signals_pair() which is the canonical signal path
    for all pairs strategies in the walk-forward engine.  The thresholds
    stored here are the single source of truth — no other module should
    re-implement z-score thresholds independently.
    """

    name = "Statistical Arbitrage"
    min_bars = 60
    param_grid = {"entry_z": 2.0, "exit_z": 0.5, "lookback": 60}

    def get_min_bars(self, params: Dict) -> int:
        return int(params.get("lookback", self.param_grid["lookback"]))

    def _core(self, params: Dict) -> "StatArbStrategy":
        """Build the single signal-authority core from this strategy's params.

        All pairs signal logic lives in ``StatArbStrategy.signals_from_pair_prices``
        (the stateful entry/exit carry used by the backtest). These wrappers
        delegate to it so there is exactly one z-score/threshold implementation
        (Rule 1) — no independent re-derivation here.
        """
        return StatArbStrategy(
            entry_threshold=float(params.get("entry_z", self.param_grid["entry_z"])),
            exit_threshold=float(params.get("exit_z", self.param_grid["exit_z"])),
            use_fixed_window=True,
            fixed_window_size=int(params.get("lookback", self.param_grid["lookback"])),
        )

    @staticmethod
    def _merge_pair(df_a: pd.DataFrame, df_b: pd.DataFrame) -> pd.DataFrame:
        return (
            pd.merge(
                df_a[["timestamp", "close"]].rename(columns={"close": "close_a"}),
                df_b[["timestamp", "close"]].rename(columns={"close": "close_b"}),
                on="timestamp",
                how="inner",
            )
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    def generate_signals_pair(
        self, df_a: pd.DataFrame, df_b: pd.DataFrame, params: Dict
    ) -> pd.DataFrame:
        """Return DataFrame[timestamp, position_a, position_b] for each bar.

        Delegates to the single signal authority (stateful carry).
        """
        lookback = int(params.get("lookback", self.param_grid["lookback"]))
        merged = self._merge_pair(df_a, df_b)
        if len(merged) < lookback + 5:
            out = merged[["timestamp"]].copy()
            out["position_a"] = 0
            out["position_b"] = 0
            return out

        frame = self._core(params).signals_from_pair_prices(merged)
        return frame[["timestamp", "position_a", "position_b"]].copy()

    def signals_to_db_format(
        self, df_a: pd.DataFrame, df_b: pd.DataFrame, params: Dict,
        symbol_a: str, symbol_b: str,
    ) -> pd.DataFrame:
        """Produce DB-compatible signal rows (BUY/SELL/HOLD) for the signals table.

        Delegates to the single signal authority — the SAME stateful path the
        backtest engine uses — so ``python main.py signals`` and ``backtest``
        produce identical signal distributions (Phase-1 gate).
        """
        lookback = int(params.get("lookback", self.param_grid["lookback"]))
        merged = self._merge_pair(df_a, df_b)
        if len(merged) < lookback + 5:
            return pd.DataFrame(
                columns=["symbol_a", "symbol_b", "timestamp", "signal", "z_score", "position_a", "position_b"]
            )

        frame = self._core(params).signals_from_pair_prices(merged)
        return StatArbStrategy.to_db_signals(frame, symbol_a, symbol_b)
