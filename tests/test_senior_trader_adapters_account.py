"""Tests for senior_trader/adapters/account.py — confirms it builds on
adapters.positions.current_position (Phase 2's single-source refactor)
rather than loading a second PositionState, and delegates to
signals.pending_signal_detector.current_account_state for the actual
PropAccountState construction (Phase 1's single-authority guarantee)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from risk.prop_account import PropAccountState
from senior_trader.adapters.account import current_state
from trading.position import PositionState


@patch("signals.pending_signal_detector.current_account_state")
@patch("senior_trader.adapters.positions.current_position")
@patch("config.loader.get_settings")
def test_current_state_delegates_to_shared_authority(mock_settings, mock_position, mock_account_state):
    pos = PositionState(run_id="live_kraken", strategy_name="", symbol="", exchange="kraken", equity=5000.0)
    mock_position.return_value = pos
    mock_settings.return_value = MagicMock()
    expected = MagicMock(spec=PropAccountState)
    mock_account_state.return_value = expected

    db = MagicMock()
    result = current_state(db, exchange="kraken")

    mock_position.assert_called_once_with(db, exchange="kraken")
    mock_account_state.assert_called_once_with(pos, mock_settings.return_value)
    assert result is expected
