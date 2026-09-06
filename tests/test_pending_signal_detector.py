"""Tests for signals/pending_signal_detector.py.

_build_payload is a pure function of PositionState + a settings-like object
(same philosophy as tests/test_risk_sizing_and_alerts.py for _target_notional) —
no DB needed. _qualifying_single_symbol_candidates and scan_kraken_signals are
covered with a mock DB / patched leaderboard, mirroring tests/test_leaderboard.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from signals.pending_signal_detector import (
    _build_payload,
    _qualifying_single_symbol_candidates,
    scan_kraken_signals,
)
from trading.position import PositionState


def _cfg(**overrides):
    base = dict(
        account_size=5000.0, max_drawdown_pct=0.06, max_daily_loss_pct=0.04,
        max_adverse_move_pct=0.40, risk_buffer_pct=0.7, leg_allocation_pct=0.18,
        risk_per_trade_pct=0.99,  # high so headroom caps, not risk cap, drive these tests
        pending_signal_default_stop_pct=0.02, pending_signal_r_multiple=2.0,
        pending_signal_expiry_minutes=30,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _pos(equity=5000.0):
    return PositionState(
        run_id="live_kraken", strategy_name="Test", symbol="BTC/USD",
        equity=equity, peak_equity=equity, daily_start_equity=equity,
    )


def test_build_payload_long_with_structural_stop_uses_r_multiple_take_profit():
    sig = _build_payload(
        strategy_name="SMC Breakout", symbol="BTC/USD", exchange="kraken", rank=1,
        score=0.5, signal="BUY", price=100.0, stop_price=95.0,
        source_timeframe="1d", pos=_pos(), cfg=_cfg(),
    )
    assert sig.direction == "LONG"
    assert sig.stop_price == pytest.approx(95.0)
    # take_profit = price + r_multiple * stop_distance = 100 + 2*5 = 110
    assert sig.take_profit_price == pytest.approx(110.0)
    assert "structural stop" in sig.thesis


def test_build_payload_short_flips_stop_and_take_profit_direction():
    sig = _build_payload(
        strategy_name="SMC Breakout", symbol="BTC/USD", exchange="kraken", rank=2,
        score=0.3, signal="SELL", price=100.0, stop_price=105.0,
        source_timeframe="1d", pos=_pos(), cfg=_cfg(),
    )
    assert sig.direction == "SHORT"
    # take_profit = price - r_multiple * stop_distance = 100 - 2*5 = 90
    assert sig.take_profit_price == pytest.approx(90.0)


def test_build_payload_falls_back_to_default_stop_pct_when_strategy_has_none():
    sig = _build_payload(
        strategy_name="EMA Crossover", symbol="BTC/USD", exchange="kraken", rank=3,
        score=0.2, signal="BUY", price=100.0, stop_price=None,
        source_timeframe="1d", pos=_pos(), cfg=_cfg(pending_signal_default_stop_pct=0.02),
    )
    assert sig.stop_price == pytest.approx(98.0)  # 100 * (1 - 0.02)
    assert sig.take_profit_price == pytest.approx(104.0)  # 100 + 2*2
    assert "fallback" in sig.thesis


def test_build_payload_sizes_via_target_notional_not_raw_leg_allocation():
    cfg = _cfg(leg_allocation_pct=0.18)
    sig = _build_payload(
        strategy_name="EMA Crossover", symbol="BTC/USD", exchange="kraken", rank=1,
        score=0.5, signal="BUY", price=100.0, stop_price=98.0,
        source_timeframe="1d", pos=_pos(), cfg=cfg,
    )
    # 5000 * 0.18 = 900 raw leg allocation; the dual-cap sizing must not just
    # pass that through unmodified (would defeat the point of reusing it).
    assert sig.position_size_usd is not None
    assert sig.qty == pytest.approx(sig.position_size_usd / 100.0)


def _leaderboard_row(strategy, symbol, qualifies, score=0.5, params=None):
    return {
        "strategy_name": strategy, "symbol": symbol, "params": params or {},
        "qualifies": qualifies, "score": score,
    }


def test_qualifying_single_symbol_candidates_excludes_pairs_and_non_qualifying():
    lb = pd.DataFrame([
        _leaderboard_row("Statistical Arbitrage", "BTC/USD|BTC/USDT", True, score=0.9),
        _leaderboard_row("EMA Crossover", "BTC/USD", True, score=0.4),
        _leaderboard_row("ATR Volatility Breakout", "ETH/USD", False, score=0.0),
    ])
    lb.index = [1, 2, 3]

    with patch("monitoring.leaderboard.build_leaderboard", return_value=lb):
        candidates = _qualifying_single_symbol_candidates(db=MagicMock())

    names = [(c[1], c[2]) for c in candidates]
    assert ("EMA Crossover", "BTC/USD") in names
    assert not any(name == "Statistical Arbitrage" for name, _ in names)  # pairs excluded
    assert not any(sym == "ETH/USD" for _, sym in names)  # non-qualifying excluded


def test_scan_kraken_signals_suppressed_when_account_failed():
    halted_pos = PositionState(
        run_id="live_kraken", strategy_name="", symbol="", exchange="kraken",
        account_failed=True,
    )
    with patch("signals.pending_signal_detector.load_position", return_value=halted_pos), \
         patch("signals.pending_signal_detector._qualifying_single_symbol_candidates") as mock_candidates:
        result = scan_kraken_signals(db=MagicMock(), exchange="kraken")

    assert result == []
    mock_candidates.assert_not_called()  # suppressed before even querying the leaderboard
