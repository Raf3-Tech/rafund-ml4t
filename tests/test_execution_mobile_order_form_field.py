"""Tests for execution/mobile/adb_client.py::set_order_form_field."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from execution.mobile.adb_client import set_order_form_field


def test_rejects_unknown_field_without_requiring_authorization(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ValueError, match="total_usd"):
        set_order_form_field("total_usd", "100")


@pytest.mark.parametrize("bad_value", ["0.01; rm -rf /", "1 2", "$(whoami)", "abc", "", "1e10", "-5"])
def test_rejects_non_plain_numeric_value_without_requiring_authorization(monkeypatch, bad_value):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ValueError):
        set_order_form_field("quantity", bad_value)


def test_requires_authorization_for_a_valid_field_and_value(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(Exception):  # ComplianceGateError
        set_order_form_field("quantity", "0.01")


def test_taps_field_then_clears_then_types_value(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return MagicMock(stdout="", stderr="", returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        set_order_form_field("quantity", "0.015")

    # First call: tap the field.
    assert "tap" in calls[0] and "345" in calls[0] and "357" in calls[0]
    # Middle calls: clear via backspace keyevents.
    clear_calls = calls[1:-1]
    assert len(clear_calls) >= 20
    assert all("keyevent" in c and "KEYCODE_DEL" in c for c in clear_calls)
    # Last call: type the value.
    assert "text" in calls[-1] and "0.015" in calls[-1]


def test_limit_price_field_taps_its_own_coordinates(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", return_value=MagicMock(stdout="", stderr="", returncode=0)) as mock_run:
        set_order_form_field("limit_price", "85000.5")

    first_call_args = mock_run.call_args_list[0][0][0]
    assert "345" in first_call_args and "288" in first_call_args


@pytest.mark.parametrize("field,expected_xy", [
    ("take_profit", (345, 656)),
    ("stop_loss", (345, 723)),
])
def test_tp_sl_fields_tap_their_own_coordinates(monkeypatch, field, expected_xy):
    """Per explicit user direction: every order this system populates must
    have TP/SL — these fields only exist once tap_tp_sl_toggle() has
    enabled TP/SL on the form."""
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    with patch("subprocess.run", return_value=MagicMock(stdout="", stderr="", returncode=0)) as mock_run:
        set_order_form_field(field, "85000")

    first_call_args = mock_run.call_args_list[0][0][0]
    x, y = expected_xy
    assert str(x) in first_call_args and str(y) in first_call_args
