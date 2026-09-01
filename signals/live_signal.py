"""Live signal dataclass and generator for manual execution display.

This module is read-only: it derives a live BUY/SELL/HOLD suggestion only and
must not place orders or otherwise mutate state.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Optional

import structlog

from data.db import DatabaseConnection
from strategies.stat_arb import StatArbPairsStrategy, StatArbStrategy
from portfolio.optimizer import kelly_fraction
from config.loader import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class LiveSignal:
    symbol_a: str
    symbol_b: str
    timestamp: datetime
    z_score: Optional[float]
    signal: str  # "BUY" | "SELL" | "HOLD"
    hedge_ratio: Optional[float]
    entry_price_a: Optional[float]
    entry_price_b: Optional[float]
    suggested_notional: Optional[float]
    suggested_stop_pct: Optional[float]
    confidence: Optional[float]
    account_state: Dict

    def to_json(self) -> Dict:
        d = asdict(self)
        # timestamp -> iso
        if isinstance(d.get("timestamp"), datetime):
            d["timestamp"] = d["timestamp"].isoformat()
        return d


class LiveSignalGenerator:
    """Produce a LiveSignal for a pair using the canonical StatArb authority.

    Constructor:
      db: DatabaseConnection — used to pull recent prices
      strategy: StatArbPairsStrategy instance (wrapper) — used only to build
                the canonical StatArbStrategy core via its _core(params)
      account_rules: AccountRules-like object with max_notional() and a
                .halted flag (see portfolio.risk.PropFirmRiskGuard interface)
    """

    def __init__(self, db: DatabaseConnection, strategy: StatArbPairsStrategy, account_rules):
        self.db = db
        self.strategy = strategy
        self.account_rules = account_rules
        self.cfg = get_settings()

    def _pull_pair_prices(self, symbol_a: str, symbol_b: str, lookback: int):
        # re-use db.get_prices if available; fall back to read_sql pattern
        try:
            df_a = self.db.get_prices(symbol_a, limit=lookback)
            df_b = self.db.get_prices(symbol_b, limit=lookback)
        except Exception:
            # best-effort: query the prices table directly (older helper)
            df_a = self.db.read_sql(
                """
                SELECT timestamp, close
                FROM prices
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                [symbol_a, lookback],
            )
            df_b = self.db.read_sql(
                """
                SELECT timestamp, close
                FROM prices
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                [symbol_b, lookback],
            )
        # normalize to ascending timestamps and required column names
        if not df_a.empty:
            df_a = df_a.sort_values("timestamp").reset_index(drop=True)
            df_a = df_a.rename(columns={"close": "close"})
        if not df_b.empty:
            df_b = df_b.sort_values("timestamp").reset_index(drop=True)
            df_b = df_b.rename(columns={"close": "close"})
        return df_a, df_b

    def latest(self, symbol_a: str, symbol_b: str, params: Optional[Dict] = None) -> LiveSignal:
        """Return a LiveSignal for the pair.

        - Reuses `StatArbStrategy.signals_from_pair_prices()` to compute z/hedge/signal.
        - Sizes via the provided `account_rules` (PropFirmRiskGuard-like).
        - Honors account halt: if halted, returns `signal='HOLD'` and marks account_state.halted=True.
        """
        params = params or {}
        lookback = int(params.get("lookback", self.strategy.param_grid.get("lookback", 60)))

        # pull prices
        df_a, df_b = self._pull_pair_prices(symbol_a, symbol_b, lookback + 10)
        from pandas import DataFrame

        if df_a.empty or df_b.empty:
            now = datetime.utcnow()
            return LiveSignal(
                symbol_a=symbol_a,
                symbol_b=symbol_b,
                timestamp=now,
                z_score=None,
                signal="HOLD",
                hedge_ratio=None,
                entry_price_a=None,
                entry_price_b=None,
                suggested_notional=None,
                suggested_stop_pct=None,
                confidence=None,
                account_state={
                    "equity": None,
                    "daily_pnl": None,
                    "distance_to_daily_limit": None,
                    "distance_to_drawdown_floor": None,
                    "halted": getattr(self.account_rules, "halted", False),
                },
            )

        # merge & format into expected pair_df: timestamp, close_a, close_b
        pair_df = DataFrame()
        pair_df["timestamp"] = df_a["timestamp"]
        pair_df["close_a"] = df_a["close"].astype(float)
        pair_df = pair_df.merge(df_b[["timestamp", "close"]].rename(columns={"close": "close_b"}), on="timestamp", how="inner")

        # Use the strategy's core (StatArbStrategy) to compute the canonical signals
        core = self.strategy._core(params)
        result = core.signals_from_pair_prices(pair_df)

        # take last row
        last = result.iloc[-1]
        z = float(last.get("z_score")) if last.get("z_score") is not None else None
        hedge = float(last.get("hedge_ratio")) if last.get("hedge_ratio") is not None else None
        sig_int = int(last.get("signal")) if last.get("signal") is not None else 0
        sig_map = {1: "BUY", -1: "SELL", 0: "HOLD"}
        signal = sig_map.get(sig_int, "HOLD")
        entry_price_a = float(last.get("close_a")) if last.get("close_a") is not None else None
        entry_price_b = float(last.get("close_b")) if last.get("close_b") is not None else None

        # account state from provided guard
        acct_state = {}
        try:
            # Expect account_rules to provide current equity/daily_start_equity/peak_equity
            current_equity = getattr(self.account_rules, "current_equity", None)
            acct_check = self.account_rules.check(current_equity if current_equity is not None else 0.0)
            acct_state = {
                "equity": current_equity,
                "daily_pnl": getattr(self.account_rules, "daily_pnl", None),
                "distance_to_daily_limit": acct_check.get("distance_to_daily_limit"),
                "distance_to_drawdown_floor": acct_check.get("distance_to_drawdown_floor"),
                "halted": acct_check.get("halted", False),
            }
        except Exception:
            acct_state = {"equity": None, "daily_pnl": None, "distance_to_daily_limit": None, "distance_to_drawdown_floor": None, "halted": False}

        # if halted, override signal
        if acct_state.get("halted"):
            signal = "HOLD"

        # sizing: propose notional via account_rules.max_notional(current_equity)
        suggested_notional = None
        try:
            cur_eq = acct_state.get("equity") or 0.0
            max_not = float(self.account_rules.max_notional(cur_eq))
            use_kelly = bool(getattr(self.cfg, "use_kelly", False))
            if use_kelly and hasattr(core, "fixed_mean"):
                # derive kelly fraction based on last observations (best-effort)
                k = kelly_fraction(0.0, 1.0, half_kelly=True)  # placeholder call — real μ/σ unknown here
                suggested_notional = max_not * k
            else:
                suggested_notional = max_not
        except Exception:
            suggested_notional = None

        # confidence: |z| / entry_threshold clipped to [0,1]
        try:
            entry_z = float(params.get("entry_z", self.strategy.param_grid.get("entry_z", 2.0)))
            confidence = None
            if z is not None and entry_z > 0:
                confidence = min(1.0, abs(z) / float(entry_z))
            else:
                confidence = None
        except Exception:
            confidence = None

        now = datetime.utcnow()
        return LiveSignal(
            symbol_a=symbol_a,
            symbol_b=symbol_b,
            timestamp=now,
            z_score=z,
            signal=signal,
            hedge_ratio=hedge,
            entry_price_a=entry_price_a,
            entry_price_b=entry_price_b,
            suggested_notional=round(suggested_notional, 2) if suggested_notional is not None else None,
            suggested_stop_pct=float(params.get("suggested_stop_pct", 0.02)),
            confidence=confidence,
            account_state=acct_state,
        )
