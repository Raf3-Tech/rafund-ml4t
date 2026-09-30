"""Tests for execution/mobile/adb_client.py::tap_trade_view_toggle — the
two unlabeled icon buttons bottom-right of the Trade tab's order book,
purpose unconfirmed. See test_execution_mobile_boundaries.py for the
structural guarantees.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from execution.mobile.adb_client import tap_trade_view_toggle


def test_rejects_unknown_icon_without_requiring_authorization(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ValueError, match="middle"):
        tap_trade_view_toggle("middle")


def test_requires_authorization_for_a_valid_icon(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(Exception):  # ComplianceGateError
        tap_trade_view_toggle("left")


@pytest.mark.parametrize("icon,expected_xy", [
    ("left", (1067, 1939)),
    ("right", (1133, 1939)),
])
def test_taps_the_documented_coordinates_for_each_icon(monkeypatch, icon, expected_xy):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", return_value=MagicMock(stdout="", stderr="", returncode=0)) as mock_run:
        tap_trade_view_toggle(icon)

    args = mock_run.call_args[0][0]
    assert "input" in args and "tap" in args
    x, y = expected_xy
    assert str(x) in args
    assert str(y) in args
