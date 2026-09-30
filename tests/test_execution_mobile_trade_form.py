"""Tests for execution/mobile/adb_client.py::tap_trade_action — opens the
order-entry form only, never places an order. See
test_execution_mobile_boundaries.py for the structural guarantees.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from execution.mobile.adb_client import tap_trade_action


def test_rejects_unknown_action_without_requiring_authorization(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ValueError, match="Limit"):
        tap_trade_action("Limit")


def test_requires_authorization_for_a_valid_action(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(Exception):  # ComplianceGateError
        tap_trade_action("Buy")


@pytest.mark.parametrize("action,expected_xy", [
    ("Buy", (274, 1777)),
    ("Sell", (775, 1777)),
])
def test_taps_the_documented_coordinates_for_each_action(monkeypatch, action, expected_xy):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", return_value=MagicMock(stdout="", stderr="", returncode=0)) as mock_run:
        tap_trade_action(action)

    args = mock_run.call_args[0][0]
    assert "input" in args and "tap" in args
    x, y = expected_xy
    assert str(x) in args
    assert str(y) in args
