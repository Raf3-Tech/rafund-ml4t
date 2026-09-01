from portfolio.risk import AccountRules, PropFirmRiskGuard


def test_max_notional_scales_to_live_account_leverage():
    rules = AccountRules(
        account_size=5000.0,
        daily_loss_limit_pct=0.03,
        drawdown_limit_pct=0.03,
        max_leverage=2.0,
    )
    guard = PropFirmRiskGuard(rules, equity=5000.0, daily_start_equity=5000.0, peak_equity=5000.0)

    assert guard.max_notional(4850.0) == 0.0
    assert guard.max_notional(5000.0) == 10000.0


def test_propfirm_guard_halts_at_exact_drawdown_floor_regardless_of_peak_equity():
    rules = AccountRules(
        account_size=5000.0,
        daily_loss_limit_pct=0.03,
        drawdown_limit_pct=0.03,
        max_leverage=2.0,
    )
    guard = PropFirmRiskGuard(rules, equity=5000.0, daily_start_equity=5000.0, peak_equity=10000.0)

    at_floor = guard.check(4850.0)
    above_floor = guard.check(4850.01)

    assert at_floor["halted"] is True
    assert at_floor["reason"] == "drawdown_floor"
    assert above_floor["halted"] is False
