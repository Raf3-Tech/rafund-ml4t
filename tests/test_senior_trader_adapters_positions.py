"""Tests for senior_trader/adapters/positions.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from senior_trader.adapters.positions import current_position
from trading.position import PositionState


@patch("trading.position.load_position")
def test_current_position_loads_live_run_id_for_exchange(mock_load):
    mock_load.return_value = PositionState(run_id="live_kraken", strategy_name="", symbol="", exchange="kraken")
    db = MagicMock()

    result = current_position(db, exchange="kraken")

    mock_load.assert_called_once_with(db, "live_kraken", strategy_name="", symbol="", exchange="kraken")
    assert result.run_id == "live_kraken"


@patch("trading.position.load_position")
def test_current_position_scopes_run_id_to_given_exchange(mock_load):
    mock_load.return_value = PositionState(run_id="live_binance", strategy_name="", symbol="", exchange="binance")
    db = MagicMock()

    current_position(db, exchange="binance")

    mock_load.assert_called_once_with(db, "live_binance", strategy_name="", symbol="", exchange="binance")
