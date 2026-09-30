"""Tests for senior_trader/auditor.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from senior_trader.auditor import run_daily_audit


def _db_with(**tables) -> MagicMock:
    """tables maps query-substring -> DataFrame to return for that call."""
    db = MagicMock()

    def _read_sql(sql, params=None):
        for substring, df in tables.items():
            if substring in sql:
                return df
        return pd.DataFrame()

    db.read_sql.side_effect = _read_sql
    return db


def test_all_checks_pass_on_clean_data():
    db = _db_with(**{"gd.action": pd.DataFrame(), "ei.intent_id": pd.DataFrame()})
    report = run_daily_audit(db)
    assert report.all_passed is True
    assert len(report.findings) == 4


def test_take_decision_without_approve_or_reduce_fails_that_check():
    db = _db_with(**{"gd.action": pd.DataFrame([{"decision_id": "d1", "action": "REJECT"}])})
    report = run_daily_audit(db)
    finding = next(f for f in report.findings if f.check == "every_take_decision_backed_by_approve_or_reduce")
    assert finding.passed is False
    assert finding.evidence["offenders"] == [{"decision_id": "d1", "action": "REJECT"}]
    assert report.all_passed is False


def test_orphan_execution_intent_fails_that_check():
    db = _db_with(**{"ei.intent_id": pd.DataFrame([{"intent_id": "i1"}])})
    report = run_daily_audit(db)
    finding = next(f for f in report.findings if f.check == "no_orphan_execution_intents")
    assert finding.passed is False


def test_generic_setup_tag_fails_that_check():
    # "setup_tag FROM trade_journal" (no WHERE) is unique to this query —
    # distinct from "close_reason FROM trade_journal WHERE ..." below.
    db = _db_with(
        **{"setup_tag FROM trade_journal": pd.DataFrame([{"trade_id": "t1", "setup_tag": "perfect_setup"}])}
    )
    report = run_daily_audit(db)
    finding = next(f for f in report.findings if f.check == "setup_tags_are_not_generic")
    assert finding.passed is False


def test_non_factual_close_reason_fails_that_check():
    db = _db_with(
        **{"close_reason FROM trade_journal": pd.DataFrame([{"trade_id": "t1", "close_reason": "vibes"}])}
    )
    report = run_daily_audit(db)
    finding = next(f for f in report.findings if f.check == "close_reasons_are_factual")
    assert finding.passed is False


def test_factual_close_reason_passes_that_check():
    db = _db_with(
        **{"close_reason FROM trade_journal": pd.DataFrame([{"trade_id": "t1", "close_reason": "stop_loss"}])}
    )
    report = run_daily_audit(db)
    finding = next(f for f in report.findings if f.check == "close_reasons_are_factual")
    assert finding.passed is True
