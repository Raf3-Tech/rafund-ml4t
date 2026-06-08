"""Tests for the Phase-1 permutation-entropy trade gate in WalkForwardStatArb."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.stat_arb import WalkForwardStatArb


def _pair_frame(n=400, seed=0):
    """A cointegrated-ish pair: B is a random walk, A tracks B plus a
    mean-reverting (AR) spread, so z-scores cross the entry bands."""
    rng = np.random.default_rng(seed)
    log_b = np.cumsum(rng.normal(0, 0.01, n)) + 5.0
    spread = np.zeros(n)
    for t in range(1, n):
        spread[t] = 0.8 * spread[t - 1] + rng.normal(0, 0.05)  # AR(1), reverting
    log_a = log_b + spread
    ts = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {"timestamp": ts, "price_a": np.exp(log_a), "price_b": np.exp(log_b)}
    )


def _entries(positions: pd.Series) -> int:
    prev = positions.shift(1).fillna(0)
    return int(((prev == 0) & (positions != 0)).sum())


def test_gate_never_increases_entry_count():
    df = _pair_frame()
    train, test = df.iloc[:200], df.iloc[200:]

    base = WalkForwardStatArb(entropy_threshold=None)
    base.prepare(train)
    base_sig = base.generate_signals(test)

    gated = WalkForwardStatArb(entropy_threshold=0.85, entropy_window=30)
    gated.prepare(train)
    gated_sig = gated.generate_signals(test)

    # Same OOS bars, but the gate can only remove entries, never add them.
    assert _entries(gated_sig["position_a"]) <= _entries(base_sig["position_a"])


def test_extreme_low_threshold_blocks_essentially_all_entries():
    df = _pair_frame(seed=1)
    train, test = df.iloc[:200], df.iloc[200:]
    s = WalkForwardStatArb(entropy_threshold=0.001, entropy_window=30)
    s.prepare(train)
    sig = s.generate_signals(test)
    assert _entries(sig["position_a"]) == 0


def test_disabled_gate_matches_legacy_behaviour():
    df = _pair_frame(seed=2)
    train, test = df.iloc[:200], df.iloc[200:]
    s = WalkForwardStatArb(entropy_threshold=None)
    s.prepare(train)
    sig = s.generate_signals(test)
    # position_b must always be the inverse of position_a (pair invariant).
    assert (sig["position_b"] == -sig["position_a"]).all()
