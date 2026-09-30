"""Tests for execution/mobile/kraken_app_observer.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from execution.mobile.kraken_app_observer import StaleOrUnexpectedScreenError, observe_prop_account_screen

_FIXTURE_XML = (Path(__file__).parent / "fixtures" / "kraken_app_prop_screen_fixture.xml").read_text()


def test_raises_when_focus_is_not_the_kraken_app():
    with patch("execution.mobile.adb_client.current_focus", return_value="mCurrentFocus=Window{... com.android.settings ...}"):
        with pytest.raises(StaleOrUnexpectedScreenError, match="com.kraken.trade"):
            observe_prop_account_screen()


def test_raises_when_call_state_not_idle():
    with patch("execution.mobile.adb_client.current_focus", return_value="mCurrentFocus=Window{... com.kraken.trade ...}"), \
         patch("execution.mobile.adb_client.call_state", return_value="mCallState=2"):
        with pytest.raises(StaleOrUnexpectedScreenError, match="idle"):
            observe_prop_account_screen()


def test_observes_and_parses_when_focus_and_call_state_ok():
    with patch("execution.mobile.adb_client.current_focus", return_value="mCurrentFocus=Window{... com.kraken.trade/.MainActivity}"), \
         patch("execution.mobile.adb_client.call_state", return_value="mCallState=0"), \
         patch("execution.mobile.adb_client.dump_ui", return_value=_FIXTURE_XML):
        screen = observe_prop_account_screen()

    assert screen.account_label == "Prop • Test Fixture Account"
    assert len(screen.favorites) == 2


def test_verify_focus_false_skips_focus_and_call_checks():
    with patch("execution.mobile.adb_client.current_focus") as mock_focus, \
         patch("execution.mobile.adb_client.call_state") as mock_call, \
         patch("execution.mobile.adb_client.dump_ui", return_value=_FIXTURE_XML):
        observe_prop_account_screen(verify_focus=False)

    mock_focus.assert_not_called()
    mock_call.assert_not_called()
