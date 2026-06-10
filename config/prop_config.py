"""Prop-challenge configuration: read/write helpers.

Shared by the dashboard routes and the research pipeline so both always
see the same values.  The backing file is config/prop_challenge.json;
env/DB overrides are intentionally not supported — this is a UI-managed
config, not an ops config.
"""

from __future__ import annotations

import json
from typing import Any

from config.paths import PROP_CONFIG_PATH

_DEFAULTS: dict[str, Any] = {
    "account_size": 5000.0,
    "profit_pct": 9.0,
    "daily_loss_pct": 3.0,
    "max_dd_pct": 3.0,
    "max_leverage": 5.0,
    "win_prob_target": 70.0,
}


def read_prop_config() -> dict[str, Any]:
    cfg = dict(_DEFAULTS)
    if PROP_CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(PROP_CONFIG_PATH.read_text()))
        except Exception:
            pass
    return cfg


def write_prop_config(cfg: dict) -> dict[str, Any]:
    sanitized: dict[str, Any] = {
        "account_size":    max(100.0,  float(cfg.get("account_size",    5000))),
        "profit_pct":      max(0.1,    min(50.0,  float(cfg.get("profit_pct",      9)))),
        "daily_loss_pct":  max(0.1,    min(20.0,  float(cfg.get("daily_loss_pct",  3)))),
        "max_dd_pct":      max(0.1,    min(20.0,  float(cfg.get("max_dd_pct",      3)))),
        "max_leverage":    max(1.0,    min(100.0, float(cfg.get("max_leverage",     5)))),
        "win_prob_target": max(0.0,    min(100.0, float(cfg.get("win_prob_target", 70)))),
    }
    PROP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROP_CONFIG_PATH.write_text(json.dumps(sanitized, indent=2))
    return sanitized
