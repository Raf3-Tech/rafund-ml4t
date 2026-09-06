"""Tests for signals/pending_signal_detector.py.

_build_payload is a pure function of PositionState + a settings-like object
(same philosophy as tests/test_strategy_setup.py for the underlying formulas) — no DB
needed. Sizing/cost/risk fields are cross-checked directly against
strategies.setup + risk.cost_model rather than hand-computed, since they're
several chained Decimal operations deep; those modules' own tests already
pin the formulas themselves. _qualifying_single_symbol_candidates and
scan_kraken_signals are covered with a mock DB / patched leaderboard,
mirroring tests/test_leaderboard.py.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from signals.pending_signal_detector import (
    _build_payload,
    _qualifying_single_symbol_candidates,
    scan_kraken_signals,
)
from strategies.setup import derive_expected_cost, derive_leverage_required, derive_notional, derive_size, derive_worst_case_loss
from trading.position import PositionState


def _cfg(**overrides):
    base = dict(
        pending_signal_default_stop_pct=Decimal("0.02"), pending_signal_r_multiple=Decimal("2.0"),
        pending_signal_expiry_minutes=30, pending_signal_risk_pct=Decimal("0.0025"),
        pending_signal_expected_hold_hours=Decimal("24"),
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
        score=0.5, signal="BUY", price=Decimal("100.0"), stop_price=Decimal("95.0"),
        source_timeframe="1d", pos=_pos(), cfg=_cfg(),
    )
    assert sig.direction == "LONG"
    assert sig.stop_price == Decimal("95")
    # take_profit = price + r_multiple * stop_distance = 100 + 2*5 = 110
    assert sig.take_profit_price == Decimal("110")
    assert "structural stop" in sig.thesis


def test_build_payload_short_flips_stop_and_take_profit_direction():
    sig = _build_payload(
        strategy_name="SMC Breakout", symbol="BTC/USD", exchange="kraken", rank=2,
        score=0.3, signal="SELL", price=Decimal("100.0"), stop_price=Decimal("105.0"),
        source_timeframe="1d", pos=_pos(), cfg=_cfg(),
    )
    assert sig.direction == "SHORT"
    # take_profit = price - r_multiple * stop_distance = 100 - 2*5 = 90
    assert sig.take_profit_price == Decimal("90")


def test_build_payload_falls_back_to_default_stop_pct_when_strategy_has_none():
    sig = _build_payload(
        strategy_name="EMA Crossover", symbol="BTC/USD", exchange="kraken", rank=3,
        score=0.2, signal="BUY", price=Decimal("100.0"), stop_price=None,
        source_timeframe="1d", pos=_pos(), cfg=_cfg(pending_signal_default_stop_pct=Decimal("0.02")),
    )
    assert sig.stop_price == Decimal("98")  # 100 * (1 - 0.02)
    assert sig.take_profit_price == Decimal("104")  # 100 + 2*2
    assert "fallback" in sig.thesis


def test_build_payload_sizes_via_risk_based_formula_not_leg_allocation():
    """Sizing must come from strategies.setup.derive_size (equity*risk_pct /
    stop_distance) — not any allocation-based fraction of equity. Cross-check
    against the formula directly rather than a hand-computed literal."""
    equity = Decimal("5000")
    price, stop = Decimal("100.0"), Decimal("98.0")
    cfg = _cfg(pending_signal_risk_pct=Decimal("0.0025"))
    sig = _build_payload(
        strategy_name="EMA Crossover", symbol="BTC/USD", exchange="kraken", rank=1,
        score=0.5, signal="BUY", price=price, stop_price=stop,
        source_timeframe="1d", pos=_pos(float(equity)), cfg=cfg,
    )
    expected_size = derive_size(equity, price, stop, risk_pct=Decimal("0.0025"))
    expected_notional = derive_notional(expected_size, price)
    assert sig.qty == expected_size.quantize(sig.qty)
    assert sig.position_size_usd == expected_notional.quantize(sig.position_size_usd)


def test_build_payload_sizing_scales_with_pending_signal_risk_pct():
    """Doubling the Kraken-Prop-specific risk_pct doubles size exactly —
    proving sizing is driven by that config value alone."""
    equity = Decimal("5000")
    price, stop = Decimal("100.0"), Decimal("98.0")
    base = _build_payload(
        strategy_name="EMA Crossover", symbol="BTC/USD", exchange="kraken", rank=1,
        score=0.5, signal="BUY", price=price, stop_price=stop, source_timeframe="1d",
        pos=_pos(float(equity)), cfg=_cfg(pending_signal_risk_pct=Decimal("0.0025")),
    )
    doubled = _build_payload(
        strategy_name="EMA Crossover", symbol="BTC/USD", exchange="kraken", rank=1,
        score=0.5, signal="BUY", price=price, stop_price=stop, source_timeframe="1d",
        pos=_pos(float(equity)), cfg=_cfg(pending_signal_risk_pct=Decimal("0.005")),
    )
    assert doubled.qty == base.qty * 2


def test_build_payload_populates_all_new_risk_fields_consistently():
    equity = Decimal("5000")
    price, stop = Decimal("60000.0"), Decimal("59500.0")
    cfg = _cfg(pending_signal_risk_pct=Decimal("0.0025"), pending_signal_expected_hold_hours=Decimal("24"))
    sig = _build_payload(
        strategy_name="SMC Breakout", symbol="BTC/USD", exchange="kraken", rank=1,
        score=0.5, signal="BUY", price=price, stop_price=stop, source_timeframe="1d",
        pos=_pos(float(equity)), cfg=cfg,
    )
    size = derive_size(equity, price, stop, risk_pct=Decimal("0.0025"))
    notional = derive_notional(size, price)
    expected_cost = derive_expected_cost(notional, Decimal("24"))
    worst_case_loss = derive_worst_case_loss(size, price, stop, expected_cost)
    leverage_required = derive_leverage_required(notional, equity)

    assert sig.expected_cost == expected_cost.quantize(sig.expected_cost)
    assert sig.worst_case_loss == worst_case_loss.quantize(sig.worst_case_loss)
    assert sig.leverage_required == leverage_required.quantize(sig.leverage_required)
    assert sig.expected_hold_hours == Decimal("24")
    assert sig.generated_at is not None


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
