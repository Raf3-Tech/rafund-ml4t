"""Tests for senior_trader/escalation.py — the incidents writer and the one
Phase 1 detection check (check_account_state_consistency)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from risk.prop_account import PropAccountState
from senior_trader.escalation import RISK_BREACH, check_account_state_consistency, record_incident

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _account(**overrides) -> PropAccountState:
    base = dict(
        balance=Decimal("10000"), unrealized_pnl=Decimal("0"), accrued_funding_cost=Decimal("0"),
        daily_start_balance=Decimal("10000"), peak_balance=Decimal("10000"),
        kraken_mdd_pct=Decimal("0.03"), last_rollover=NOW,
    )
    base.update(overrides)
    return PropAccountState(**base)


def test_healthy_account_has_no_consistency_issues():
    assert check_account_state_consistency(_account()) is None


def test_kraken_mdl_breach_detected():
    # daily_start_balance=10000, MDL floor at 3% => equity below 9700 breaches.
    account = _account(balance=Decimal("9000"))
    issues = check_account_state_consistency(account)
    assert issues is not None
    assert "kraken_mdl_breached" in issues


def test_kraken_mdd_breach_detected():
    # peak_balance=10000, kraken_mdd_pct=3% => equity below 9700 breaches.
    account = _account(peak_balance=Decimal("20000"), balance=Decimal("10000"))
    issues = check_account_state_consistency(account)
    assert issues is not None
    assert "kraken_mdd_breached" in issues


def test_record_incident_writes_expected_columns_and_commits():
    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur

    event_id = record_incident(
        db, category=RISK_BREACH, severity="critical", evidence={"foo": "bar"},
        recommended_action="freeze new execution",
    )

    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "incidents" in sql
    assert params[0] == event_id
    assert params[1] == RISK_BREACH
    assert params[2] == "critical"
    conn.commit.assert_called_once()
    db.return_connection.assert_called_once_with(conn)
