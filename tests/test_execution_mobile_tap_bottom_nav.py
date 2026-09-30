"""Tests for execution/mobile/adb_client.py::tap_bottom_nav — the one
narrow, explicitly-authorized navigation exception in an otherwise
read-only package. See test_execution_mobile_boundaries.py for the
structural guarantees around how narrow it is.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from execution.mobile.adb_client import tap_bottom_nav


def test_rejects_unknown_tab_without_requiring_authorization(monkeypatch):
    """The ValueError check happens before the compliance gate — an
    invalid tab name should never even prompt for authorization."""
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ValueError, match="Buy"):
        tap_bottom_nav("Buy")


def test_requires_authorization_for_a_valid_tab(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(Exception):  # ComplianceGateError
        tap_bottom_nav("Prop")


@pytest.mark.parametrize("tab,expected_xy", [
    ("Prop", (129, 1855)),
    ("Markets", (364, 1855)),
    ("Trade", (600, 1855)),
    ("Portfolio", (835, 1855)),
    ("Account", (1070, 1855)),
])
def test_taps_the_documented_coordinates_for_each_tab(monkeypatch, tab, expected_xy):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", return_value=MagicMock(stdout="", stderr="", returncode=0)) as mock_run:
        tap_bottom_nav(tab)

    args = mock_run.call_args[0][0]
    assert "input" in args and "tap" in args
    x, y = expected_xy
    assert str(x) in args
    assert str(y) in args
