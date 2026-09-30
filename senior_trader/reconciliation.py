"""Reconciliation — AGENT_ORCHESTRATION.md Section 38.

Compares an execution intent (what was supposed to happen) against a fill
result (what actually happened). Phase 3's fill result is
senior_trader.simulation's SimulatedFill; a later phase's browser-observed
fill plugs into this exact same function, unchanged — reconcile() takes any
object with .status/.fill_price, not specifically a SimulatedFill.

Given simulate_execution() always fills at the intent's own price when not
expired, RECONCILIATION_FAILURE is unreachable from Phase 3's own simulator
today — this function exists as the tested seam a later phase's real,
imperfect fill data plugs into, not because Phase 3 can currently produce a
mismatch.
"""
from __future__ import annotations

from senior_trader.models import FILLED, RECONCILED, RECONCILIATION_FAILURE, ExecutionIntent, ReconciliationResult


def reconcile(intent: ExecutionIntent, fill) -> ReconciliationResult:
    if fill.status != FILLED:
        # An intent that expired unfilled is the expected outcome of the
        # stale-signal path (AGENT_ORCHESTRATION.md Section 15), not a
        # mismatch — nothing to reconcile against.
        return ReconciliationResult(
            status=RECONCILED, evidence={"intent_id": intent.intent_id, "fill_status": fill.status},
        )

    issues = {}
    if fill.fill_price != intent.price:
        issues["price_mismatch"] = {"expected": str(intent.price), "observed": str(fill.fill_price)}
    if fill.intent_id != intent.intent_id:
        issues["intent_id_mismatch"] = {"expected": intent.intent_id, "observed": fill.intent_id}

    if issues:
        return ReconciliationResult(status=RECONCILIATION_FAILURE, evidence=issues)
    return ReconciliationResult(
        status=RECONCILED, evidence={"intent_id": intent.intent_id, "fill_price": str(fill.fill_price)},
    )
