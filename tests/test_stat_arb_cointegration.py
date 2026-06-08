"""Causal (train-fold) cointegration gate for WalkForwardStatArb.

A pair must be stationary on the TRAINING window to trade out-of-sample. This
removes full-sample selection look-ahead: the trade/no-trade decision uses only
in-sample information.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.stat_arb import WalkForwardStatArb


def _frame(price_a, price_b):
    ts = pd.date_range("2022-01-01", periods=len(price_a), freq="D", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "price_a": price_a, "price_b": price_b})


def _cointegrated_pair(n=250, seed=0):
    rng = np.random.default_rng(seed)
    base = 100.0 + np.cumsum(rng.normal(0, 0.5, n))         # random-walk anchor
    noise = rng.normal(0, 1.0, n)                            # stationary spread
    price_b = base
    price_a = base + noise                                   # A tracks B + mean-reverting gap
    return _frame(price_a, price_b)


def _non_cointegrated_pair(n=250, seed=1):
    rng = np.random.default_rng(seed)
    price_a = 100.0 + np.cumsum(rng.normal(0, 0.5, n))       # independent random walks
    price_b = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    return _frame(price_a, price_b)


def test_non_cointegrated_pair_stays_flat_when_gate_enabled():
    df = _non_cointegrated_pair()
    train, test = df.iloc[:150], df.iloc[150:]
    strat = WalkForwardStatArb(require_cointegration=True)
    strat.prepare(train)

    assert strat.is_cointegrated is False
    out = strat.generate_signals(test)
    assert (out["position_a"] == 0).all()
    assert (out["position_b"] == 0).all()


def test_cointegrated_pair_can_trade_when_gate_enabled():
    df = _cointegrated_pair()
    train, test = df.iloc[:150], df.iloc[150:]
    strat = WalkForwardStatArb(entry_threshold=1.0, exit_threshold=0.3, require_cointegration=True)
    strat.prepare(train)

    assert strat.is_cointegrated is True
    out = strat.generate_signals(test)
    # A stationary spread should breach the +/-1 sigma band somewhere OOS.
    assert (out["position_a"] != 0).any()


def test_gate_disabled_by_default_trades_regardless():
    df = _non_cointegrated_pair()
    train, test = df.iloc[:150], df.iloc[150:]
    strat = WalkForwardStatArb(entry_threshold=1.0, exit_threshold=0.3)  # default: gate off
    strat.prepare(train)
    out = strat.generate_signals(test)
    # No cointegration filtering -> the strategy may take positions on the band.
    assert "position_a" in out.columns and len(out) == len(test)
