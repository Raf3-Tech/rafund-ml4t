"""Contract tests for the single-asset strategy library (spec Phase 2 / step 3).

Every strategy must: return a Series of only BUY/SELL/HOLD, aligned 1:1 to the
input frame, with the first ``get_min_bars(params)`` entries all HOLD (warmup).
"""

import numpy as np
import pandas as pd
import pytest

from strategies.ema_crossover import EMACrossover
from strategies.macd import MACDStrategy
from strategies.supertrend import SupertrendStrategy
from strategies.donchian_breakout import DonchianBreakout
from strategies.bollinger_reversion import BollingerReversion
from strategies.rsi_extremes import RSIExtremes
from strategies.atr_volatility_breakout import ATRVolatilityBreakout
from strategies.keltner_squeeze import KeltnerSqueeze
from strategies.dca import DCAStrategy
from strategies.hodl_rebalance import HODLRebalance

STRATEGIES = [
    EMACrossover, MACDStrategy, SupertrendStrategy, DonchianBreakout,
    BollingerReversion, RSIExtremes, ATRVolatilityBreakout, KeltnerSqueeze,
    DCAStrategy, HODLRebalance,
]


def _ohlcv(n: int = 900, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, n))))
    return pd.DataFrame({
        "timestamp": pd.date_range("2017-08-18", periods=n, freq="D", tz="UTC"),
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": 1e6,
    })


def _default_params(strategy):
    return {k: (v[0] if isinstance(v, list) else v) for k, v in strategy.param_grid.items()}


@pytest.mark.parametrize("cls", STRATEGIES, ids=[c.__name__ for c in STRATEGIES])
def test_strategy_contract(cls):
    strategy = cls()
    df = _ohlcv()
    params = _default_params(strategy)
    sig = strategy.generate_signals(df, params)

    assert len(sig) == len(df), f"{strategy.name}: length mismatch"
    assert set(pd.Series(sig).unique()) <= {"BUY", "SELL", "HOLD"}, f"{strategy.name}: invalid label"

    min_bars = strategy.get_min_bars(params)
    warmup = pd.Series(sig).iloc[:min_bars]
    assert (warmup == "HOLD").all(), f"{strategy.name}: first {min_bars} bars must be HOLD"


@pytest.mark.parametrize("cls", STRATEGIES, ids=[c.__name__ for c in STRATEGIES])
def test_strategy_is_deterministic(cls):
    strategy = cls()
    df = _ohlcv()
    params = _default_params(strategy)
    s1 = pd.Series(strategy.generate_signals(df, params)).reset_index(drop=True)
    s2 = pd.Series(strategy.generate_signals(df, params)).reset_index(drop=True)
    assert s1.equals(s2), f"{strategy.name}: non-deterministic output"
