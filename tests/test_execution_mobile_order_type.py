"""Tests for execution/mobile/adb_client.py::tap_order_type_dropdown and
tap_order_type_option — switching the order form's order type. This
system is limit-only by design (AGENT_ORCHESTRATION.md Section 16); these
tests exist partly to prove "Limit" is a real, selectable option distinct
from Kraken's own "Market" default, which nothing in this codebase should
ever submit unchanged.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from execution.mobile.adb_client import tap_order_type_dropdown, tap_order_type_option


def test_dropdown_requires_authorization(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(Exception):  # ComplianceGateError
        tap_order_type_dropdown()


def test_dropdown_taps_documented_coordinates(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", return_value=MagicMock(stdout="", stderr="", returncode=0)) as mock_run:
        tap_order_type_dropdown()
    args = mock_run.call_args[0][0]
    assert "input" in args and "tap" in args
    assert "345" in args and "218" in args


def test_option_rejects_unknown_without_requiring_authorization(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ValueError, match="StopLimit"):
        tap_order_type_option("StopLimit")


def test_option_requires_authorization_for_a_valid_option(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(Exception):  # ComplianceGateError
        tap_order_type_option("Limit")


@pytest.mark.parametrize("option,expected_xy", [
    ("Limit", (155, 287)),
    ("Market", (155, 344)),
    ("Trigger", (155, 401)),
])
def test_option_taps_the_documented_coordinates(monkeypatch, option, expected_xy):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", return_value=MagicMock(stdout="", stderr="", returncode=0)) as mock_run:
        tap_order_type_option(option)
    args = mock_run.call_args[0][0]
    x, y = expected_xy
    assert str(x) in args
    assert str(y) in args
