"""Tests for senior_trader/review_session.py — the Phase 1 read-only review
cycle. Mocks the DB the same way tests/test_pretrade_gate.py and
tests/test_pending_signal_detector.py do (MagicMock db/conn/cursor, patch
at the point of use) — no real database connection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd

from risk.prop_account import PropAccountState
from senior_trader.models import NO_TRADE, NO_TRADE_NO_ACTIVE_SIGNALS, NO_TRADE_STALE_SIGNAL, TAKE
from senior_trader.review_session import run_review_cycle

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _healthy_account() -> PropAccountState:
    return PropAccountState(
        balance=Decimal("10000"), unrealized_pnl=Decimal("0"), accrued_funding_cost=Decimal("0"),
        daily_start_balance=Decimal("10000"), peak_balance=Decimal("10000"),
        kraken_mdd_pct=Decimal("0.03"), last_rollover=NOW,
    )


def _signals_df(**overrides) -> pd.DataFrame:
    row = dict(
        id=1, strategy_name="EMA Crossover", symbol="BTC/USD", direction="LONG",
        limit_price=Decimal("100"), expires_at=NOW + timedelta(minutes=30),
    )
    row.update(overrides)
    return pd.DataFrame([row])


def _decisions_insert_calls(cur) -> list:
    return [c for c in cur.execute.call_args_list if "INSERT INTO decisions" in c[0][0]]


def _setup_db(read_sql_return: pd.DataFrame, gate_row=None):
    db = MagicMock()
    db.read_sql.return_value = read_sql_return
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur
    cur.fetchone.return_value = gate_row
    return db, cur


@patch("senior_trader.review_session.account_current_state")
def test_no_active_signals_writes_placeholder_no_trade_decision(mock_account):
    mock_account.return_value = _healthy_account()
    db, cur = _setup_db(pd.DataFrame())

    with patch("senior_trader.review_session.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        report = run_review_cycle(db, exchange="kraken")

    assert report.active_signals_reviewed == 0
    assert report.decisions_no_trade == 1
    assert report.decisions_take == 0
    assert report.incidents_raised == 0

    inserts = _decisions_insert_calls(cur)
    assert len(inserts) == 1
    _, params = inserts[0][0]
    assert params[0]  # decision_id present
    assert params[1] is None  # pending_signal_id
    assert params[3] == NO_TRADE
    assert params[4] == NO_TRADE_NO_ACTIVE_SIGNALS


@patch("senior_trader.review_session.account_current_state")
def test_active_unexpired_signal_with_gate_approval_is_taken(mock_account):
    mock_account.return_value = _healthy_account()
    gate_row = (99, "APPROVE", "all_checks_passed", NOW - timedelta(minutes=1))
    db, cur = _setup_db(_signals_df(), gate_row=gate_row)

    with patch("senior_trader.review_session.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        report = run_review_cycle(db, exchange="kraken")

    assert report.active_signals_reviewed == 1
    assert report.decisions_take == 1
    assert report.decisions_no_trade == 0

    inserts = _decisions_insert_calls(cur)
    assert len(inserts) == 1
    _, params = inserts[0][0]
    assert params[1] == 1        # pending_signal_id
    assert params[2] == 99       # gate_decision_id
    assert params[3] == TAKE
    assert params[4] is None     # no_trade_reason


@patch("senior_trader.review_session.account_current_state")
def test_expired_active_signal_is_no_trade_stale(mock_account):
    mock_account.return_value = _healthy_account()
    expired_df = _signals_df(expires_at=NOW - timedelta(minutes=5))
    db, cur = _setup_db(expired_df, gate_row=None)

    with patch("senior_trader.review_session.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        report = run_review_cycle(db, exchange="kraken")

    assert report.decisions_no_trade == 1
    assert report.decisions_take == 0

    inserts = _decisions_insert_calls(cur)
    _, params = inserts[0][0]
    assert params[3] == NO_TRADE
    assert params[4] == NO_TRADE_STALE_SIGNAL


@patch("senior_trader.review_session.record_incident")
@patch("senior_trader.review_session.account_current_state")
def test_kraken_mdl_breach_raises_incident(mock_account, mock_record_incident):
    breached = PropAccountState(
        balance=Decimal("9000"), unrealized_pnl=Decimal("0"), accrued_funding_cost=Decimal("0"),
        daily_start_balance=Decimal("10000"), peak_balance=Decimal("10000"),
        kraken_mdd_pct=Decimal("0.03"), last_rollover=NOW,
    )
    mock_account.return_value = breached
    db, _cur = _setup_db(pd.DataFrame())

    report = run_review_cycle(db, exchange="kraken")

    assert report.incidents_raised == 1
    mock_record_incident.assert_called_once()


@patch("senior_trader.review_session.account_current_state")
def test_healthy_account_raises_no_incident(mock_account):
    mock_account.return_value = _healthy_account()
    db, _cur = _setup_db(pd.DataFrame())

    report = run_review_cycle(db, exchange="kraken")

    assert report.incidents_raised == 0
