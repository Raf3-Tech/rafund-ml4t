"""Tests for execution/compliance_gate.py."""
from __future__ import annotations

import pytest

from execution.compliance_gate import (
    LIVE_BROWSER_EXECUTION_AUTHORIZED,
    ComplianceGateError,
    live_execution_authorized,
    mobile_observation_authorized,
    observation_authorized,
    require_live_execution_authorized,
    require_mobile_observation_authorized,
    require_observation_authorized,
)


def test_literal_constant_defaults_false():
    """AGENT_ORCHESTRATION.md Section 11's exact requirement."""
    assert LIVE_BROWSER_EXECUTION_AUTHORIZED is False


def test_observation_not_authorized_by_default(monkeypatch):
    monkeypatch.delenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", raising=False)
    assert observation_authorized() is False


def test_observation_authorized_only_with_exact_value_1(monkeypatch):
    monkeypatch.setenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", "1")
    assert observation_authorized() is True

    monkeypatch.setenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", "true")
    assert observation_authorized() is False

    monkeypatch.setenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", "yes")
    assert observation_authorized() is False


def test_live_execution_not_authorized_by_default(monkeypatch):
    monkeypatch.delenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", raising=False)
    monkeypatch.delenv("KRAKEN_LIVE_BROWSER_EXECUTION_AUTHORIZED", raising=False)
    assert live_execution_authorized() is False


def test_live_execution_requires_observation_too(monkeypatch):
    """Setting only the live-execution flag, without observation, must not
    authorize anything — Section 11's progression is additive, not a
    bypass."""
    monkeypatch.delenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", raising=False)
    monkeypatch.setenv("KRAKEN_LIVE_BROWSER_EXECUTION_AUTHORIZED", "1")
    assert live_execution_authorized() is False


def test_live_execution_authorized_when_both_flags_set(monkeypatch):
    monkeypatch.setenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", "1")
    monkeypatch.setenv("KRAKEN_LIVE_BROWSER_EXECUTION_AUTHORIZED", "1")
    assert live_execution_authorized() is True


def test_require_observation_authorized_raises_when_unset(monkeypatch):
    monkeypatch.delenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ComplianceGateError):
        require_observation_authorized()


def test_require_observation_authorized_passes_when_set(monkeypatch):
    monkeypatch.setenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", "1")
    require_observation_authorized()  # must not raise


def test_require_live_execution_authorized_raises_when_unset(monkeypatch):
    monkeypatch.delenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", raising=False)
    monkeypatch.delenv("KRAKEN_LIVE_BROWSER_EXECUTION_AUTHORIZED", raising=False)
    with pytest.raises(ComplianceGateError):
        require_live_execution_authorized()


def test_mobile_observation_not_authorized_by_default(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    assert mobile_observation_authorized() is False


def test_mobile_observation_authorized_only_with_exact_value_1(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    assert mobile_observation_authorized() is True
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "true")
    assert mobile_observation_authorized() is False


def test_mobile_observation_independent_of_browser_observation(monkeypatch):
    """Authorizing one observation surface must not authorize the other."""
    monkeypatch.setenv("KRAKEN_BROWSER_OBSERVATION_AUTHORIZED", "1")
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    assert observation_authorized() is True
    assert mobile_observation_authorized() is False


def test_require_mobile_observation_authorized_raises_when_unset(monkeypatch):
    monkeypatch.delenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", raising=False)
    with pytest.raises(ComplianceGateError):
        require_mobile_observation_authorized()


def test_require_mobile_observation_authorized_passes_when_set(monkeypatch):
    monkeypatch.setenv("KRAKEN_MOBILE_OBSERVATION_AUTHORIZED", "1")
    require_mobile_observation_authorized()  # must not raise
