"""Deterministic Senior Trader state machine — Phase 1 subset.

Only the states/transitions Phase 1's read-only review cycle
(senior_trader/review_session.py) actually visits are wired into
_LEGAL_TRANSITIONS below. EXECUTION_READY, EXECUTING, RECONCILING,
POSITION_MONITORING, HALTED, and ESCALATED exist in the TraderState enum —
so a later phase adding them doesn't need a migration or a rename — but have
no legal transition INTO them yet beyond what's listed here. See
state/integration_plan.md Section 5 for the phase boundary this encodes.
"""
from __future__ import annotations

from enum import Enum


class TraderState(str, Enum):
    STARTING = "STARTING"
    HEALTH_CHECK = "HEALTH_CHECK"
    OBSERVING = "OBSERVING"
    SIGNAL_REVIEW = "SIGNAL_REVIEW"
    DECISION_PENDING = "DECISION_PENDING"
    NO_TRADE = "NO_TRADE"
    EXECUTION_READY = "EXECUTION_READY"
    EXECUTING = "EXECUTING"
    RECONCILING = "RECONCILING"
    POSITION_MONITORING = "POSITION_MONITORING"
    HALTED = "HALTED"
    ESCALATED = "ESCALATED"
    SHUTDOWN = "SHUTDOWN"


# Phase 1 legal transitions only. A later phase implementing execution adds
# EXECUTION_READY -> EXECUTING -> RECONCILING -> POSITION_MONITORING here;
# until then EXECUTION_READY is a dead end (a TAKE decision is recorded, but
# nothing carries it further) by design, not by oversight.
_LEGAL_TRANSITIONS = {
    TraderState.STARTING: {TraderState.HEALTH_CHECK},
    TraderState.HEALTH_CHECK: {TraderState.OBSERVING, TraderState.SHUTDOWN},
    TraderState.OBSERVING: {TraderState.SIGNAL_REVIEW, TraderState.SHUTDOWN},
    TraderState.SIGNAL_REVIEW: {TraderState.DECISION_PENDING, TraderState.SHUTDOWN},
    TraderState.DECISION_PENDING: {
        TraderState.NO_TRADE, TraderState.EXECUTION_READY, TraderState.SHUTDOWN,
    },
    TraderState.NO_TRADE: {TraderState.SIGNAL_REVIEW, TraderState.SHUTDOWN},
    TraderState.EXECUTION_READY: {TraderState.SHUTDOWN},
    TraderState.SHUTDOWN: set(),
}


class IllegalTransition(ValueError):
    pass


def transition(current: TraderState, target: TraderState) -> TraderState:
    """Raise IllegalTransition if `target` is not reachable from `current`
    in this phase's transition table; otherwise return `target` so the
    caller can assign it as the new current state."""
    legal = _LEGAL_TRANSITIONS.get(current, set())
    if target not in legal:
        raise IllegalTransition(f"{current.value} -> {target.value} is not a legal transition")
    return target
