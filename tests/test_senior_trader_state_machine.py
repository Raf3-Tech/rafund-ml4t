"""Tests for senior_trader/state_machine.py — the Phase 1 legal-transition
subset. Mirrors AGENT_ORCHESTRATION.md Section 33's own example
(`REJECTED -> EXECUTING must be impossible`), applied to this phase's
actual state set.
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


def test_execution_ready_cannot_reach_executing_in_phase_1():
    with pytest.raises(IllegalTransition):
        transition(TraderState.EXECUTION_READY, TraderState.EXECUTING)


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
