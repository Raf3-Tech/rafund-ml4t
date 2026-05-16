"""
Load settings from config/settings.yaml with environment variable overrides.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_PATH = _ROOT / "config" / "settings.yaml"


def _load_yaml() -> Dict[str, Any]:
    if not _SETTINGS_PATH.exists():
        return {}
    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


def _env_float(key: str, default: float) -> float:
    val = _env(key)
    return float(val) if val is not None else default


def _env_int(key: str, default: int) -> int:
    val = _env(key)
    return int(val) if val is not None else default


def _env_bool(key: str, default: bool) -> bool:
    val = _env(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """Runtime configuration (YAML + .env overrides)."""

    def __init__(self, raw: Optional[Dict[str, Any]] = None):
        raw = raw or _load_yaml()
        db = raw.get("database", {})
        data = raw.get("data", {})
        strat = raw.get("strategy", {})
        bt = raw.get("backtesting", {})
        eval_cfg = raw.get("evaluation", {})
        collect = raw.get("collect", {})

        self.db_host = _env("DB_HOST", db.get("host", "localhost"))
        self.db_port = _env_int("DB_PORT", int(db.get("port", 5432)))
        self.db_name = _env("DB_NAME", db.get("database", "rafund"))
        self.db_user = _env("DB_USER", db.get("user", "postgres"))
        self.db_password = _env("DB_PASSWORD", db.get("password", "postgres"))

        symbols_csv = _env("COLLECT_SYMBOLS", "")
        if symbols_csv:
            self.collect_symbols = [s.strip() for s in symbols_csv.split(",") if s.strip()]
        else:
            self.collect_symbols = list(collect.get("symbols", [
                "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
                "ADA/USDT", "DOT/USDT", "LINK/USDT", "XRP/USDT",
            ]))

        self.collect_start_date = _env(
            "COLLECT_START_DATE", collect.get("start_date", "2017-08-17")
        )
        self.timeframe = collect.get("timeframe", data.get("timeframe", "1d"))

        self.eval_pair_a = _env("EVAL_PAIR_A", eval_cfg.get("pair_a", "BTC/USDT"))
        self.eval_pair_b = _env("EVAL_PAIR_B", eval_cfg.get("pair_b", "ETH/USDT"))
        self.account_size = _env_float("ACCOUNT_SIZE", float(eval_cfg.get("account_size", 5000)))
        self.step1_profit = _env_float("STEP1_PROFIT", float(eval_cfg.get("step1_profit", 250)))
        self.step2_profit = _env_float("STEP2_PROFIT", float(eval_cfg.get("step2_profit", 500)))
        self.max_daily_loss_pct = _env_float(
            "MAX_DAILY_LOSS_PCT", float(eval_cfg.get("max_daily_loss_pct", 0.04))
        )
        self.max_drawdown_pct = _env_float(
            "MAX_DRAWDOWN_PCT", float(eval_cfg.get("max_drawdown_pct", 0.06))
        )
        self.max_leverage = _env_float("MAX_LEVERAGE", float(eval_cfg.get("max_leverage", 5.0)))

        self.entry_threshold = _env_float(
            "ENTRY_THRESHOLD", float(strat.get("entry_threshold", 2.0))
        )
        self.exit_threshold = _env_float(
            "EXIT_THRESHOLD", float(strat.get("exit_threshold", 0.5))
        )
        self.lookback = _env_int("LOOKBACK", int(strat.get("lookback_period", 60)))
        self.leg_allocation_pct = _env_float(
            "LEG_ALLOCATION_PCT", float(strat.get("leg_allocation_pct", 0.18))
        )
        self.commission = _env_float("COMMISSION", float(bt.get("commission", 0.001)))
        self.stop_loss_spread_pct = _env_float(
            "STOP_LOSS_SPREAD_PCT", float(strat.get("stop_loss_spread_pct", 0.03))
        )
        self.max_holding_days = _env_int(
            "MAX_HOLDING_DAYS", int(strat.get("max_holding_days", 30))
        )
        self.save_to_db = _env_bool("SAVE_TO_DB", True)

    def research_pairs_enabled(self) -> bool:
        return _env_bool("RESEARCH_ALL_PAIRS", False)


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
