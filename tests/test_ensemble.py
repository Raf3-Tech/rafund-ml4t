"""Unit tests for the majority-vote ensemble signal layer.

Tests the monitoring.ensemble module in isolation — no DB, no engine.
"""

from __future__ import annotations

import pandas as pd
import pytest

from monitoring.ensemble import build_ensemble_signal, ensemble_agreement_rate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lb(rows: list[dict]) -> pd.DataFrame:
    """Minimal leaderboard DataFrame factory."""
    defaults = {
        "score_conservative": 1.0,
        "score_standard": 0.8,
        "score_permissive": 0.6,
        "avg_sharpe": 1.0,
        "qualifies_conservative": True,
        "qualifies_standard": True,
        "qualifies_permissive": True,
        "best_tier": "CONSERVATIVE",
        "symbol": "BTC/USDT",
    }
    records = []
    for r in rows:
        row = {**defaults, **r}
        records.append(row)
    return pd.DataFrame(records)


def _sig(values: list[str], start: int = 0) -> pd.Series:
    return pd.Series(values, index=range(start, start + len(values)))


# ---------------------------------------------------------------------------
# build_ensemble_signal
# ---------------------------------------------------------------------------

class TestBuildEnsembleSignal:

    def test_unanimous_buy_returns_buy(self):
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0},
            {"strategy_name": "B", "score_conservative": 1.5},
            {"strategy_name": "C", "score_conservative": 1.0},
        ])
        signals = {
            "A": _sig(["BUY", "BUY", "BUY"]),
            "B": _sig(["BUY", "BUY", "BUY"]),
            "C": _sig(["BUY", "BUY", "BUY"]),
        }
        result = build_ensemble_signal(lb, signals, top_n=3)
        assert list(result) == ["BUY", "BUY", "BUY"]

    def test_unanimous_sell_returns_sell(self):
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0},
            {"strategy_name": "B", "score_conservative": 1.5},
        ])
        signals = {
            "A": _sig(["SELL", "SELL"]),
            "B": _sig(["SELL", "SELL"]),
        }
        result = build_ensemble_signal(lb, signals, top_n=2)
        assert list(result) == ["SELL", "SELL"]

    def test_majority_buy_wins(self):
        """2 BUY + 1 SELL = net +1 → BUY."""
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 3.0},
            {"strategy_name": "B", "score_conservative": 2.0},
            {"strategy_name": "C", "score_conservative": 1.0},
        ])
        signals = {
            "A": _sig(["BUY"]),
            "B": _sig(["BUY"]),
            "C": _sig(["SELL"]),
        }
        result = build_ensemble_signal(lb, signals, top_n=3)
        assert result.iloc[0] == "BUY"

    def test_tie_resolves_to_hold(self):
        """1 BUY + 1 SELL = net 0 → HOLD."""
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0},
            {"strategy_name": "B", "score_conservative": 1.0},
        ])
        signals = {
            "A": _sig(["BUY"]),
            "B": _sig(["SELL"]),
        }
        result = build_ensemble_signal(lb, signals, top_n=2)
        assert result.iloc[0] == "HOLD"

    def test_missing_strategy_skipped(self):
        """Strategies in leaderboard but not in signals dict are silently skipped."""
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0},
            {"strategy_name": "MISSING", "score_conservative": 1.0},
        ])
        signals = {"A": _sig(["BUY", "BUY"])}
        result = build_ensemble_signal(lb, signals, top_n=2)
        # Only A participates; BUY wins unanimously
        assert list(result) == ["BUY", "BUY"]

    def test_empty_leaderboard_returns_hold(self):
        signals = {"A": _sig(["BUY", "SELL"])}
        result = build_ensemble_signal(pd.DataFrame(), signals, top_n=3)
        assert list(result) == ["HOLD", "HOLD"]

    def test_empty_signals_returns_empty(self):
        lb = _lb([{"strategy_name": "A", "score_conservative": 1.0}])
        result = build_ensemble_signal(lb, {}, top_n=1)
        assert len(result) == 0

    def test_top_n_limits_participants(self):
        """Only top_n strategies by score should participate."""
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 3.0},
            {"strategy_name": "B", "score_conservative": 2.0},
            {"strategy_name": "C", "score_conservative": 1.0},
        ])
        # C votes SELL; A and B vote BUY — with top_n=2 only A and B count
        signals = {
            "A": _sig(["BUY"]),
            "B": _sig(["BUY"]),
            "C": _sig(["SELL"]),
        }
        result = build_ensemble_signal(lb, signals, top_n=2)
        assert result.iloc[0] == "BUY"

    def test_tier_filter_conservative(self):
        """Tier filter should restrict to strategies qualifying for that tier."""
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0, "qualifies_conservative": True},
            {"strategy_name": "B", "score_conservative": 0.0, "qualifies_conservative": False},
        ])
        lb["score_conservative"] = [2.0, 0.0]
        signals = {
            "A": _sig(["BUY"]),
            "B": _sig(["SELL"]),
        }
        result = build_ensemble_signal(lb, signals, top_n=3, tier="CONSERVATIVE")
        # Only A qualifies — BUY
        assert result.iloc[0] == "BUY"

    def test_hold_signal_is_zero_vote(self):
        """A HOLD vote should contribute 0, so BUY + HOLD = +1 → BUY."""
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0},
            {"strategy_name": "B", "score_conservative": 1.0},
        ])
        signals = {
            "A": _sig(["BUY"]),
            "B": _sig(["HOLD"]),
        }
        result = build_ensemble_signal(lb, signals, top_n=2)
        assert result.iloc[0] == "BUY"

    def test_output_index_matches_input(self):
        """The composite signal must have the same index as the input signals."""
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        lb = _lb([{"strategy_name": "A", "score_conservative": 1.0}])
        signals = {"A": pd.Series(["BUY", "HOLD", "SELL", "BUY", "HOLD"], index=idx)}
        result = build_ensemble_signal(lb, signals, top_n=1)
        assert list(result.index) == list(idx)

    def test_signal_values_are_valid_labels(self):
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0},
            {"strategy_name": "B", "score_conservative": 1.0},
        ])
        signals = {
            "A": _sig(["BUY", "SELL", "HOLD", "BUY", "SELL"]),
            "B": _sig(["HOLD", "BUY", "SELL", "SELL", "BUY"]),
        }
        result = build_ensemble_signal(lb, signals, top_n=2)
        assert set(result.unique()) <= {"BUY", "SELL", "HOLD"}


