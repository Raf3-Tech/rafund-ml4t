"""
Load settings from config/settings.yaml with environment variable overrides.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import yaml
from sqlalchemy.engine import URL

from config.constants import STAT_ARB_ENTRY_Z, STAT_ARB_EXIT_Z

_log = logging.getLogger(__name__)

# Hard maximums for the 1-step Turbo prop eval constraint.
# These are the UPPER BOUNDS — configured values must never exceed them
# when eval_mode is active, and Python-level fallback defaults must
# never be set above them regardless of mode.
_EVAL_MAX_DAILY_LOSS_PCT: float = 0.03
_EVAL_MAX_DRAWDOWN_PCT: float = 0.03

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_PATH = _ROOT / "config" / "settings.yaml"

_VAR_PATTERN = re.compile(r"\$\{[^}]+\}")


def build_database_url(
    *,
    for_sqlalchemy: bool = True,
    database_url: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    database: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    sslmode: Optional[str] = None,
) -> Optional[str]:
    """Single source of truth for the database connection URL.

    Precedence: an explicit/`DATABASE_URL` value wins; otherwise the URL is
    assembled from the discrete ``DB_*`` components (with an optional
    ``DB_SSLMODE`` for hosted databases such as Supabase).

    ``for_sqlalchemy`` selects the scheme:
      * True  -> ``postgresql+psycopg2://`` (SQLAlchemy engine + Alembic)
      * False -> ``postgresql://``           (raw libpq DSN for psycopg2 pool)

    Both the application (``DatabaseConnection``) and Alembic call this, so
    local dev and a shipped deployment configure the DB through exactly one
    mechanism — set ``DATABASE_URL`` and everything (app, engine, migrations)
    follows.

    CONTRACT: ``DATABASE_URL`` must be URL form
    (``postgresql://user:pass@host:port/db``). A libpq keyword DSN
    (``"host=... dbname=..."``) is intentionally NOT supported — it is passed
    through unchanged and will fail the SQLAlchemy engine/Alembic. Hosted
    providers hand you URL form; using DSN form is operator error by design.
    """
    driver = "postgresql+psycopg2" if for_sqlalchemy else "postgresql"

    raw = database_url or os.environ.get("DATABASE_URL")
    if raw:
        # Normalize the scheme to the requested driver, preserving the rest
        # (credentials, host, db, and any ?sslmode=... query string).
        for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
            if raw.startswith(prefix):
                return f"{driver}://{raw[len(prefix) :]}"
        return raw  # some other scheme — pass through untouched

    host = host or os.environ.get("DB_HOST", "localhost")
    port = int(port or os.environ.get("DB_PORT", 5432))
    database = database or os.environ.get("DB_NAME", "rafund")
    user = user or os.environ.get("DB_USER", "postgres")
    password = password if password is not None else os.environ.get("DB_PASSWORD")
    sslmode = sslmode or os.environ.get("DB_SSLMODE")

    url = URL.create(
        driver,
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    if sslmode:
        url = url.set(query={"sslmode": sslmode})
    # render_as_string handles correct escaping of special chars in the password.
    return url.render_as_string(hide_password=False)


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` references in YAML string values.

    A reference to an unset environment variable is left untouched by
    ``os.path.expandvars`` — we convert any such leftover ``${VAR}`` to ``None``
    so a literal placeholder (e.g. ``${DB_PASSWORD}``) can never become the
    actual configured value.
    """
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        expanded = os.path.expandvars(value)
        if _VAR_PATTERN.search(expanded):
            return None
        return expanded
    return value


def _load_yaml() -> Dict[str, Any]:
    if not _SETTINGS_PATH.exists():
        return {}
    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        return _expand_env(yaml.safe_load(f) or {})


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
        live_cfg = raw.get("live_account", {})
        collect = raw.get("collect", {})
        retrain = raw.get("retraining", {})

        self.db_host = _env("DB_HOST", db.get("host", "localhost"))
        self.db_port = _env_int("DB_PORT", int(db.get("port", 5432)))
        self.db_name = _env("DB_NAME", db.get("database", "rafund"))
        self.db_user = _env("DB_USER", db.get("user", "postgres"))
        self.db_password = _env("DB_PASSWORD", db.get("password"))

        symbols_csv = _env("COLLECT_SYMBOLS", "")
        if symbols_csv:
            self.collect_symbols = [
                s.strip() for s in symbols_csv.split(",") if s.strip()
            ]
        else:
            self.collect_symbols = list(
                collect.get(
                    "symbols",
                    [
                        "BTC/USDT",
                        "ETH/USDT",
                        "SOL/USDT",
                        "BNB/USDT",
                        "ADA/USDT",
                        "DOT/USDT",
                        "LINK/USDT",
                        "XRP/USDT",
                    ],
                )
            )

        kraken_csv = _env("KRAKEN_SYMBOLS", "")
        if kraken_csv:
            self.kraken_symbols = [s.strip() for s in kraken_csv.split(",") if s.strip()]
        else:
            self.kraken_symbols = list(
                collect.get(
                    "kraken_symbols",
                    [
                        "BTC/USD",
                        "ETH/USD",
                        "SOL/USD",
                        "ADA/USD",
                        "DOT/USD",
                        "LINK/USD",
                        "XRP/USD",
                    ],
                )
            )

        htx_csv = _env("HTX_SYMBOLS", "")
        if htx_csv:
            self.htx_symbols = [s.strip() for s in htx_csv.split(",") if s.strip()]
        else:
            self.htx_symbols = list(
                collect.get(
                    "htx_symbols",
                    [
                        "BTC/USDT",
                        "ETH/USDT",
                        "SOL/USDT",
                        "BNB/USDT",
                        "ADA/USDT",
                        "DOT/USDT",
                        "LINK/USDT",
                        "XRP/USDT",
                    ],
                )
            )

        self.collect_start_date = _env(
            "COLLECT_START_DATE", collect.get("start_date", "2017-08-17")
        )
        self.timeframe = collect.get("timeframe", data.get("timeframe", "1d"))

        # Multi-timeframe hierarchy: higher frames define bias, lower frames time the entry.
        # Collector fetches OHLCV for every listed timeframe on startup and each cycle.
        self.timeframes: List[str] = list(
            collect.get("timeframes", ["1h", "4h", "1d", "1w"])
        )

        # Warn immediately if settings.yaml is missing or has no [evaluation] section —
        # silently falling back to hardcoded values for risk parameters is never safe.
        if not _SETTINGS_PATH.exists():
            _log.warning(
                "settings.yaml NOT FOUND at %s — using hardcoded risk defaults. "
                "This is UNSAFE for live/eval trading. "
                "max_daily_loss_pct=%.2f max_drawdown_pct=%.2f",
                _SETTINGS_PATH, _EVAL_MAX_DAILY_LOSS_PCT, _EVAL_MAX_DRAWDOWN_PCT,
            )
        elif not eval_cfg:
            _log.warning(
                "settings.yaml has no [evaluation] section — using hardcoded risk defaults. "
                "max_daily_loss_pct=%.2f max_drawdown_pct=%.2f",
                _EVAL_MAX_DAILY_LOSS_PCT, _EVAL_MAX_DRAWDOWN_PCT,
            )

        self.eval_pair_a = _env("EVAL_PAIR_A", eval_cfg.get("pair_a", "BTC/USDT"))
        self.eval_pair_b = _env("EVAL_PAIR_B", eval_cfg.get("pair_b", "ETH/USDT"))
        self.account_size = _env_float(
            "ACCOUNT_SIZE", float(eval_cfg.get("account_size", 5000))
        )
        self.step1_profit = _env_float(
            "STEP1_PROFIT", float(eval_cfg.get("step1_profit", 450))
        )
        self.step2_profit = _env_float(
            "STEP2_PROFIT", float(eval_cfg.get("step2_profit", 450))
        )
        # Fallback defaults are set to the eval constraint maximums (3% / 3%).
        # If settings.yaml is absent these kick in — they are conservative by design
        # so a missing config file degrades safely rather than permissively.
        self.max_daily_loss_pct = _env_float(
            "MAX_DAILY_LOSS_PCT", float(eval_cfg.get("max_daily_loss_pct", _EVAL_MAX_DAILY_LOSS_PCT))
        )
        self.max_drawdown_pct = _env_float(
            "MAX_DRAWDOWN_PCT", float(eval_cfg.get("max_drawdown_pct", _EVAL_MAX_DRAWDOWN_PCT))
        )
        self.max_leverage = _env_float(
            "MAX_LEVERAGE", float(eval_cfg.get("max_leverage", 5.0))
        )
        self.live_account = SimpleNamespace(
            account_size=_env_float(
                "LIVE_ACCOUNT_ACCOUNT_SIZE", float(live_cfg.get("account_size", 5000))
            ),
            daily_loss_limit_pct=_env_float(
                "LIVE_ACCOUNT_DAILY_LOSS_LIMIT_PCT",
                float(live_cfg.get("daily_loss_limit_pct", 0.03)),
            ),
            drawdown_limit_pct=_env_float(
                "LIVE_ACCOUNT_DRAWDOWN_LIMIT_PCT",
                float(live_cfg.get("drawdown_limit_pct", 0.03)),
            ),
            max_leverage=_env_float(
                "LIVE_ACCOUNT_MAX_LEVERAGE", float(live_cfg.get("max_leverage", 2.0))
            ),
        )

        # Two-tier protective floor (paper/live only — backtest uses prop firm's actual floor).
        # Soft floor: reduce position size 50% when cumulative DD from initial capital ≥ this.
        # Hard floor: halt + force-close at this amount — $5 buffer before the prop firm's $150 limit.
        # Recovery: soft-floor sizing returns to normal only when DD drops back below this amount.
        self.soft_floor_amount = _env_float(
            "SOFT_FLOOR_AMOUNT", float(eval_cfg.get("soft_floor_amount", 130.0))
        )
        self.hard_floor_amount = _env_float(
            "HARD_FLOOR_AMOUNT", float(eval_cfg.get("hard_floor_amount", 145.0))
        )
        self.soft_floor_recovery_amount = _env_float(
            "SOFT_FLOOR_RECOVERY_AMOUNT", float(eval_cfg.get("soft_floor_recovery_amount", 100.0))
        )

        # Gap-risk safety clip for live/paper position sizing (see
        # trading.position.max_safe_notional): caps a single new position so
        # that even a `max_adverse_move_pct`-sized adverse move right after
        # entry only burns `risk_buffer_pct` of the remaining headroom to the
        # daily-loss/drawdown floor, instead of leg_allocation_pct alone
        # (which has no awareness of how close equity already is to the
        # floor, or of single-bar gap risk). Defaults are calibrated to the
        # worst single-bar BTC move observed in our own price history
        # (~40% on "Black Thursday", 2020-03-12).
        self.max_adverse_move_pct = _env_float(
            "MAX_ADVERSE_MOVE_PCT", float(eval_cfg.get("max_adverse_move_pct", 0.40))
        )
        self.risk_buffer_pct = _env_float(
            "RISK_BUFFER_PCT", float(eval_cfg.get("risk_buffer_pct", 0.7))
        )

        # Per-trade risk cap: maximum fraction of account_size that a single trade
        # may lose if price moves max_adverse_move_pct against the position.
        # notional_cap = account_size * risk_per_trade_pct / max_adverse_move_pct
        # On $5K with 0.5% risk and 40% worst-case: cap = $62.50 notional.
        self.risk_per_trade_pct = _env_float(
            "RISK_PER_TRADE_PCT", float(eval_cfg.get("risk_per_trade_pct", 0.005))
        )

        # Eval mode: tighter constraints (DCA disabled, max 3 trades/day, only
        # ready_for_live strategies run in eval slot).
        self.eval_mode = _env_bool(
            "EVAL_MODE", bool(eval_cfg.get("eval_mode", False))
        )
        self.max_trades_per_day = _env_int(
            "MAX_TRADES_PER_DAY",
            int(eval_cfg.get("max_trades_per_day", 3 if eval_cfg.get("eval_mode") else 10)),
        )

        # Email alerts on risk events (halts, kill switch, account failure).
        # Silently disabled (logged, not sent) unless all three are set.
        self.alert_email_to = _env("ALERT_EMAIL_TO", "")
        self.smtp_host = _env("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = _env_int("SMTP_PORT", 587)
        self.smtp_user = _env("SMTP_USER", "")
        self.smtp_password = _env("SMTP_PASSWORD", "")

        self.entry_threshold = _env_float(
            "ENTRY_THRESHOLD", float(strat.get("entry_threshold", STAT_ARB_ENTRY_Z))
        )
        self.exit_threshold = _env_float(
            "EXIT_THRESHOLD", float(strat.get("exit_threshold", STAT_ARB_EXIT_Z))
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

        # Phase 1: permutation-entropy tradeability filter.
        self.entropy_enabled = _env_bool(
            "ENTROPY_ENABLED", bool(strat.get("entropy_enabled", True))
        )
        self.entropy_threshold = _env_float(
            "ENTROPY_THRESHOLD", float(strat.get("entropy_threshold", 0.95))
        )
        self.entropy_window = _env_int(
            "ENTROPY_WINDOW", int(strat.get("entropy_window", 30))
        )
        self.entropy_embedding = _env_int(
            "ENTROPY_EMBEDDING", int(strat.get("entropy_embedding", 3))
        )

        self.save_to_db = _env_bool("SAVE_TO_DB", True)

        # Paper trading: skip a BUY/SELL whose direction disagrees with the
        # recent-history regime trend (see trading.paper_trader's regime
        # filter) — the "golden rule" of top-down analysis (only trade with
        # the dominant trend), applied with data the engine already computes
        # (backtesting.window_engine.compute_regime) rather than new data.
        self.paper_regime_filter_enabled = _env_bool(
            "PAPER_REGIME_FILTER_ENABLED", bool(strat.get("paper_regime_filter_enabled", True))
        )

        # Retraining symbols may intentionally be a *subset* of collect symbols
        # (only some pairs have feature rows) — see settings.yaml. We enforce
        # the subset relationship, not equality, in _validate().
        self.retrain_symbols = list(retrain.get("symbols", []))

        self._validate()

    def _validate(self) -> None:
        """Fail fast on out-of-range configuration rather than silently
        producing nonsensical risk/strategy behaviour at runtime."""
        errors: List[str] = []

        def _check(cond: bool, msg: str) -> None:
            if not cond:
                errors.append(msg)

        _check(
            0.0 < self.max_daily_loss_pct < 1.0,
            f"max_daily_loss_pct must be in (0, 1), got {self.max_daily_loss_pct}",
        )
        _check(
            0.0 < self.max_drawdown_pct < 1.0,
            f"max_drawdown_pct must be in (0, 1), got {self.max_drawdown_pct}",
        )
        _check(
            self.max_leverage > 0, f"max_leverage must be > 0, got {self.max_leverage}"
        )
        _check(
            self.live_account.max_leverage > 0,
            f"live_account.max_leverage must be > 0, got {self.live_account.max_leverage}",
        )
        _check(
            0.0 < self.live_account.daily_loss_limit_pct < 1.0,
            f"live_account.daily_loss_limit_pct must be in (0, 1), got {self.live_account.daily_loss_limit_pct}",
        )
        _check(
            0.0 < self.live_account.drawdown_limit_pct < 1.0,
            f"live_account.drawdown_limit_pct must be in (0, 1), got {self.live_account.drawdown_limit_pct}",
        )
        _check(
            self.live_account.account_size > 0,
            f"live_account.account_size must be > 0, got {self.live_account.account_size}",
        )
        _check(
            0.0 <= self.commission < 1.0,
            f"commission must be in [0, 1), got {self.commission}",
        )
        _check(
            self.entry_threshold > 0,
            f"entry_threshold must be > 0, got {self.entry_threshold}",
        )
        _check(
            self.exit_threshold >= 0,
            f"exit_threshold must be >= 0, got {self.exit_threshold}",
        )
        _check(
            0.0 < self.leg_allocation_pct <= 1.0,
            f"leg_allocation_pct must be in (0, 1], got {self.leg_allocation_pct}",
        )
        _check(
            0.0 < self.max_adverse_move_pct <= 1.0,
            f"max_adverse_move_pct must be in (0, 1], got {self.max_adverse_move_pct}",
        )
        _check(
            0.0 < self.risk_buffer_pct <= 1.0,
            f"risk_buffer_pct must be in (0, 1], got {self.risk_buffer_pct}",
        )
        _check(
            0.0 < self.risk_per_trade_pct <= 0.05,
            f"risk_per_trade_pct must be in (0, 0.05], got {self.risk_per_trade_pct}",
        )
        _check(
            0.0 < self.soft_floor_amount < self.hard_floor_amount,
            f"soft_floor_amount ({self.soft_floor_amount}) must be < hard_floor_amount ({self.hard_floor_amount})",
        )
        _check(
            self.hard_floor_amount < self.account_size * self.max_drawdown_pct,
            f"hard_floor_amount ({self.hard_floor_amount}) must be < prop-firm floor "
            f"({self.account_size * self.max_drawdown_pct:.2f})",
        )
        _check(
            self.max_trades_per_day > 0,
            f"max_trades_per_day must be > 0, got {self.max_trades_per_day}",
        )

        # Assertion: when eval_mode is active, risk limits MUST be at or within
        # the prop-firm maximums. This is a hard gate — misconfigured limits in
        # eval mode would silently allow a rule breach, which is never acceptable.
        if self.eval_mode:
            _check(
                self.max_daily_loss_pct <= _EVAL_MAX_DAILY_LOSS_PCT,
                f"eval_mode=true but max_daily_loss_pct={self.max_daily_loss_pct} "
                f"> eval maximum {_EVAL_MAX_DAILY_LOSS_PCT}",
            )
            _check(
                self.max_drawdown_pct <= _EVAL_MAX_DRAWDOWN_PCT,
                f"eval_mode=true but max_drawdown_pct={self.max_drawdown_pct} "
                f"> eval maximum {_EVAL_MAX_DRAWDOWN_PCT}",
            )
            _check(
                self.max_trades_per_day <= 3,
                f"eval_mode=true but max_trades_per_day={self.max_trades_per_day} > 3",
            )

        _check(self.lookback > 0, f"lookback must be > 0, got {self.lookback}")
        _check(
            self.max_holding_days > 0,
            f"max_holding_days must be > 0, got {self.max_holding_days}",
        )
        _check(
            self.account_size > 0, f"account_size must be > 0, got {self.account_size}"
        )
        _check(
            0.0 < self.entropy_threshold <= 1.0,
            f"entropy_threshold must be in (0, 1], got {self.entropy_threshold}",
        )
        _check(
            2 <= self.entropy_embedding <= 7,
            f"entropy_embedding must be in [2, 7], got {self.entropy_embedding}",
        )
        _check(
            self.entropy_window > self.entropy_embedding,
            f"entropy_window ({self.entropy_window}) must exceed "
            f"entropy_embedding ({self.entropy_embedding})",
        )

        drift = set(self.retrain_symbols) - set(self.collect_symbols)
        _check(
            not drift,
            f"retraining.symbols must be a subset of collect.symbols; "
            f"unknown symbols: {sorted(drift)}",
        )

        if errors:
            raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(errors))

    @property
    def hard_floor_equity(self) -> float:
        """Absolute equity level at which the internal hard floor fires."""
        return self.account_size - self.hard_floor_amount

    @property
    def soft_floor_equity(self) -> float:
        """Absolute equity level at which the soft floor (50% sizing) activates."""
        return self.account_size - self.soft_floor_amount

    @property
    def risk_notional_cap(self) -> float:
        """Max notional so that a max_adverse_move_pct loss ≤ risk_per_trade_pct × account_size."""
        return (self.account_size * self.risk_per_trade_pct) / self.max_adverse_move_pct

    def research_pairs_enabled(self) -> bool:
        return _env_bool("RESEARCH_ALL_PAIRS", False)


_settings: Optional[Settings] = None
_settings_lock = threading.Lock()


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        # Double-checked locking: cheap fast-path once initialised, safe under
        # concurrent first access from the retraining scheduler's threads.
        with _settings_lock:
            if _settings is None:
                _settings = Settings()
    return _settings


def load_config() -> Dict[str, Any]:
    """Return the raw parsed ``settings.yaml`` dict (no .env overrides applied).

    Used by components that consume nested config sections directly, such as the
    retraining scheduler's ``retraining`` block.
    """
    return _load_yaml()
