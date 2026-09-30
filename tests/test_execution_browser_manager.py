"""Tests for execution/browser/browser_manager.py.

Only tests the compliance-gate enforcement path, which raises before
playwright is ever imported — so this file runs without playwright
installed, same as the rest of this test session's environment. Actually
launching a browser is out of scope for this test suite by design (no
safe, real Kraken environment is available here — see
execution/browser/locators.py's docstring).
"""
from __future__ import annotations

import pytest

from execution.browser.browser_manager import kraken_terminal_session
from execution.compliance_gate import ComplianceGateError


def test_kraken_terminal_session_raises_when_observation_not_authorized(monkeypatch):
    monkeypatch.delenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ComplianceGateError):
        with kraken_terminal_session():
            pass  # pragma: no cover — must never reach here


def test_kraken_terminal_session_error_message_points_to_the_env_var(monkeypatch):
    monkeypatch.delenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ComplianceGateError, match="KRAKEN_BROWSER_OBSERVATION_AUTHORIZED"):
        with kraken_terminal_session():
            pass  # pragma: no cover
