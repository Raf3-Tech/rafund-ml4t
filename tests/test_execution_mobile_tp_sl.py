"""Tests for execution/mobile/adb_client.py::tap_tp_sl_toggle.

Per explicit user direction: every order this system's Phase 5 pipeline
populates MUST have TP/SL (Take Profit / Stop Loss) enabled — a risk
mitigation requirement, not an optional field. This toggle's existence
and correctness is load-bearing for that requirement.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from execution.mobile.adb_client import tap_tp_sl_toggle


def test_requires_authorization(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(Exception):  # ComplianceGateError
        tap_tp_sl_toggle()


def test_taps_documented_coordinates(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", return_value=MagicMock(stdout="", stderr="", returncode=0)) as mock_run:
        tap_tp_sl_toggle()
    args = mock_run.call_args[0][0]
    assert "input" in args and "tap" in args
    assert "36" in args and "600" in args
