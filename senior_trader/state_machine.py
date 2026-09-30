"""Deterministic Senior Trader state machine.

Phase 1 wired STARTING through EXECUTION_READY/NO_TRADE (review_session.py).
Phase 3 adds EXECUTING/RECONCILING/ESCALATED — senior_trader/paper_session.py
drives one EXECUTION_READY -> EXECUTING -> RECONCILING -> (SIGNAL_REVIEW |
ESCALATED) transition per execution intent it processes, so this table is
actually exercised at runtime, not just tested in isolation. POSITION_MONITORING
and HALTED still have no legal transition into them: POSITION_MONITORING
implies an open-position lifecycle Phase 3 doesn't track (no CLOSE
simulation exists yet — see senior_trader/metrics.py's module docstring),
and HALTED is a later phase's emergency-stop concern
(AGENT_ORCHESTRATION.md Section 19). ESCALATED deliberately has no path
back to trading states — Section 19: "never attempt to trade its way out of
a breach" — a human clears an incident, this state machine doesn't.
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


_LEGAL_TRANSITIONS = {
    TraderState.STARTING: {TraderState.HEALTH_CHECK},
    TraderState.HEALTH_CHECK: {TraderState.OBSERVING, TraderState.SHUTDOWN},
    TraderState.OBSERVING: {TraderState.SIGNAL_REVIEW, TraderState.SHUTDOWN},
    TraderState.SIGNAL_REVIEW: {TraderState.DECISION_PENDING, TraderState.SHUTDOWN},
    TraderState.DECISION_PENDING: {
        TraderState.NO_TRADE, TraderState.EXECUTION_READY, TraderState.SHUTDOWN,
    },
    TraderState.NO_TRADE: {TraderState.SIGNAL_REVIEW, TraderState.SHUTDOWN},
    TraderState.EXECUTION_READY: {TraderState.EXECUTING, TraderState.SHUTDOWN},
    TraderState.EXECUTING: {TraderState.RECONCILING, TraderState.SHUTDOWN},
    TraderState.RECONCILING: {
        TraderState.SIGNAL_REVIEW, TraderState.ESCALATED, TraderState.SHUTDOWN,
    },
    TraderState.ESCALATED: {TraderState.SHUTDOWN},
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
