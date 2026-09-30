"""Auditor — AGENT_ORCHESTRATION.md Section 39. Independent of the decision
process: reads only, asks whether the other components behaved as
designed, never resolves or overrides anything itself.

Phase 3 implements the subset of Section 39's question list actually
checkable against Phase 1-3's schema (decisions, gate_decisions,
pending_signals, execution_intents, trade_journal). Not yet checkable, and
not faked here: whether execution matched intent against a REAL fill
(needs Phase 5's browser-observed data), and the WEEKLY_TRADING_AUDIT /
STRATEGY_REVIEW_REPORT / EXECUTION_QUALITY_REPORT report types Section 39
also names — those need more history than a single daily run produces.
run_daily_audit() is this phase's DAILY_TRADING_AUDIT.
"""
from __future__ import annotations

from senior_trader.models import AuditFinding, AuditReport

_BANNED_SETUP_TAGS = {"perfect_setup", "high_confidence", "strong_signal"}
_FACTUAL_CLOSE_REASONS = {
    "stop_loss", "take_profit", "signal_exit", "timeout", "risk_halt",
    "manual_intervention", "strategy_invalidated", "account_protection",
}


def _check_every_take_backed_by_approve_or_reduce(db) -> AuditFinding:
    """A TAKE decision must never exist without a real APPROVE/REDUCE gate
    decision behind it — AGENT_ORCHESTRATION.md Section 3's "never
    reinterpret a REJECT as an APPROVE" is a promise about behavior; this
    checks the resulting data actually held that promise."""
    df = db.read_sql(
        """
        SELECT d.decision_id, gd.action FROM decisions d
        LEFT JOIN gate_decisions gd ON gd.id = d.gate_decision_id
        WHERE d.decision = 'TAKE' AND (gd.action IS NULL OR gd.action NOT IN ('APPROVE', 'REDUCE'))
        """
    )
    offenders = df.to_dict("records") if not df.empty else []
    return AuditFinding(
        check="every_take_decision_backed_by_approve_or_reduce",
        passed=not offenders, evidence={"offenders": offenders},
    )


def _check_no_orphan_execution_intents(db) -> AuditFinding:
    """Every execution_intents row must reference a real decisions row —
    an intent can't exist without the decision that authorized it."""
    df = db.read_sql(
        """
        SELECT ei.intent_id FROM execution_intents ei
        LEFT JOIN decisions d ON d.decision_id = ei.decision_id
        WHERE d.decision_id IS NULL
        """
    )
    offenders = df.to_dict("records") if not df.empty else []
    return AuditFinding(
        check="no_orphan_execution_intents", passed=not offenders, evidence={"offenders": offenders},
    )


def _check_setup_tags_are_not_generic(db) -> AuditFinding:
    """AGENT_ORCHESTRATION.md Section 21: setup_tag must describe what
    actually happened, never a dishonest confidence label."""
    df = db.read_sql("SELECT trade_id, setup_tag FROM trade_journal")
    offenders = df[df["setup_tag"].isin(_BANNED_SETUP_TAGS)].to_dict("records") if not df.empty else []
    return AuditFinding(
        check="setup_tags_are_not_generic", passed=not offenders, evidence={"offenders": offenders},
    )


def _check_close_reasons_are_factual(db) -> AuditFinding:
    df = db.read_sql("SELECT trade_id, close_reason FROM trade_journal WHERE close_reason IS NOT NULL")
    offenders = (
        df[~df["close_reason"].isin(_FACTUAL_CLOSE_REASONS)].to_dict("records") if not df.empty else []
    )
    return AuditFinding(
        check="close_reasons_are_factual", passed=not offenders, evidence={"offenders": offenders},
    )


def run_daily_audit(db) -> AuditReport:
    return AuditReport(findings=[
        _check_every_take_backed_by_approve_or_reduce(db),
        _check_no_orphan_execution_intents(db),
        _check_setup_tags_are_not_generic(db),
        _check_close_reasons_are_factual(db),
    ])
