"""Phase 3 — Paper Trader: decision -> execution intent -> simulated
execution -> reconciliation -> journal -> audit, with no real order placed
anywhere (tests/test_senior_trader_boundaries.py still passes unchanged).

Composes on top of Phase 1/2 rather than being folded into
review_session.run_review_cycle: it reads TAKE decisions that cycle already
wrote (senior_trader.adapters.decisions.take_decisions_without_intent) and
have no execution_intent yet, so review_session.py stays exactly what it
was — a pure read-only decision recorder that doesn't know anything
downstream ever executes.

Gated behind senior_trader.config's execution_mode: with the default
RESEARCH mode, this is a safe no-op (reports zero intents built) — Section
42's progression (RESEARCH -> PAPER -> DRY_RUN -> ...) only means something
if RESEARCH mode genuinely does nothing here. Set
senior_trader.execution_mode: PAPER in config/settings.yaml (or
SENIOR_TRADER_EXECUTION_MODE=PAPER) to actually run Phase 3's simulation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog

from senior_trader.adapters.decisions import take_decisions_without_intent
from senior_trader.adapters.gate import decision_by_id
from senior_trader.adapters.signals import signal_by_id
from senior_trader.auditor import run_daily_audit
from senior_trader.config import PAPER, get_senior_trader_config
from senior_trader.escalation import EXECUTION_ANOMALY, record_incident
from senior_trader.execution_intent import build_execution_intent, persist_execution_intent, record_fill_outcome
from senior_trader.journal import write_journal_entry
from senior_trader.models import FILLED, JournalEntry
from senior_trader.reconciliation import reconcile
from senior_trader.simulation import simulate_execution
from senior_trader.state_machine import TraderState, transition

logger = structlog.get_logger(__name__)


@dataclass
class PaperTradingCycleReport:
    exchange: str
    execution_mode: str
    generated_at: datetime
    intents_built: int
    fills_filled: int
    fills_expired: int
    reconciliation_failures: int
    audit_passed: bool
    skipped_reason: Optional[str] = None


def _process_one(db, decision: dict, account: str, execution_mode: str, now: datetime) -> tuple:
    """Returns (built: bool, fill_status: Optional[str], reconciled: bool)."""
    if decision["pending_signal_id"] is None:
        return False, None, True  # the "no active signals" placeholder decision — nothing to execute

    signal = signal_by_id(db, int(decision["pending_signal_id"]))
    if signal is None:
        logger.warning("senior_trader_paper_signal_missing", decision_id=decision["decision_id"])
        return False, None, True

    gate = decision_by_id(db, int(decision["gate_decision_id"])) if decision["gate_decision_id"] else None

    state = transition(TraderState.DECISION_PENDING, TraderState.EXECUTION_READY)
    intent = build_execution_intent(decision, signal, gate, account=account, execution_mode=execution_mode)
    persist_execution_intent(db, intent)

    state = transition(state, TraderState.EXECUTING)
    fill = simulate_execution(intent, now)
    record_fill_outcome(db, intent.intent_id, status=fill.status, fill_price=fill.fill_price, filled_at=fill.filled_at)

    state = transition(state, TraderState.RECONCILING)
    result = reconcile(intent, fill)

    if result.status != "RECONCILED":
        transition(state, TraderState.ESCALATED)
        record_incident(
            db, category=EXECUTION_ANOMALY, severity="high", evidence=result.evidence,
            recommended_action="Review the simulated fill against its execution intent by hand.",
            current_state={"intent_id": intent.intent_id},
        )
        logger.warning("senior_trader_paper_reconciliation_failure", intent_id=intent.intent_id, evidence=result.evidence)
        return True, fill.status, False

    transition(state, TraderState.SIGNAL_REVIEW)

    if fill.status == FILLED:
        entry = JournalEntry(
            trade_id=str(uuid.uuid4()), decision_id=decision["decision_id"],
            pending_signal_id=int(signal["id"]), setup_tag=intent.setup_tag,
            symbol=intent.symbol, direction=signal["direction"], signal_price=intent.price,
            decision_timestamp=decision["created_at"], execution_timestamp=fill.filled_at,
            fill_price=fill.fill_price, quantity=intent.quantity,
            notes="Phase 3 simulated execution — no real order placed.",
        )
        write_journal_entry(db, entry)

    return True, fill.status, True


def run_paper_trading_cycle(db, exchange: str = "kraken") -> PaperTradingCycleReport:
    now = datetime.now(timezone.utc)
    cfg = get_senior_trader_config()

    if cfg.execution_mode != PAPER:
        logger.info("senior_trader_paper_skipped_execution_mode_not_paper", execution_mode=cfg.execution_mode)
        return PaperTradingCycleReport(
            exchange=exchange, execution_mode=cfg.execution_mode, generated_at=now,
            intents_built=0, fills_filled=0, fills_expired=0, reconciliation_failures=0,
            audit_passed=True,
            skipped_reason=f"execution_mode={cfg.execution_mode!r} (set PAPER to run Phase 3 simulation)",
        )

    account = f"kraken_prop_{exchange}"
    pending = take_decisions_without_intent(db)

    intents_built = fills_filled = fills_expired = reconciliation_failures = 0
    for decision in pending:
        built, fill_status, reconciled = _process_one(db, decision, account, cfg.execution_mode, now)
        if built:
            intents_built += 1
            if fill_status == FILLED:
                fills_filled += 1
            else:
                fills_expired += 1
            if not reconciled:
                reconciliation_failures += 1

    audit = run_daily_audit(db)

    return PaperTradingCycleReport(
        exchange=exchange, execution_mode=cfg.execution_mode, generated_at=now,
        intents_built=intents_built, fills_filled=fills_filled, fills_expired=fills_expired,
        reconciliation_failures=reconciliation_failures, audit_passed=audit.all_passed,
    )