# ---------------------------------------------------------------------------
# ensemble_agreement_rate
# ---------------------------------------------------------------------------

class TestEnsembleAgreementRate:

    def test_unanimous_gives_rate_one(self):
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0},
            {"strategy_name": "B", "score_conservative": 1.0},
        ])
        signals = {
            "A": _sig(["BUY", "SELL", "HOLD"]),
            "B": _sig(["BUY", "SELL", "HOLD"]),
        }
        rate = ensemble_agreement_rate(lb, signals, top_n=2)
        assert rate == pytest.approx(1.0)

    def test_no_agreement_gives_rate_zero(self):
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0},
            {"strategy_name": "B", "score_conservative": 1.0},
        ])
        signals = {
            "A": _sig(["BUY", "BUY"]),
            "B": _sig(["SELL", "SELL"]),
        }
        rate = ensemble_agreement_rate(lb, signals, top_n=2)
        assert rate == pytest.approx(0.0)

    def test_partial_agreement(self):
        lb = _lb([
            {"strategy_name": "A", "score_conservative": 2.0},
            {"strategy_name": "B", "score_conservative": 1.0},
        ])
        signals = {
            "A": _sig(["BUY", "SELL"]),  # agree on bar0 (both differ) ...
            "B": _sig(["BUY", "BUY"]),
        }
        # bar0: both BUY -> agree; bar1: A=SELL B=BUY -> disagree
        rate = ensemble_agreement_rate(lb, signals, top_n=2)
        assert rate == pytest.approx(0.5)

    def test_empty_leaderboard_returns_zero(self):
        signals = {"A": _sig(["BUY"])}
        rate = ensemble_agreement_rate(pd.DataFrame(), signals, top_n=2)
        assert rate == 0.0

    def test_single_strategy_returns_zero(self):
        """Need at least 2 strategies to measure agreement."""
        lb = _lb([{"strategy_name": "A", "score_conservative": 1.0}])
        signals = {"A": _sig(["BUY", "SELL"])}
        rate = ensemble_agreement_rate(lb, signals, top_n=1)
        assert rate == 0.0
