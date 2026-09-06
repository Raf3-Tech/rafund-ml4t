"""Manual-execution signal detector — proposes limit-order setups for a human
to place by hand on the Kraken prop platform. Read-only against market data
and strategy/leaderboard/position state; writes only `pending_signals` rows.

This module has no order-placement capability, not even paper. It must never
import trading.execution, ccxt, or anything that talks to a Kraken order
endpoint.

Reuses (does not reimplement):
  - monitoring.leaderboard.build_leaderboard for the qualifying-strategy gate
  - trading.paper_trader._last_signal / _latest_bars for entry detection
  - trading.paper_trader._target_notional for position sizing (soft-floor
    halving + the dual gap-risk/per-trade-risk cap from trading.position)
  - trading.position.load_position / PositionState for account + halt state
  - strategies.base.BaseStrategy.get_stop_level for structural stops

Not reused (nothing exists yet to reuse):
  - take-profit level — derived as an R-multiple of the stop distance
  - a stop-distance fallback for strategies with no structural stop
    (BaseStrategy.get_stop_level returns None for everything but SMCBreakout)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import structlog

from strategies.base import BasePairsStrategy
from strategies.registry import StrategyRegistry
from trading.paper_trader import _last_signal, _latest_bars, _target_notional
from trading.position import load_position

logger = structlog.get_logger(__name__)

# Fallbacks used only when a strategy has no structural stop (get_stop_level
# returns None) — new, config-overridable, and separate from
# max_adverse_move_pct (that one models worst-case gap risk for sizing, not a
# level a human would actually place an order at).
_DEFAULT_STOP_PCT = 0.02
_DEFAULT_R_MULTIPLE = 2.0
_DEFAULT_EXPIRY_MINUTES = 30
_DEFAULT_STALE_MOVE_PCT = 0.01
_MIN_BARS_PADDING = 10


@dataclass
class PendingSignal:
    strategy_name: str
    symbol: str
    exchange: str
    direction: str  # LONG | SHORT
    limit_price: float
    stop_price: Optional[float]
    take_profit_price: Optional[float]
    position_size_usd: Optional[float]
    qty: Optional[float]
    leaderboard_score: float
    leaderboard_rank: int
    thesis: str
    source_timeframe: str
    expires_at: datetime


def _qualifying_single_symbol_candidates(db) -> List[tuple]:
    """Qualifying (score > 0, pass_ratio >= MIN_CONSISTENCY) single-symbol rows,
    best-per-(strategy, symbol) like trading.paper_trader._paper_candidates,
    but restricted to qualifies == True (that function deliberately runs
    everything, qualifying or not, to build paper history) and additionally
    scoped to symbols this exchange actually has price history for — checked
    later via _latest_bars rather than here, since the leaderboard carries no
    exchange column.

    Returns (rank, strategy_name, symbol, params, score) tuples.
    """
    from monitoring.leaderboard import build_leaderboard

    lb = build_leaderboard(db)
    if lb.empty:
        return []

    candidates = []
    seen: set = set()
    for rank, row in lb.iterrows():
        if not bool(row["qualifies"]):
            continue
        strategy_name = str(row["strategy_name"])
        symbol = str(row["symbol"])
        key = (strategy_name, symbol)
        if key in seen:
            continue
        seen.add(key)

        try:
            strategy = StrategyRegistry.instantiate(strategy_name)
        except KeyError:
            continue
        if isinstance(strategy, BasePairsStrategy):
            continue

        params = row["params"] if isinstance(row["params"], dict) else {}
        candidates.append((int(rank), strategy_name, symbol, params, float(row["score"])))

    return candidates


def _thesis(strategy_name: str, symbol: str, direction: str, rank: int, score: float,
            stop_price: float, has_structural_stop: bool) -> str:
    """One-line human-readable rationale. Called with an already-resolved
    stop_price (real or fallback) — always populated by _build_payload."""
    stop_note = "structural stop" if has_structural_stop else f"fallback {_DEFAULT_STOP_PCT:.0%} stop"
    return (
        f"{strategy_name} rank #{rank} (leaderboard score {score:.3f}) — {direction} "
        f"signal on {symbol}, {stop_note} at {stop_price:.4g}"
    )


def _build_payload(
    *, strategy_name: str, symbol: str, exchange: str, rank: int, score: float,
    signal: str, price: float, stop_price: Optional[float], source_timeframe: str,
    pos, cfg,
) -> PendingSignal:
    """Pure computation (no DB I/O) — split out so it's unit-testable without
    a database, mirroring the _target_notional / max_safe_notional tests."""
    direction = "LONG" if signal == "BUY" else "SHORT"
    has_structural_stop = stop_price is not None
    if stop_price is None:
        stop_pct = getattr(cfg, "pending_signal_default_stop_pct", _DEFAULT_STOP_PCT)
        stop_price = price * (1 - stop_pct) if direction == "LONG" else price * (1 + stop_pct)

    stop_distance = abs(price - stop_price)
    r_multiple = getattr(cfg, "pending_signal_r_multiple", _DEFAULT_R_MULTIPLE)
    take_profit = price + r_multiple * stop_distance if direction == "LONG" else price - r_multiple * stop_distance

    stop_distance_pct = stop_distance / price if price > 0 else None
    notional = _target_notional(pos, cfg, current_price=price, stop_price=stop_price)
    qty = notional / price if price > 0 else None

    expiry_minutes = getattr(cfg, "pending_signal_expiry_minutes", _DEFAULT_EXPIRY_MINUTES)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)

    return PendingSignal(
        strategy_name=strategy_name,
        symbol=symbol,
        exchange=exchange,
        direction=direction,
        limit_price=round(price, 8),
        stop_price=round(stop_price, 8),
        take_profit_price=round(take_profit, 8),
        position_size_usd=round(notional, 2) if notional is not None else None,
        qty=round(qty, 8) if qty is not None else None,
        leaderboard_score=round(score, 4),
        leaderboard_rank=rank,
        thesis=_thesis(strategy_name, symbol, direction, rank, score, stop_price, has_structural_stop),
        source_timeframe=source_timeframe,
        expires_at=expires_at,
    )


def scan_kraken_signals(db, exchange: str = "kraken") -> List[PendingSignal]:
    """One detector cycle: qualifying Kraken single-symbol strategies -> at
    most one new/refreshed pending_signals row per (strategy, symbol,
    direction) with an active BUY/SELL signal. Returns the signals written
    (empty if the account is halted or nothing qualifies)."""
    from config.loader import get_settings

    cfg = get_settings()

    pos = load_position(db, f"live_{exchange}", strategy_name="", symbol="", exchange=exchange)
    if pos.account_failed or pos.daily_halt or pos.manual_halt:
        reason = "account_failed (hard floor)" if pos.account_failed else (
            "daily_halt" if pos.daily_halt else "manual_halt"
        )
        logger.info("pending_signal_scan_suppressed", exchange=exchange, reason=reason)
        return []

    candidates = _qualifying_single_symbol_candidates(db)
    if not candidates:
        logger.info("pending_signal_scan_no_qualifying_candidates", exchange=exchange)
        return []

    written: List[PendingSignal] = []
    for rank, strategy_name, symbol, params, score in candidates:
        strategy = StrategyRegistry.instantiate(strategy_name)
        n_bars = strategy.get_min_bars(params) * 2 + _MIN_BARS_PADDING
        df = _latest_bars(db, symbol, n_bars, exchange=exchange, timeframe="1d")
        if df is None or len(df) < strategy.get_min_bars(params):
            continue  # no Kraken history for this symbol/timeframe combo

        signal = _last_signal(df, strategy_name, params)
        if signal not in ("BUY", "SELL"):
            continue

        price = float(df["close"].iloc[-1])
        stop_price = strategy.get_stop_level(df, params)

        payload = _build_payload(
            strategy_name=strategy_name, symbol=symbol, exchange=exchange, rank=rank,
            score=score, signal=signal, price=price, stop_price=stop_price,
            source_timeframe="1d", pos=pos, cfg=cfg,
        )
        _upsert_active_signal(db, payload)
        written.append(payload)

    return written


def _upsert_active_signal(db, sig: PendingSignal) -> None:
    """Insert a new active signal, or — if one already exists for this
    (strategy, symbol, direction) — just refresh its price/levels/expiry.
    Enforced by the partial unique index from migration 0017."""
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pending_signals
                (strategy_name, symbol, exchange, direction, limit_price, stop_price,
                 take_profit_price, position_size_usd, qty, leaderboard_score,
                 leaderboard_rank, thesis, source_timeframe, status, expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s)
            ON CONFLICT (strategy_name, symbol, direction) WHERE status = 'active'
            DO UPDATE SET
                limit_price       = EXCLUDED.limit_price,
                stop_price        = EXCLUDED.stop_price,
                take_profit_price = EXCLUDED.take_profit_price,
                position_size_usd = EXCLUDED.position_size_usd,
                qty               = EXCLUDED.qty,
                leaderboard_score = EXCLUDED.leaderboard_score,
                leaderboard_rank  = EXCLUDED.leaderboard_rank,
                thesis            = EXCLUDED.thesis,
                expires_at        = EXCLUDED.expires_at
            """,
            (
                sig.strategy_name, sig.symbol, sig.exchange, sig.direction, sig.limit_price,
                sig.stop_price, sig.take_profit_price, sig.position_size_usd, sig.qty,
                sig.leaderboard_score, sig.leaderboard_rank, sig.thesis, sig.source_timeframe,
                sig.expires_at,
            ),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)


