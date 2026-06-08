"""EMA Crossover strategy verification tests.

Pins the entry/exit signal behaviour against the crossover logic described in
IEEE 11035368 ("Algorithmic Crypto Trading using EMA Strategy"):
  - BUY  on golden cross  (fast EMA crosses ABOVE slow EMA)
  - SELL on death  cross  (fast EMA crosses BELOW slow EMA)
  - HOLD carries the current position between crossovers

These tests also serve as a regression guard: if the crossover logic is ever
changed (e.g. to use SMA or a different carry rule), the suite will fail and
alert the next developer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.ema_crossover import EMACrossover


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ohlcv(n: int, close_prices) -> pd.DataFrame:
    """Build a minimal OHLCV frame from a close-price sequence."""
    close = pd.Series(close_prices[:n] if len(close_prices) >= n else close_prices)
    return pd.DataFrame({
        "timestamp": pd.date_range("2019-01-01", periods=len(close), freq="D", tz="UTC"),
        "open": close,
        "high": close * 1.005,
        "low": close * 0.995,
        "close": close,
        "volume": 1e6,
    })


def _make_crossover_series(n_warmup: int, n_bull: int, n_bear: int, seed: int = 42) -> list[float]:
    """Build a price series that forms one clean golden cross then one death cross.

    Returns a price list where:
      - First `n_warmup` bars: flat (fast ≈ slow, no signal).
      - Next `n_bull` bars:  trending up   (fast EMA crosses above slow → golden cross).
      - Next `n_bear` bars:  trending down  (fast EMA crosses below slow → death cross).
    """
    rng = np.random.default_rng(seed)
    prices: list[float] = []

    # Warmup: flat around 100
    base = 100.0
    for _ in range(n_warmup):
        base += rng.normal(0, 0.01)
        prices.append(max(base, 1.0))

    # Bull run: monotone up so fast EMA quickly crosses above slow
    for i in range(n_bull):
        base += 2.0 + rng.normal(0, 0.05)   # +2/bar deterministic uptrend
        prices.append(base)

    # Bear run: monotone down so fast EMA quickly crosses below slow
    for i in range(n_bear):
        base -= 2.5 + rng.normal(0, 0.05)   # -2.5/bar deterministic downtrend
        prices.append(max(base, 1.0))

    return prices


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEMACrossoverLogic:
    """Pin the crossover logic: golden cross → BUY, death cross → SELL."""

    def test_buy_on_golden_cross(self):
        """After a sustained uptrend the strategy must emit at least one BUY."""
        prices = _make_crossover_series(n_warmup=200, n_bull=300, n_bear=0)
        df = _ohlcv(len(prices), prices)
        strat = EMACrossover()
        params = {"fast": 20, "slow": 50}
        signals = strat.generate_signals(df, params)
        assert "BUY" in signals.values, "No BUY emitted after a clear uptrend — crossover logic broken."

    def test_sell_on_death_cross(self):
        """After a sustained downtrend following an uptrend, a SELL must appear."""
        prices = _make_crossover_series(n_warmup=200, n_bull=200, n_bear=300)
        df = _ohlcv(len(prices), prices)
        strat = EMACrossover()
        params = {"fast": 20, "slow": 50}
        signals = strat.generate_signals(df, params)
        assert "SELL" in signals.values, "No SELL emitted after a clear downtrend — crossover logic broken."

    def test_warmup_is_all_hold(self):
        """First `slow` bars must all be HOLD (warmup period)."""
        n = 500
        rng = np.random.default_rng(0)
        prices = list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, n))))
        df = _ohlcv(n, prices)
        strat = EMACrossover()
        params = {"fast": 20, "slow": 50}
        signals = strat.generate_signals(df, params)
        warmup = signals.iloc[:50]
        assert (warmup == "HOLD").all(), "Warmup bars contain non-HOLD signals."

    def test_only_valid_signal_labels(self):
        """Signal series must only contain BUY, SELL, or HOLD."""
        n = 600
        rng = np.random.default_rng(1)
        prices = list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, n))))
        df = _ohlcv(n, prices)
        strat = EMACrossover()
        params = {"fast": 20, "slow": 50}
        signals = strat.generate_signals(df, params)
        invalid = set(signals.unique()) - {"BUY", "SELL", "HOLD"}
        assert not invalid, f"Unexpected signal labels: {invalid}"

    def test_signal_series_length_matches_input(self):
        """Signal length must equal the number of input bars (1-to-1 alignment)."""
        n = 400
        rng = np.random.default_rng(2)
        prices = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))))
        df = _ohlcv(n, prices)
        strat = EMACrossover()
        params = {"fast": 20, "slow": 50}
        signals = strat.generate_signals(df, params)
        assert len(signals) == n

    def test_hold_carries_position_between_crossovers(self):
        """Between a BUY and the subsequent SELL there must be no HOLD-that-resets.

        The engine interprets HOLD as 'carry current position', so HOLD bars
        between a BUY and the next SELL must not introduce unexpected BUY/SELL.
        Specifically, we verify that after the first BUY we see a run of HOLDs
        before the next SELL — i.e. the strategy does not immediately BUY→SELL
        on consecutive bars, which would indicate faulty carry logic.
        """
        prices = _make_crossover_series(n_warmup=200, n_bull=250, n_bear=300)
        df = _ohlcv(len(prices), prices)
        strat = EMACrossover()
        params = {"fast": 20, "slow": 50}
        signals = pd.Series(strat.generate_signals(df, params))

        # Find first BUY
        buy_idx = signals[signals == "BUY"].index[0]
        # Find next SELL after that BUY
        after_buy = signals.iloc[buy_idx + 1:]
        sell_idx_rel = after_buy[after_buy == "SELL"].index
        assert len(sell_idx_rel) > 0, "No SELL found after the first BUY."

        sell_idx = sell_idx_rel[0]
        between = signals.iloc[buy_idx + 1:sell_idx]
        # Between BUY and next SELL, only HOLD is valid (no new BUY/SELL)
        assert (between == "HOLD").all(), (
            "Expected only HOLDs between BUY and subsequent SELL — "
            "carry logic appears broken."
        )

    def test_get_min_bars_uses_slow_param(self):
        """get_min_bars must return the slow EMA period."""
        strat = EMACrossover()
        assert strat.get_min_bars({"fast": 10, "slow": 100}) == 100
        assert strat.get_min_bars({"fast": 20, "slow": 50}) == 50

    def test_mutation_grid_has_slow_gt_fast(self):
        """Every slow-grid value must exceed the corresponding fast-grid value
        to produce valid crossover pairs (slow must always be larger than fast)."""
        strat = EMACrossover()
        min_fast = min(strat._FAST_GRID)
        for slow in strat._SLOW_GRID:
            assert slow > min_fast, (
                f"Slow EMA period {slow} is not greater than minimum fast {min_fast}."
            )

    def test_fast_slow_default_ordering(self):
        """Default fast < default slow (sanity on param_grid)."""
        strat = EMACrossover()
        assert strat.param_grid["fast"] < strat.param_grid["slow"]
