"""Offline tests for the gap-risk position-sizing clip and email alerting.

max_safe_notional / _target_notional are pure functions of PositionState +
a settings-like object, so a SimpleNamespace stand-in is enough — no DB.
send_alert is tested with config.loader.get_settings and smtplib.SMTP both
patched at their use site in trading.alerts.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from trading.alerts import send_alert
from trading.paper_trader import _target_notional
from trading.position import PositionState, max_safe_notional


def _cfg(**overrides):
    base = dict(
        account_size=5000.0, max_drawdown_pct=0.06, max_daily_loss_pct=0.04,
        max_adverse_move_pct=0.40, risk_buffer_pct=0.7, leg_allocation_pct=0.18,
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
