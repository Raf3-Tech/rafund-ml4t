"""Regression: z-score threshold fork between signals path and backtest engine.

AGENTS.md Rule 1 requires a single authority for every numerical concern.
The canonical threshold values live in config.constants and are loaded at
runtime via config.loader.get_settings().  This suite fails if any module
re-introduces a divergent hardcoded literal.

Three guards:
  1. Constant consistency  — all module-level defaults equal STAT_ARB_ENTRY_Z /
                             STAT_ARB_EXIT_Z from config.constants.
  2. Threshold sensitivity — EvaluationBacktestEngine wires the argument
                             through; a different threshold produces different
                             signals (catches future internal hardcoding).
  3. Path parity           — at a non-default threshold (1.5 / 0.3) both the
                             engine path and the StatArbStrategy core produce
                             identical signal series (catches per-path drift).
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from backtesting.engine_eval import EvaluationBacktestEngine, prepare_pair_prices
from config.constants import STAT_ARB_ENTRY_Z, STAT_ARB_EXIT_Z
from strategies.stat_arb import StatArbPairsStrategy, StatArbStrategy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _synthetic_prices(n: int = 300, seed: int = 7) -> pd.DataFrame:
    """Long-form prices DataFrame with two symbols (for EvaluationBacktestEngine)."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    a = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    b = a * np.exp(rng.normal(0, 0.05, n))
    df_a = pd.DataFrame({"timestamp": ts, "close": a, "symbol": "A/USDT"})
    df_b = pd.DataFrame({"timestamp": ts, "close": b, "symbol": "B/USDT"})
    return pd.concat([df_a, df_b], ignore_index=True)


def _da_db(n: int = 300, seed: int = 7):
    """Separate per-symbol DataFrames for the StatArbStrategy path."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    a = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    b = a * np.exp(rng.normal(0, 0.05, n))
    da = pd.DataFrame({"timestamp": ts, "close": a})
    db = pd.DataFrame({"timestamp": ts, "close": b})
    return da, db


def _engine_signals(prices, entry, exit_thr, lookback=60):
    eng = EvaluationBacktestEngine(
        symbol_a="A/USDT",
        symbol_b="B/USDT",
        entry_threshold=entry,
        exit_threshold=exit_thr,
        lookback=lookback,
    )
    pair_df = prepare_pair_prices(prices, "A/USDT", "B/USDT")
    return eng.generate_pair_signals(pair_df)["signal"].tolist()


def _cmd_signals(da, db, entry, exit_thr, lookback=60):
    merged = (
        pd.merge(
            da.rename(columns={"close": "close_a"}),
            db.rename(columns={"close": "close_b"}),
            on="timestamp",
            how="inner",
        )
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    core = StatArbStrategy(
        entry_threshold=entry,
        exit_threshold=exit_thr,
        use_fixed_window=True,
        fixed_window_size=lookback,
    )
    return core.signals_from_pair_prices(merged)["signal"].tolist()


# ---------------------------------------------------------------------------
# Guard 1 — constant consistency
# ---------------------------------------------------------------------------


def test_stat_arb_pairs_strategy_param_grid_matches_constants():
    """StatArbPairsStrategy.param_grid must equal the canonical constants.
    If someone changes the param_grid literals without updating constants, this fails."""
    assert StatArbPairsStrategy.param_grid["entry_z"] == STAT_ARB_ENTRY_Z, (
        f"param_grid entry_z={StatArbPairsStrategy.param_grid['entry_z']} "
        f"!= STAT_ARB_ENTRY_Z={STAT_ARB_ENTRY_Z}"
    )
    assert StatArbPairsStrategy.param_grid["exit_z"] == STAT_ARB_EXIT_Z, (
        f"param_grid exit_z={StatArbPairsStrategy.param_grid['exit_z']} "
        f"!= STAT_ARB_EXIT_Z={STAT_ARB_EXIT_Z}"
    )


def test_evaluation_engine_default_matches_constants():
    """EvaluationBacktestEngine constructor defaults must equal the canonical constants."""
    sig = inspect.signature(EvaluationBacktestEngine.__init__)
    assert sig.parameters["entry_threshold"].default == STAT_ARB_ENTRY_Z, (
        f"EvaluationBacktestEngine default entry_threshold="
        f"{sig.parameters['entry_threshold'].default} != STAT_ARB_ENTRY_Z={STAT_ARB_ENTRY_Z}"
    )
    assert sig.parameters["exit_threshold"].default == STAT_ARB_EXIT_Z, (
        f"EvaluationBacktestEngine default exit_threshold="
        f"{sig.parameters['exit_threshold'].default} != STAT_ARB_EXIT_Z={STAT_ARB_EXIT_Z}"
    )


# ---------------------------------------------------------------------------
# Guard 2 — threshold sensitivity
# ---------------------------------------------------------------------------


def test_engine_threshold_is_not_hardcoded():
    """Passing a non-default threshold to EvaluationBacktestEngine must change
    the signal series. If the engine ignores its argument and uses a hardcoded
    value internally, signals for 2.0 and 1.5 would be identical — test fails."""
    prices = _synthetic_prices()
    sigs_default = _engine_signals(
        prices, entry=STAT_ARB_ENTRY_Z, exit_thr=STAT_ARB_EXIT_Z
    )
    sigs_tight = _engine_signals(prices, entry=1.5, exit_thr=0.3)
    assert sigs_default != sigs_tight, (
        "entry_threshold=1.5 produced the same signals as entry_threshold=2.0 — "
        "threshold is not being wired through EvaluationBacktestEngine."
    )


# ---------------------------------------------------------------------------
# Guard 3 — path parity at non-default threshold
# ---------------------------------------------------------------------------


def test_engine_and_signals_path_agree_at_nondefault_threshold():
    """At a non-default threshold (1.5/0.3) the engine path and the
    StatArbStrategy signals_from_pair_prices path must produce identical signal
    series.  If either path hardcodes the default 2.0 instead of wiring through
    its argument, the two series diverge and this test fails."""
    ENTRY, EXIT, LOOKBACK = 1.5, 0.3, 60
    prices = _synthetic_prices()
    da, db = _da_db()

    e_sigs = _engine_signals(prices, entry=ENTRY, exit_thr=EXIT, lookback=LOOKBACK)
    c_sigs = _cmd_signals(da, db, entry=ENTRY, exit_thr=EXIT, lookback=LOOKBACK)

    assert e_sigs == c_sigs, (
        f"Engine path and signals-command path diverge at entry_threshold={ENTRY}: "
        f"first difference at index "
        f"{next(i for i, (a, b) in enumerate(zip(e_sigs, c_sigs)) if a != b)}"
    )
