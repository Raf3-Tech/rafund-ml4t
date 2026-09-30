"""Senior Trader configuration — a distinct concern from config.loader.Settings
(engine/strategy config), loaded the same way: config/settings.yaml's
`senior_trader` block, overridden by SENIOR_TRADER_* env vars.

correlation_cap_pct/consecutive_loss_limit exist here as forward-looking
scaffolding for risk.pretrade_gate.GateContext's two admittedly-uncalibrated
placeholder defaults (see risk/pretrade_gate.py's module docstring and
state/repository_map.md Section 3) — build_gate_context() below is ready for
a future phase to use. It is deliberately NOT wired into
signals/pending_signal_detector.py::scan_kraken_signals in Phase 1: that
would change an existing, already-working module's behavior, which this
phase's completion criteria (state/integration_plan.md Section 8) explicitly
rule out. Wiring it in is a small, reviewable follow-up, not a Phase 1 task.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from config.loader import load_config

RESEARCH = "RESEARCH"
PAPER = "PAPER"
DRY_RUN = "DRY_RUN"
# SUPERVISED_BROWSER and AUTHORIZED_AUTONOMOUS_BROWSER (AGENT_ORCHESTRATION.md
# Section 42) are later phases — not valid values until that code exists.
_VALID_EXECUTION_MODES = (RESEARCH, PAPER, DRY_RUN)


def _env_decimal(key: str, default: Optional[Decimal]) -> Optional[Decimal]:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return Decimal(raw)


def _env_int(key: str, default: Optional[int]) -> Optional[int]:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class SeniorTraderConfig:
    execution_mode: str = RESEARCH
    correlation_cap_pct: Optional[Decimal] = None
    consecutive_loss_limit: Optional[int] = None


def _load() -> SeniorTraderConfig:
    raw = load_config().get("senior_trader", {}) or {}

    execution_mode = os.environ.get("SENIOR_TRADER_EXECUTION_MODE", raw.get("execution_mode", RESEARCH))
    if execution_mode not in _VALID_EXECUTION_MODES:
        raise ValueError(
            f"senior_trader.execution_mode={execution_mode!r} is not implemented yet "
            f"(Phase 1 supports {_VALID_EXECUTION_MODES}); see AGENT_ORCHESTRATION.md Section 42."
        )

    yaml_cap = raw.get("correlation_cap_pct")
    cap = _env_decimal(
        "SENIOR_TRADER_CORRELATION_CAP_PCT",
        Decimal(str(yaml_cap)) if yaml_cap is not None else None,
    )

    yaml_limit = raw.get("consecutive_loss_limit")
    limit = _env_int(
        "SENIOR_TRADER_CONSECUTIVE_LOSS_LIMIT",
        int(yaml_limit) if yaml_limit is not None else None,
    )

    return SeniorTraderConfig(execution_mode=execution_mode, correlation_cap_pct=cap, consecutive_loss_limit=limit)


_config: Optional[SeniorTraderConfig] = None
_config_lock = threading.Lock()


def get_senior_trader_config() -> SeniorTraderConfig:
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = _load()
    return _config


def build_gate_context(**overrides):
    """A risk.pretrade_gate.GateContext seeded from SeniorTraderConfig's
    overrides where a risk owner has set them, falling back to the gate's
    own defaults otherwise. Never a second risk calculation — just real
    numbers in place of uncalibrated placeholders when supplied. Not called
    from anywhere in Phase 1 (see module docstring); exists so a later phase
    can wire it in without redesigning this config module."""
    from risk.pretrade_gate import DEFAULT_CONSECUTIVE_LOSS_LIMIT, DEFAULT_CORRELATION_CAP_PCT, GateContext

    cfg = get_senior_trader_config()
    kwargs = dict(
        correlation_cap_pct=(
            cfg.correlation_cap_pct if cfg.correlation_cap_pct is not None else DEFAULT_CORRELATION_CAP_PCT
        ),
        consecutive_loss_limit=(
            cfg.consecutive_loss_limit if cfg.consecutive_loss_limit is not None else DEFAULT_CONSECUTIVE_LOSS_LIMIT
        ),
    )
    kwargs.update(overrides)
    return GateContext(**kwargs)
