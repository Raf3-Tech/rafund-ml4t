"""Tests for senior_trader/state_machine.py. Mirrors AGENT_ORCHESTRATION.md
Section 33's own example (`REJECTED -> EXECUTING must be impossible`),
applied to this codebase's actual state set.
"""
from __future__ import annotations

import pytest

from senior_trader.state_machine import IllegalTransition, TraderState, transition


def test_full_happy_path_no_trade_cycle():
    state = transition(TraderState.STARTING, TraderState.HEALTH_CHECK)
    state = transition(state, TraderState.OBSERVING)
    state = transition(state, TraderState.SIGNAL_REVIEW)
    state = transition(state, TraderState.DECISION_PENDING)
    state = transition(state, TraderState.NO_TRADE)
    state = transition(state, TraderState.SIGNAL_REVIEW)
    assert state == TraderState.SIGNAL_REVIEW


def test_decision_pending_can_reach_execution_ready():
    state = transition(TraderState.DECISION_PENDING, TraderState.EXECUTION_READY)
    assert state == TraderState.EXECUTION_READY


def test_full_happy_path_take_and_simulated_execution_cycle():
    """The exact sequence senior_trader/paper_session.py drives per
    execution intent: EXECUTION_READY -> EXECUTING -> RECONCILING -> back
    to SIGNAL_REVIEW for the next candidate."""
    state = transition(TraderState.DECISION_PENDING, TraderState.EXECUTION_READY)
    state = transition(state, TraderState.EXECUTING)
    state = transition(state, TraderState.RECONCILING)
    state = transition(state, TraderState.SIGNAL_REVIEW)
    assert state == TraderState.SIGNAL_REVIEW


def test_reconciliation_failure_reaches_escalated_not_trading_states():
    state = transition(TraderState.RECONCILING, TraderState.ESCALATED)
    assert state == TraderState.ESCALATED


def test_escalated_has_no_path_back_to_trading():
    """AGENT_ORCHESTRATION.md Section 19: never attempt to trade out of a
    breach — a human clears an incident, this state machine doesn't."""
    with pytest.raises(IllegalTransition):
        transition(TraderState.ESCALATED, TraderState.SIGNAL_REVIEW)
    with pytest.raises(IllegalTransition):
        transition(TraderState.ESCALATED, TraderState.EXECUTING)


def test_execution_ready_cannot_skip_straight_to_reconciling():
    with pytest.raises(IllegalTransition):
        transition(TraderState.EXECUTION_READY, TraderState.RECONCILING)


def test_shutdown_has_no_outgoing_transitions():
    with pytest.raises(IllegalTransition):
        transition(TraderState.SHUTDOWN, TraderState.STARTING)


def test_starting_cannot_skip_health_check():
    with pytest.raises(IllegalTransition):
        transition(TraderState.STARTING, TraderState.OBSERVING)


def test_every_state_can_reach_shutdown_or_is_shutdown_itself():
    """No dead end short of SHUTDOWN — every non-terminal state in this
    phase's table has an escape hatch."""
    from senior_trader.state_machine import _LEGAL_TRANSITIONS

    for state, legal in _LEGAL_TRANSITIONS.items():
        if state is TraderState.SHUTDOWN:
            continue
        assert TraderState.SHUTDOWN in legal or any(
            TraderState.SHUTDOWN in _LEGAL_TRANSITIONS.get(t, set()) for t in legal
        ), f"{state} has no path to SHUTDOWN"
