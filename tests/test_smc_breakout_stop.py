"""Tests for SMCBreakout.get_stop_level — the structural stop (order block
edge) used by trading/paper_trader.py, separate from the already-tested
generate_signals contract in tests/test_strategies_contract.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.smc_breakout import SMCBreakout


def _ohlcv(n: int = 900, seed: int = 0) -> pd.DataFrame:
    """Unlike tests/test_strategies_contract.py's fixture (open==close, which
    makes the engulfing-bar condition structurally unsatisfiable), this gives
    open a small independent jitter off the prior close so real bullish/
    bearish engulfing bars — and therefore real BUY/SELL signals — actually
    occur, which this test needs to exercise get_stop_level at all."""
    rng = np.random.default_rng(seed)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, n))))
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.006, n))
    high = pd.concat([open_, close], axis=1).max(axis=1) * 1.004
    low = pd.concat([open_, close], axis=1).min(axis=1) * 0.996
    return pd.DataFrame({
        "timestamp": pd.date_range("2017-08-18", periods=n, freq="D", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close, "volume": 1e6,
    })


def _params(strategy):
    return {k: (v[0] if isinstance(v, list) else v) for k, v in strategy.param_grid.items()}


def test_returns_none_below_min_bars():
    strategy = SMCBreakout()
    params = _params(strategy)
    df = _ohlcv(n=strategy.get_min_bars(params) - 1)
    assert strategy.get_stop_level(df, params) is None


@pytest.mark.parametrize("seed", [0, 2, 4, 5, 7])  # seeds confirmed to fire BUY/SELL
def test_stop_level_sits_on_the_correct_side_of_every_entry(seed):
    """Whenever generate_signals fires a BUY/SELL, the stop computed from
    data truncated to that same bar must sit below (BUY) or above (SELL)
    the entry price — the order block's near edge is always outside the
    entry, never past it."""
    strategy = SMCBreakout()
    params = _params(strategy)
    df = _ohlcv(n=900, seed=seed)
    signals = strategy.generate_signals(df, params)

    checked_any = False
    for i, sig in enumerate(signals):
        if sig not in ("BUY", "SELL"):
            continue
        stop = strategy.get_stop_level(df.iloc[: i + 1], params)
        assert stop is not None, f"bar {i}: {sig} fired but no active range/stop"
        price = float(df["close"].iloc[i])
        if sig == "BUY":
            assert stop < price, f"bar {i}: BUY stop {stop} not below price {price}"
        else:
            assert stop > price, f"bar {i}: SELL stop {stop} not above price {price}"
        checked_any = True

    assert checked_any, f"seed {seed}: no BUY/SELL fired — test isn't exercising anything"


def test_stop_level_is_deterministic():
    strategy = SMCBreakout()
    params = _params(strategy)
    df = _ohlcv(n=300, seed=0)
    assert strategy.get_stop_level(df, params) == strategy.get_stop_level(df, params)
