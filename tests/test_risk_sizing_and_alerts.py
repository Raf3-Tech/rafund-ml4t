"""Offline tests for the gap-risk position-sizing clip and email alerting.

max_safe_notional / _target_notional are pure functions of PositionState +
a settings-like object, so a SimpleNamespace stand-in is enough — no DB.
send_alert is tested with config.loader.get_settings and smtplib.SMTP both
patched at their use site in trading.alerts.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from trading.alerts import send_alert
from trading.paper_trader import _apply_paper_step, _capital_weighted_equity, _target_notional
from trading.position import PositionState, max_safe_notional


def _cfg(**overrides):
    base = dict(
        account_size=5000.0, max_drawdown_pct=0.06, max_daily_loss_pct=0.04,
        max_adverse_move_pct=0.40, risk_buffer_pct=0.7, leg_allocation_pct=0.18,
        # risk_per_trade_pct set high so the per-trade risk cap never binds in these
        # headroom-focused tests (risk_cap = 5000*0.99/0.40 ≈ 12,375 >> any headroom).
        # Tests that specifically verify the per-trade cap pass a lower value.
        risk_per_trade_pct=0.99,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _pos(equity=5000.0, daily_start_equity=5000.0):
    return PositionState(
        run_id="t", strategy_name="Test", symbol="BTC/USDT",
        equity=equity, peak_equity=equity, daily_start_equity=daily_start_equity,
    )


def test_max_safe_notional_at_full_equity_is_below_leg_allocation():
    cfg = _cfg()
    pos = _pos()
    # daily floor (4%) is tighter than the dd floor (6%) at full equity:
    # headroom = 5000 - 4800 = 200; safe = 200*0.7/0.40 = 350
    assert max_safe_notional(pos, cfg) == 350.0
    requested = pos.equity * cfg.leg_allocation_pct  # 900
    assert requested > max_safe_notional(pos, cfg)


def test_target_notional_shrinks_as_equity_nears_drawdown_floor():
    cfg = _cfg()
    near_floor = _pos(equity=4750.0, daily_start_equity=4750.0)  # only $50 above the $4700 floor
    far_from_floor = _pos(equity=5000.0)
    assert _target_notional(near_floor, cfg) < _target_notional(far_from_floor, cfg)


def test_target_notional_is_zero_at_or_below_floor():
    cfg = _cfg()
    at_floor = _pos(equity=4700.0, daily_start_equity=4700.0)
    assert _target_notional(at_floor, cfg) == 0.0


def test_target_notional_respects_daily_loss_floor_too():
    cfg = _cfg()
    # Equity is fine vs. the drawdown floor but the day already lost most of its budget.
    # daily floor = 5000 - 5000*0.04 = 4800; equity 4810 -> only $10 of daily headroom.
    pos = _pos(equity=4810.0, daily_start_equity=5000.0)
    notional = _target_notional(pos, cfg)
    assert notional < 20.0  # 10 * 0.7 / 0.40 = 17.5


def test_target_notional_honors_extra_cap():
    cfg = _cfg()
    pos = _pos()
    assert _target_notional(pos, cfg, extra_cap=10.0) == 10.0


def test_send_alert_noop_when_not_configured():
    fake_cfg = _cfg(alert_email_to="", smtp_user="", smtp_password="")
    with patch("config.loader.get_settings", return_value=fake_cfg), \
         patch("smtplib.SMTP") as mock_smtp:
        sent = send_alert("TEST SUBJECT", "body")
    assert sent is False
    mock_smtp.assert_not_called()


def test_send_alert_sends_when_configured():
    fake_cfg = _cfg(
        alert_email_to="rf3.dev@gmail.com", smtp_user="bot@gmail.com",
        smtp_password="app-password", smtp_host="smtp.gmail.com", smtp_port=587,
    )
    mock_server = MagicMock()
    with patch("config.loader.get_settings", return_value=fake_cfg), \
         patch("smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_server
        sent = send_alert("ACCOUNT FAILED", "equity breached the floor")
    assert sent is True
    mock_server.login.assert_called_once_with("bot@gmail.com", "app-password")
    mock_server.sendmail.assert_called_once()
    to_addrs = mock_server.sendmail.call_args[0][1]
    assert to_addrs == ["rf3.dev@gmail.com"]


def test_send_alert_failure_does_not_raise():
    fake_cfg = _cfg(alert_email_to="rf3.dev@gmail.com", smtp_user="bot@gmail.com",
                     smtp_password="app-password")
    with patch("config.loader.get_settings", return_value=fake_cfg), \
         patch("smtplib.SMTP", side_effect=OSError("network down")):
        sent = send_alert("DAILY LOSS HALT", "body")
    assert sent is False


# ── risk-parity-weighted paper capital sizing ─────────────────────────────────


class _StubDB:
    def __init__(self, returns_df):
        self._returns_df = returns_df

    def read_sql(self, query, params=None):
        return self._returns_df


def test_capital_weighted_equity_equal_split_with_no_history():
    candidates = [("EMA Crossover", "BTC/USDT", {}), ("MACD", "ETH/USDT", {})]
    db = _StubDB(pd.DataFrame())  # no engine_results history yet

    weights = _capital_weighted_equity(db, candidates, total_capital=10_000.0)

    assert set(weights) == {("EMA Crossover", "BTC/USDT"), ("MACD", "ETH/USDT")}
    assert sum(weights.values()) == pytest.approx(10_000.0)
    assert weights[("EMA Crossover", "BTC/USDT")] == pytest.approx(5_000.0)
    assert weights[("MACD", "ETH/USDT")] == pytest.approx(5_000.0)


def test_capital_weighted_equity_favors_lower_volatility_strategy():
    candidates = [("Steady", "BTC/USDT", {}), ("Volatile", "ETH/USDT", {})]
    key_steady = "Steady|BTC/USDT|{}"
    key_volatile = "Volatile|ETH/USDT|{}"

    rng = np.random.RandomState(0)
    n = 60
    returns_df = pd.DataFrame({
        "strategy_name": ["Steady"] * n + ["Volatile"] * n,
        "symbol": ["BTC/USDT"] * n + ["ETH/USDT"] * n,
        "params": ["{}"] * (2 * n),
        "window_end": list(range(n)) * 2,
        "total_return_pct": list(rng.normal(1.0, 0.5, n)) + list(rng.normal(1.0, 8.0, n)),
    })
    db = _StubDB(returns_df)

    weights = _capital_weighted_equity(db, candidates, total_capital=10_000.0)

    assert weights[("Steady", "BTC/USDT")] > weights[("Volatile", "ETH/USDT")]
    assert sum(weights.values()) == pytest.approx(10_000.0)


# ── regime-as-live-filter (top-down analysis "golden rule") ──────────────────


def test_regime_filter_blocks_long_open_in_bear_regime():
    pos = _pos()
    n = _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "BUY", 100.0, "2026-01-01",
        regime_direction="bear",
    )
    assert n == 0
    assert pos.is_flat


def test_regime_filter_blocks_short_open_in_bull_regime():
    pos = _pos()
    n = _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "SELL", 100.0, "2026-01-01",
        regime_direction="bull",
    )
    assert n == 0
    assert pos.is_flat


def test_regime_filter_allows_aligned_open():
    pos = _pos()
    n = _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "BUY", 100.0, "2026-01-01",
        regime_direction="bull",
    )
    assert n == 1
    assert pos.side == "LONG"


def test_regime_filter_does_not_block_closing_existing_position():
    pos = _pos()
    # Open LONG with no filter active.
    _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "BUY", 100.0, "2026-01-01",
    )
    assert pos.side == "LONG"

    # SELL flips: bull regime blocks the *new* SHORT open, but must not
    # block closing the existing LONG — should end up flat, not still long.
    n = _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "SELL", 110.0, "2026-01-02",
        regime_direction="bull",
    )
    assert n == 1  # the flip-close only, no new open
    assert pos.is_flat


def test_regime_filter_disabled_passthrough_when_direction_none():
    pos = _pos()
    n = _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "BUY", 100.0, "2026-01-01",
        regime_direction=None,
    )
    assert n == 1
    assert pos.side == "LONG"


# ── structural stop (SMCBreakout.get_stop_level) ─────────────────────────────


def test_open_records_stop_price():
    pos = _pos()
    _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "BUY", 100.0, "2026-01-01",
        stop_price=95.0,
    )
    assert pos.side == "LONG"
    assert pos.stop_price == 95.0


def test_stop_breach_force_closes_before_new_signal():
    pos = _pos()
    _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "BUY", 100.0, "2026-01-01",
        stop_price=95.0,
    )
    assert pos.side == "LONG"

    with patch("trading.paper_trader.log_order") as mock_log_order:
        # Price gapped through the stop; strategy still says HOLD/BUY this
        # bar — the stop must fire regardless of what the new signal says.
        n = _apply_paper_step(
            MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "BUY", 90.0, "2026-01-02",
            stop_price=80.0,
        )

    assert n == 1
    assert pos.is_flat
    assert pos.stop_price is None
    mock_log_order.assert_called_once()
    _, kwargs = mock_log_order.call_args
    assert kwargs.get("close_reason") == "stop"


def test_no_stop_set_never_force_closes():
    pos = _pos()
    _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "BUY", 100.0, "2026-01-01",
    )
    # Same-side signal repeats (no flip, no close-on-HOLD in play here) and
    # price drops a lot — with no stop set, nothing should force-close.
    n = _apply_paper_step(
        MagicMock(), _cfg(), pos, "Test", "BTC/USDT", "BUY", 90.0, "2026-01-02",
    )
    assert n == 0
    assert pos.side == "LONG"