def expire_stale_signals(db, exchange: str = "kraken") -> int:
    """Mark active signals expired (a) past expires_at, or (b) whose latest
    price has moved beyond pending_signal_stale_price_move_pct away from
    limit_price. Returns the number of rows expired. Call before scanning for
    new signals so a stale setup never blocks the idempotency slot for a
    fresh one."""
    from config.loader import get_settings

    cfg = get_settings()
    stale_pct = getattr(cfg, "pending_signal_stale_price_move_pct", _DEFAULT_STALE_MOVE_PCT)

    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, symbol, limit_price FROM pending_signals "
            "WHERE exchange = %s AND status = 'active' AND expires_at <= NOW()",
            (exchange,),
        )
        time_expired_ids = [row[0] for row in cur.fetchall()]

        cur.execute(
            "SELECT id, symbol, limit_price FROM pending_signals "
            "WHERE exchange = %s AND status = 'active' AND expires_at > NOW()",
            (exchange,),
        )
        still_active = cur.fetchall()
        cur.close()
    finally:
        db.return_connection(conn)

    price_expired_ids = []
    for sig_id, symbol, limit_price in still_active:
        df = _latest_bars(db, symbol, 1, exchange=exchange, timeframe="1d")
        if df is None or df.empty:
            continue
        current_price = float(df["close"].iloc[-1])
        if limit_price and abs(current_price - float(limit_price)) / float(limit_price) > stale_pct:
            price_expired_ids.append(sig_id)

    expired_ids = time_expired_ids + price_expired_ids
    if not expired_ids:
        return 0

    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pending_signals SET status = 'expired', resolved_at = NOW() WHERE id = ANY(%s)",
            (expired_ids,),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)

    return len(expired_ids)
