"""Tests for senior_trader/config.py — execution-mode validation and the
config-overridable gate-context placeholders (not wired into the real gate
yet; see the module's own docstring)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from senior_trader.config import DRY_RUN, PAPER, RESEARCH, SeniorTraderConfig, build_gate_context


def test_default_config_is_research_mode_with_no_overrides():
    cfg = SeniorTraderConfig()
    assert cfg.execution_mode == RESEARCH
    assert cfg.correlation_cap_pct is None
    assert cfg.consecutive_loss_limit is None


def test_build_gate_context_falls_back_to_gate_defaults_when_unset():
    from risk.pretrade_gate import DEFAULT_CONSECUTIVE_LOSS_LIMIT, DEFAULT_CORRELATION_CAP_PCT

    context = build_gate_context()
    assert context.correlation_cap_pct == DEFAULT_CORRELATION_CAP_PCT
    assert context.consecutive_loss_limit == DEFAULT_CONSECUTIVE_LOSS_LIMIT


def test_build_gate_context_uses_configured_overrides(monkeypatch):
    import senior_trader.config as config_module

    monkeypatch.setattr(
        config_module, "get_senior_trader_config",
        lambda: SeniorTraderConfig(correlation_cap_pct=Decimal("0.5"), consecutive_loss_limit=2),
    )
    context = build_gate_context()
    assert context.correlation_cap_pct == Decimal("0.5")
    assert context.consecutive_loss_limit == 2


def test_build_gate_context_explicit_kwargs_win_over_config(monkeypatch):
    import senior_trader.config as config_module

    monkeypatch.setattr(
        config_module, "get_senior_trader_config",
        lambda: SeniorTraderConfig(correlation_cap_pct=Decimal("0.5")),
    )
    context = build_gate_context(correlation_cap_pct=Decimal("0.9"))
    assert context.correlation_cap_pct == Decimal("0.9")


def test_valid_execution_modes_accepted():
    for mode in (RESEARCH, PAPER, DRY_RUN):
        assert SeniorTraderConfig(execution_mode=mode).execution_mode == mode
