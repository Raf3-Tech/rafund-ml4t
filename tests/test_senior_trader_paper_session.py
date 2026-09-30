"""Tests for senior_trader/paper_session.py — Phase 3's orchestrator.

Mocks every DB-touching collaborator (adapters, persistence, journal,
escalation, audit) but lets the real build_execution_intent/
simulate_execution/reconcile run, so this exercises the actual wiring
between them rather than re-testing their already-covered internals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from senior_trader.config import PAPER, RESEARCH, SeniorTraderConfig
from senior_trader.models import AuditReport
from senior_trader.paper_session import run_paper_trading_cycle

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _decision(**overrides) -> dict:
    base = dict(decision_id="dec-1", pending_signal_id=1, gate_decision_id=99, evidence={}, created_at=NOW)
    base.update(overrides)
    return base


def _signal(**overrides) -> dict:
    base = dict(
        id=1, strategy_name="EMA Crossover", symbol="BTC/USD", direction="LONG",
        limit_price=Decimal("100"), qty=Decimal("0.5"), expires_at=NOW + timedelta(minutes=30),
    )
    base.update(overrides)
    return base


def _gate(**overrides) -> dict:
    base = dict(id=99, action="APPROVE", reason_code="all_checks_passed", created_at=NOW)
    base.update(overrides)
    return base


def _patched(execution_mode=PAPER, decisions=None, signal=None, gate=None, audit_passed=True):
    return [
        patch("senior_trader.paper_session.get_senior_trader_config",
              return_value=SeniorTraderConfig(execution_mode=execution_mode)),
        patch("senior_trader.paper_session.take_decisions_without_intent", return_value=decisions or []),
        patch("senior_trader.paper_session.signal_by_id", return_value=signal),
        patch("senior_trader.paper_session.decision_by_id", return_value=gate),
        patch("senior_trader.paper_session.persist_execution_intent"),
        patch("senior_trader.paper_session.record_fill_outcome"),
        patch("senior_trader.paper_session.write_journal_entry"),
        patch("senior_trader.paper_session.record_incident"),
        patch("senior_trader.paper_session.run_daily_audit", return_value=AuditReport(findings=[])
              if audit_passed else AuditReport(findings=[MagicMock(passed=False)])),
    ]


def test_research_mode_is_a_safe_no_op():
    with patch("senior_trader.paper_session.get_senior_trader_config",
                return_value=SeniorTraderConfig(execution_mode=RESEARCH)), \
         patch("senior_trader.paper_session.take_decisions_without_intent") as mock_pending:
        report = run_paper_trading_cycle(MagicMock(), exchange="kraken")

    mock_pending.assert_not_called()
    assert report.intents_built == 0
    assert report.skipped_reason is not None
    assert "PAPER" in report.skipped_reason


def test_paper_mode_with_no_pending_decisions_still_runs_audit():
    patchers = _patched(decisions=[])
    with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7], patchers[8]:
        report = run_paper_trading_cycle(MagicMock(), exchange="kraken")

    assert report.intents_built == 0
    assert report.skipped_reason is None
    assert report.audit_passed is True


def test_placeholder_decision_with_no_pending_signal_id_is_skipped():
    decisions = [_decision(pending_signal_id=None)]
    patchers = _patched(decisions=decisions)
    with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], patchers[6], patchers[7], patchers[8], \
         patch("senior_trader.paper_session.build_execution_intent") as mock_build:
        report = run_paper_trading_cycle(MagicMock())

    mock_build.assert_not_called()
    assert report.intents_built == 0


def test_take_decision_with_unexpired_signal_fills_and_journals():
    decisions = [_decision()]
    patchers = _patched(decisions=decisions, signal=_signal(), gate=_gate())
    with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4] as mock_persist, patchers[5], \
         patchers[6] as mock_journal, patchers[7] as mock_incident, patchers[8], \
         patch("senior_trader.paper_session.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        report = run_paper_trading_cycle(MagicMock())

    assert report.intents_built == 1
    assert report.fills_filled == 1
    assert report.fills_expired == 0
    assert report.reconciliation_failures == 0
    mock_persist.assert_called_once()
    mock_journal.assert_called_once()
    mock_incident.assert_not_called()


def test_take_decision_with_expired_signal_does_not_journal():
    expired_signal = _signal(expires_at=NOW - timedelta(minutes=5))
    decisions = [_decision()]
    patchers = _patched(decisions=decisions, signal=expired_signal, gate=_gate())
    with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], \
         patchers[6] as mock_journal, patchers[7], patchers[8], \
         patch("senior_trader.paper_session.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        report = run_paper_trading_cycle(MagicMock())

    assert report.intents_built == 1
    assert report.fills_filled == 0
    assert report.fills_expired == 1
    mock_journal.assert_not_called()


def test_missing_signal_is_skipped_without_building_intent():
    decisions = [_decision()]
    patchers = _patched(decisions=decisions, signal=None, gate=_gate())
    with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4] as mock_persist, patchers[5], \
         patchers[6], patchers[7], patchers[8]:
        report = run_paper_trading_cycle(MagicMock())

    mock_persist.assert_not_called()
    assert report.intents_built == 0


def test_reconciliation_failure_raises_incident_and_skips_journal():
    decisions = [_decision()]
    patchers = _patched(decisions=decisions, signal=_signal(), gate=_gate())
    with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4], patchers[5], \
         patchers[6] as mock_journal, patchers[7] as mock_incident, patchers[8], \
         patch("senior_trader.paper_session.reconcile") as mock_reconcile:
        mock_reconcile.return_value = MagicMock(status="RECONCILIATION_FAILURE", evidence={"x": "y"})
        report = run_paper_trading_cycle(MagicMock())

    assert report.reconciliation_failures == 1
    mock_incident.assert_called_once()
    mock_journal.assert_not_called()
