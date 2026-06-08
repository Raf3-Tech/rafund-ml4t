"""Phase-1 regression: the signals command and the backtest must share one
signal authority, so their BUY/SELL/HOLD distributions are identical.

Both ``StatArbPairsStrategy.signals_to_db_format`` (the ``python main.py signals``
path) and ``StatArbStrategy.signals_from_pair_prices`` + ``to_db_signals`` (the
backtest persistence path) delegate to the same stateful core. This test pins
that they cannot drift apart again.
"""

import numpy as np
import pandas as pd

from strategies.stat_arb import StatArbPairsStrategy, StatArbStrategy


def _synthetic_pair(n: int = 600, seed: int = 3):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    a = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    b = a * np.exp(rng.normal(0, 0.05, n))
    da = pd.DataFrame({"timestamp": ts, "close": a})
    db = pd.DataFrame({"timestamp": ts, "close": b})
    return da, db


def test_signals_command_matches_backtest_distribution():
    da, db = _synthetic_pair()
    params = {"entry_z": 2.0, "exit_z": 0.5, "lookback": 60}

    # signals-command path
    cmd = StatArbPairsStrategy().signals_to_db_format(da, db, params, "A/USDT", "B/USDT")
    dist_cmd = cmd["signal"].value_counts().to_dict()

    # backtest persistence path
    core = StatArbStrategy(
        entry_threshold=2.0, exit_threshold=0.5,
        use_fixed_window=True, fixed_window_size=60,
    )
    merged = pd.merge(
        da.rename(columns={"close": "close_a"}),
        db.rename(columns={"close": "close_b"}),
        on="timestamp", how="inner",
    ).sort_values("timestamp").reset_index(drop=True)
    frame = core.signals_from_pair_prices(merged)
    dist_bt = StatArbStrategy.to_db_signals(frame, "A/USDT", "B/USDT")["signal"].value_counts().to_dict()

    assert dist_cmd == dist_bt


def test_db_signals_are_stateful_not_per_bar():
    """A stateful path holds a position between entry and exit, so the BUY/SELL
    count must equal the number of entry events, not every bar over threshold."""
    da, db = _synthetic_pair()
    params = {"entry_z": 2.0, "exit_z": 0.5, "lookback": 60}
    pairs = StatArbPairsStrategy()

    rows = pairs.signals_to_db_format(da, db, params, "A/USDT", "B/USDT")
    pos = pairs.generate_signals_pair(da, db, params)

    # position_a: +1 long (BUY), -1 short (SELL), 0 flat (HOLD) — must agree.
    assert (rows["signal"] == "BUY").sum() == (pos["position_a"] == 1).sum()
    assert (rows["signal"] == "SELL").sum() == (pos["position_a"] == -1).sum()
    assert (rows["signal"] == "HOLD").sum() == (pos["position_a"] == 0).sum()


def test_signal_values_are_valid_enum():
    da, db = _synthetic_pair()
    params = {"entry_z": 2.0, "exit_z": 0.5, "lookback": 60}
    rows = StatArbPairsStrategy().signals_to_db_format(da, db, params, "A/USDT", "B/USDT")
    assert set(rows["signal"].unique()) <= {"BUY", "SELL", "HOLD"}
