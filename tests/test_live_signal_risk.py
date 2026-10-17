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


def _rules():
    return AccountRules(account_size=5000.0, daily_loss_limit_pct=0.03, drawdown_limit_pct=0.03, max_leverage=2.0)


def test_guard_halts_on_daily_loss_regression_sign_bug():
    """Risk-officer RO1: the daily distance used to *grow* with losses, so a
    -$160 day from a $5,300 start (MDL $159) reported halted=False."""
    guard = PropFirmRiskGuard(_rules(), equity=5300.0, daily_start_equity=5300.0, peak_equity=5300.0)

    breached = guard.check(5140.0)

    assert breached["halted"] is True
    assert breached["reason"] == "daily_loss_limit"
    assert breached["distance_to_daily_limit"] == -1.0
    assert guard.max_notional(5140.0) == 0.0


def test_daily_room_shrinks_as_the_day_loses():
    guard = PropFirmRiskGuard(_rules(), equity=5000.0, daily_start_equity=5000.0, peak_equity=5000.0)

    rooms = [guard.check(e)["distance_to_daily_limit"] for e in (5000.0, 4950.0, 4900.0)]

    assert rooms == [150.0, 100.0, 50.0]
    assert guard.check(5100.0)["distance_to_daily_limit"] == 150.0  # gains don't add room


def test_daily_limit_is_three_percent_of_day_start_balance():
    """Kraken recalculates MDL at 00:30 UTC from the balance at that time."""
    guard = PropFirmRiskGuard(_rules(), equity=5300.0, daily_start_equity=5300.0, peak_equity=5300.0)

    assert guard.check(5300.0)["distance_to_daily_limit"] == 159.0
    assert guard.check(5142.0)["halted"] is False
    assert guard.check(5141.0)["halted"] is True


def test_max_notional_scales_down_through_the_daily_half_distance():
    guard = PropFirmRiskGuard(_rules(), equity=5000.0, daily_start_equity=5000.0, peak_equity=5000.0)

    assert guard.max_notional(4925.0) == 10000.0  # $75 left = exactly half
    assert guard.max_notional(4962.5 - 75.0) == 5000.0  # $37.50 left -> half size
