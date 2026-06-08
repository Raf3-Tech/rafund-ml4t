"""Tests for triple-barrier labeling (Phase 2, alpha roadmap)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from labels.triple_barrier import (
    BarrierConfig,
    drop_unusable_labels,
    ewma_volatility,
    triple_barrier_labels,
)


def _const_vol(n, v=0.01):
    return pd.Series([v] * n, dtype="float64")


def test_take_profit_hit_labels_plus_one():
    # Flat then a +3% jump; TP barrier = 2*1% = 2%.
    px = pd.Series([100, 100, 103, 103, 103], dtype="float64")
    cfg = BarrierConfig(tp_mult=2, sl_mult=2, horizon=4, vol_span=2)
    out = triple_barrier_labels(px, cfg, vol=_const_vol(len(px)))
    assert out["label"].iloc[0] == 1
    assert out["barrier"].iloc[0] == "tp"
    assert out["exit_offset"].iloc[0] == 2


def test_stop_loss_hit_labels_minus_one():
    px = pd.Series([100, 100, 97, 97, 97], dtype="float64")
    cfg = BarrierConfig(tp_mult=2, sl_mult=2, horizon=4, vol_span=2)
    out = triple_barrier_labels(px, cfg, vol=_const_vol(len(px)))
    assert out["label"].iloc[0] == -1
    assert out["barrier"].iloc[0] == "sl"


def test_timeout_labels_zero():
    # Small wiggles within +/-2% never touch a barrier -> time barrier.
    px = pd.Series([100, 100.5, 100.2, 100.8, 100.3], dtype="float64")
    cfg = BarrierConfig(tp_mult=2, sl_mult=2, horizon=4, vol_span=2)
    out = triple_barrier_labels(px, cfg, vol=_const_vol(len(px)))
    assert out["label"].iloc[0] == 0
    assert out["barrier"].iloc[0] == "time"


def test_path_dependence_first_touch_wins():
    # Rises to +2% at t=1 (TP) BEFORE dropping to -3% at t=2: TP must win.
    px = pd.Series([100, 102, 97, 97], dtype="float64")
    cfg = BarrierConfig(tp_mult=2, sl_mult=2, horizon=3, vol_span=2)
    out = triple_barrier_labels(px, cfg, vol=_const_vol(len(px)))
    assert out["label"].iloc[0] == 1
    assert out["exit_offset"].iloc[0] == 1


def test_truncated_flag_on_tail_rows():
    px = pd.Series(np.linspace(100, 110, 10), dtype="float64")
    cfg = BarrierConfig(tp_mult=2, sl_mult=2, horizon=3, vol_span=2)
    out = triple_barrier_labels(px, cfg, vol=_const_vol(len(px)))
    # Rows whose t+horizon exceeds the last index (7,8,9) are truncated.
    assert out["truncated"].iloc[7:].all()
    assert not out["truncated"].iloc[:7].any()


def test_warmup_rows_nan_when_vol_unavailable():
    rng = np.random.default_rng(0)
    px = pd.Series(100 + rng.normal(0, 1, 60).cumsum())
    cfg = BarrierConfig(vol_span=20, horizon=5)
    out = triple_barrier_labels(px, cfg)  # uses real EWMA vol
    # First (vol_span-1) rows have no volatility estimate -> NaN label.
    assert out["label"].iloc[:19].isna().all()


def test_ewma_volatility_is_causal():
    rng = np.random.default_rng(1)
    base = 100 + rng.normal(0, 1, 200).cumsum()
    short = pd.Series(base)
    long = pd.Series(np.concatenate([base, 100 + rng.normal(0, 1, 50).cumsum()]))
    v_short = ewma_volatility(short, span=20)
    v_long = ewma_volatility(long, span=20)
    np.testing.assert_allclose(
        v_short.to_numpy(), v_long.iloc[: len(short)].to_numpy(), equal_nan=True
    )


def test_drop_unusable_removes_warmup_and_truncated():
    rng = np.random.default_rng(2)
    px = pd.Series(100 + rng.normal(0, 1, 80).cumsum())
    cfg = BarrierConfig(vol_span=20, horizon=5)
    out = triple_barrier_labels(px, cfg)
    clean = drop_unusable_labels(out)
    assert clean["label"].notna().all()
    assert not clean["truncated"].any()
    assert len(clean) < len(out)


def test_labels_are_in_expected_set():
    rng = np.random.default_rng(3)
    px = pd.Series(100 + rng.normal(0, 2, 300).cumsum()).abs() + 10
    out = triple_barrier_labels(px, BarrierConfig(vol_span=20, horizon=10))
    vals = set(out["label"].dropna().unique())
    assert vals.issubset({-1.0, 0.0, 1.0})


def test_invalid_config_raises():
    with pytest.raises(ValueError):
        BarrierConfig(tp_mult=0).validate()
    with pytest.raises(ValueError):
        BarrierConfig(horizon=0).validate()
