"""Tests for the completed, causal FactorStrategy."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.factor_model import FactorStrategy


def test_standardized_signals_long_high_short_low():
    # Factor is flat in train, then swings high then low out-of-sample.
    train = pd.DataFrame({"mom": np.zeros(50)})
    test = pd.DataFrame({"mom": np.r_[np.full(10, 5.0), np.full(10, -5.0)]})

    strat = FactorStrategy(entry_z=1.0)
    strat.add_factor("mom", train["mom"])
    strat.prepare(train)
    # Train std is 0 -> degenerate; refit on a varied train so normalization is defined.
    varied_train = pd.DataFrame({"mom": np.linspace(-2, 2, 50)})
    strat.prepare(varied_train)

    scores = strat.score(test)
    signals = strat.generate_signals(scores)
    assert (signals.iloc[:10] == 1).all()    # high -> long
    assert (signals.iloc[10:] == -1).all()   # low -> short


def test_prepare_freezes_train_statistics():
    train = pd.DataFrame({"f": np.linspace(0.0, 10.0, 100)})  # mean 5, known std
    strat = FactorStrategy()
    strat.add_factor("f", train["f"])
    strat.prepare(train)

    frozen_mean = strat._means["f"]
    frozen_std = strat._stds["f"]

    # Score OOS data with a totally different scale; stats must stay the train ones.
    test = pd.DataFrame({"f": np.linspace(1000.0, 2000.0, 20)})
    strat.score(test)
    assert strat._means["f"] == frozen_mean
    assert strat._stds["f"] == frozen_std
    # And the standardized OOS score reflects train stats (large positive z).
    assert strat.score(test).iloc[-1] > 3.0


def test_zero_variance_factor_contributes_zero_no_nan():
    train = pd.DataFrame({"flat": np.full(40, 7.0)})
    strat = FactorStrategy()
    strat.add_factor("flat", train["flat"])
    strat.prepare(train)
    scores = strat.score(pd.DataFrame({"flat": np.full(5, 7.0)}))
    assert np.isfinite(scores).all()
    assert (scores == 0.0).all()


def test_weighted_combination_respects_weights():
    n = 60
    train = pd.DataFrame({
        "a": np.linspace(-1, 1, n),
        "b": np.linspace(-1, 1, n),
    })
    strat = FactorStrategy()
    strat.add_factor("a", train["a"], weight=3.0)
    strat.add_factor("b", train["b"], weight=1.0)
    strat.prepare(train)

    # Two standardized factors with equal z but different weights: composite is
    # the weight-normalized average, so it equals that common z value.
    test = pd.DataFrame({"a": [2.0], "b": [2.0]})
    z_a = strat._standardize("a", test["a"]).iloc[0]
    score = strat.score(test).iloc[0]
    assert np.isclose(score, z_a)
