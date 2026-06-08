"""Tests for the expanding (anchored) walk-forward mode of TimeSeriesSplitter,
and a guard that the default rolling mode is unchanged. Both modes must keep the
train/test boundary leakage-free."""

from __future__ import annotations

import pandas as pd

from backtesting.splitter import TimeSeriesSplitter


def _frame(n=1200):
    ts = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "value": range(n)})


def test_anchored_mode_pins_train_start_and_expands():
    df = _frame()
    folds = TimeSeriesSplitter(
        train_months=12, test_months=3, step_months=3, anchored=True
    ).split(df)

    assert len(folds) >= 2
    first_train_start = folds[0]["train_start"]
    # Every fold trains from the same anchored start...
    assert all(f["train_start"] == first_train_start for f in folds)
    # ...and the training window strictly expands.
    train_ends = [f["train_end"] for f in folds]
    assert all(b > a for a, b in zip(train_ends, train_ends[1:]))


def test_rolling_mode_slides_train_start_unchanged_default():
    df = _frame()
    folds = TimeSeriesSplitter(
        train_months=12, test_months=3, step_months=3
    ).split(df)

    assert len(folds) >= 2
    starts = [f["train_start"] for f in folds]
    # Default (rolling) advances the train_start — not all equal.
    assert len(set(starts)) > 1


def test_both_modes_have_no_train_test_overlap():
    df = _frame()
    for anchored in (False, True):
        folds = TimeSeriesSplitter(
            train_months=12, test_months=3, step_months=3, anchored=anchored
        ).split(df)
        assert folds
        for f in folds:
            # Test strictly post-dates train.
            assert f["test_start"] > f["train_end"]
        # Non-overlapping test windows.
        test_starts = [f["test_start"] for f in folds]
        test_ends = [f["test_end"] for f in folds]
        for prev_end, nxt_start in zip(test_ends, test_starts[1:]):
            assert nxt_start > prev_end


def test_anchored_respects_min_train_months():
    df = _frame()
    folds = TimeSeriesSplitter(
        train_months=6, test_months=3, step_months=3, min_train_months=6, anchored=True
    ).split(df)
    for f in folds:
        span_months = (
            f["train_end"].to_period("M") - f["train_start"].to_period("M")
        ).n + 1
        assert span_months >= 6
