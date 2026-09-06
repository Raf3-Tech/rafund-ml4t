"""Manual-execution signal detector — proposes limit-order setups for a human
to place by hand on the Kraken prop platform. Read-only against market data
and strategy/leaderboard/position state; writes only `pending_signals` rows.

This module has no order-placement capability, not even paper. It must never
import trading.execution, ccxt, or anything that talks to a Kraken order
endpoint.

Reuses (does not reimplement):
  - monitoring.leaderboard.build_leaderboard for the qualifying-strategy gate
  - trading.paper_trader._last_signal / _latest_bars for entry detection
  - trading.position.load_position / PositionState for account + halt state
  - strategies.base.BaseStrategy.get_stop_level for structural stops
  - strategies.setup for risk-based sizing / cost / R-multiple math (Kraken
    Prop's "survival, not returns" formula: size is DERIVED from
    equity*risk_pct/stop_distance, never chosen or allocation-derived)
  - risk.pretrade_gate.evaluate as the ONLY gate to 'active' status — every
    candidate is evaluated and persisted via risk.pretrade_gate.
    persist_decision, whether approved or not (scan_kraken_signals is the
    single call site; see tests/test_pending_signal_detector.py's
    test_scan_kraken_signals_rejected_setup_is_never_persisted_as_active)

Deliberately does NOT reuse trading.paper_trader._target_notional for sizing
(as an earlier version of this module did) — that function's leg-allocation
+ headroom-cap sizing is the right tool for a diversified book of many
simultaneously-open *automatic* paper positions across every exchange, not
one manually-placed Kraken Prop setup sized purely off its own stop
distance. See AGENTS.md's KRAKEN PROP GAP BACKLOG for the full rationale.
The old headroom/gap-risk protection this gave up moves to risk/
pretrade_gate.py (a later phase): REJECT the whole setup if it doesn't fit
remaining room, rather than silently shrinking its notional.

`pos.equity` (realized only, no unrealized P&L or accrued funding netted in)
is used as the equity input to sizing — not the fuller risk.prop_account.
PropAccountState.equity, which needs a live funding-accrual feed and
rollover-state persistence that don't exist yet. Swap this for
PropAccountState.equity once risk/pretrade_gate.py (which needs that live
feed anyway) exists.

Not reused (nothing exists yet to reuse):
  - a stop-distance fallback for strategies with no structural stop
    (BaseStrategy.get_stop_level returns None for everything but SMCBreakout)
  - real historical average-hold-time data (expected_hold_hours is a
    documented default, not a measured figure — see _DEFAULT_EXPECTED_HOLD_HOURS)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

import structlog

from risk.pretrade_gate import APPROVE, REDUCE, GateContext, evaluate, persist_decision
from risk.prop_account import from_position_state
from strategies.base import BasePairsStrategy
from strategies.registry import StrategyRegistry
from strategies.setup import (
    DEFAULT_RISK_PCT,
    derive_expected_cost,
    derive_leverage_required,
    derive_notional,
    derive_r_multiple,
    derive_size,
    derive_worst_case_loss,
)
from trading.paper_trader import _last_signal, _latest_bars
from trading.position import load_position

logger = structlog.get_logger(__name__)

# Fallbacks used only when a strategy has no structural stop (get_stop_level
# returns None) — new, config-overridable, and separate from
# max_adverse_move_pct (that one models worst-case gap risk for sizing, not a
# level a human would actually place an order at).
_DEFAULT_STOP_PCT = Decimal("0.02")
_DEFAULT_R_MULTIPLE = Decimal("2.0")
_DEFAULT_EXPIRY_MINUTES = 30
_DEFAULT_STALE_MOVE_PCT = 0.01
_DEFAULT_EXPECTED_HOLD_HOURS = Decimal("24")  # documented default, not measured — see module docstring
_MIN_BARS_PADDING = 10

# Kraken Prop's actual lifetime MDD room is 3-6% depending on plan tier —
# this is a documented placeholder (the 3% end, conservative: assuming less
# room than you actually have is fail-safe; assuming more is not). Must be
# set to the real account's tier via cfg.kraken_prop_mdd_pct before this
# gate protects a live account.
_DEFAULT_KRAKEN_MDD_PCT = Decimal("0.03")

# Every pending_signals column backed by a Decimal PendingSignal field
# (see migration 0018) — the single list both monitoring.routes.trading_routes
# and monitoring.routes.publish convert to float at their JSON boundary, so
# there's one place that knows which columns need the conversion.
DECIMAL_COLUMNS = (
    "limit_price", "stop_price", "take_profit_price", "position_size_usd", "qty",
    "expected_cost", "worst_case_loss", "r_multiple", "leverage_required",
    "expected_hold_hours",
)


def decimal_columns_to_float(df):
    """In-place: NUMERIC columns come back from read_sql as Decimal (object
    dtype) — Flask's default JSON encoder would serialize Decimal as a
    *string*, silently breaking any consumer doing float-only arithmetic on
    these fields (e.g. the dashboard's .toFixed()/.toLocaleString() calls).
    Decimal precision matters internally; at the JSON boundary it's display
    data, so convert explicitly. Returns df for chaining."""
    import pandas as pd

    for col in DECIMAL_COLUMNS:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: float(x) if pd.notnull(x) else None)
    return df

_PRICE_Q = Decimal("0.00000001")  # 8 dp — prices and qty
_USD_Q = Decimal("0.01")          # 2 dp — dollar amounts
_RATIO_Q = Decimal("0.0001")      # 4 dp — leverage_required, r_multiple


@dataclass
class PendingSignal:
    strategy_name: str
    symbol: str
    exchange: str
    direction: str  # LONG | SHORT
    limit_price: Decimal
    stop_price: Optional[Decimal]
    take_profit_price: Optional[Decimal]
    position_size_usd: Optional[Decimal]
    qty: Optional[Decimal]
    leaderboard_score: float
    leaderboard_rank: int
    thesis: str
    source_timeframe: str
    expires_at: datetime
    expected_cost: Decimal
    worst_case_loss: Decimal
    r_multiple: Decimal
    leverage_required: Decimal
    expected_hold_hours: Decimal
    generated_at: datetime


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
            stop_price: Decimal, has_structural_stop: bool) -> str:
    """One-line human-readable rationale. Called with an already-resolved
    stop_price (real or fallback) — always populated by _build_payload."""
    stop_note = "structural stop" if has_structural_stop else f"fallback {_DEFAULT_STOP_PCT:.0%} stop"
    return (
        f"{strategy_name} rank #{rank} (leaderboard score {score:.3f}) — {direction} "
        f"signal on {symbol}, {stop_note} at {stop_price:.4g}"
    )


def _build_payload(
    *, strategy_name: str, symbol: str, exchange: str, rank: int, score: float,
    signal: str, price: Decimal, stop_price: Optional[Decimal], source_timeframe: str,
    pos, cfg,
) -> PendingSignal:
    """Pure computation (no DB I/O) — split out so it's unit-testable without
    a database, mirroring the strategies/setup.py formula tests."""
    direction = "LONG" if signal == "BUY" else "SHORT"
    has_structural_stop = stop_price is not None
    if stop_price is None:
        stop_pct = Decimal(str(getattr(cfg, "pending_signal_default_stop_pct", _DEFAULT_STOP_PCT)))
        stop_price = price * (1 - stop_pct) if direction == "LONG" else price * (1 + stop_pct)

    stop_distance = abs(price - stop_price)
    r_multiple_target = Decimal(str(getattr(cfg, "pending_signal_r_multiple", _DEFAULT_R_MULTIPLE)))
    take_profit = price + r_multiple_target * stop_distance if direction == "LONG" else price - r_multiple_target * stop_distance

    # Sizing: risk-based, never allocation-based — see strategies/setup.py and
    # the module docstring for why pos.equity (realized only) is the input.
    equity = Decimal(str(pos.equity))
    risk_pct = Decimal(str(getattr(cfg, "pending_signal_risk_pct", DEFAULT_RISK_PCT)))
    size = derive_size(equity, price, stop_price, risk_pct=risk_pct)
    notional = derive_notional(size, price)
    leverage_required = derive_leverage_required(notional, equity)

    expected_hold_hours = Decimal(str(getattr(cfg, "pending_signal_expected_hold_hours", _DEFAULT_EXPECTED_HOLD_HOURS)))
    expected_cost = derive_expected_cost(notional, expected_hold_hours)
    worst_case_loss = derive_worst_case_loss(size, price, stop_price, expected_cost)
    r_multiple = derive_r_multiple(price, stop_price, take_profit, size, expected_cost)

    expiry_minutes = getattr(cfg, "pending_signal_expiry_minutes", _DEFAULT_EXPIRY_MINUTES)
    generated_at = datetime.now(timezone.utc)
    expires_at = generated_at + timedelta(minutes=expiry_minutes)

    return PendingSignal(
        strategy_name=strategy_name,
        symbol=symbol,
        exchange=exchange,
        direction=direction,
        limit_price=price.quantize(_PRICE_Q),
        stop_price=stop_price.quantize(_PRICE_Q),
        take_profit_price=take_profit.quantize(_PRICE_Q),
        position_size_usd=notional.quantize(_USD_Q),
        qty=size.quantize(_PRICE_Q),
        leaderboard_score=round(score, 4),
        leaderboard_rank=rank,
        thesis=_thesis(strategy_name, symbol, direction, rank, score, stop_price, has_structural_stop),
        source_timeframe=source_timeframe,
        expires_at=expires_at,
        expected_cost=expected_cost.quantize(_USD_Q),
        worst_case_loss=worst_case_loss.quantize(_USD_Q),
        r_multiple=r_multiple.quantize(_RATIO_Q),
        leverage_required=leverage_required.quantize(_RATIO_Q),
        expected_hold_hours=expected_hold_hours,
        generated_at=generated_at,
    )


def current_account_state(pos, cfg):
    """The single authority for building a live risk.prop_account.
    PropAccountState from an already-loaded PositionState — shared by
    scan_kraken_signals (the gate) and monitoring.routes.publish (the
    tablet's room-remaining figures), so both see identical numbers.

    kraken_prop_mdd_pct defaults to the conservative end of Kraken's 3-6%
    tier range; last_rollover is a same-cycle placeholder since no live feed
    persists real rollover-clock state yet (risk/daily_clock.py exists but
    isn't wired to anything that calls it on a schedule) — see AGENTS.md's
    KRAKEN PROP GAP BACKLOG for what's still needed before this is
    live-accurate."""
    kraken_mdd_pct = Decimal(str(getattr(cfg, "kraken_prop_mdd_pct", _DEFAULT_KRAKEN_MDD_PCT)))
    return from_position_state(
        pos, kraken_mdd_pct=kraken_mdd_pct, last_rollover=datetime.now(timezone.utc),
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

    account_state = current_account_state(pos, cfg)

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

        price = Decimal(str(df["close"].iloc[-1]))
        stop_level = strategy.get_stop_level(df, params)
        stop_price = Decimal(str(stop_level)) if stop_level is not None else None

        payload = _build_payload(
            strategy_name=strategy_name, symbol=symbol, exchange=exchange, rank=rank,
            score=score, signal=signal, price=price, stop_price=stop_price,
            source_timeframe="1d", pos=pos, cfg=cfg,
        )

        # The gate is the only path to a live setup — every candidate is
        # evaluated and every decision persisted, whether or not it's ever
        # written as an active signal.
        context = GateContext()
        decision = evaluate(payload, account_state, context)
        persist_decision(db, payload, account_state, context, decision)
        if decision.action not in (APPROVE, REDUCE):
            logger.info(
                "pending_signal_rejected_by_gate", strategy_name=strategy_name,
                symbol=symbol, reason=decision.reason_code,
            )
            continue

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
                 leaderboard_rank, thesis, source_timeframe, status, expires_at,
                 expected_cost, worst_case_loss, r_multiple, leverage_required,
                 expected_hold_hours)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s)
            ON CONFLICT (strategy_name, symbol, direction) WHERE status = 'active'
            DO UPDATE SET
                limit_price        = EXCLUDED.limit_price,
                stop_price         = EXCLUDED.stop_price,
                take_profit_price  = EXCLUDED.take_profit_price,
                position_size_usd  = EXCLUDED.position_size_usd,
                qty                = EXCLUDED.qty,
                leaderboard_score  = EXCLUDED.leaderboard_score,
                leaderboard_rank   = EXCLUDED.leaderboard_rank,
                thesis             = EXCLUDED.thesis,
                expires_at         = EXCLUDED.expires_at,
                expected_cost      = EXCLUDED.expected_cost,
                worst_case_loss    = EXCLUDED.worst_case_loss,
                r_multiple         = EXCLUDED.r_multiple,
                leverage_required  = EXCLUDED.leverage_required,
                expected_hold_hours = EXCLUDED.expected_hold_hours
            """,
            (
                sig.strategy_name, sig.symbol, sig.exchange, sig.direction, sig.limit_price,
                sig.stop_price, sig.take_profit_price, sig.position_size_usd, sig.qty,
                sig.leaderboard_score, sig.leaderboard_rank, sig.thesis, sig.source_timeframe,
                sig.expires_at, sig.expected_cost, sig.worst_case_loss, sig.r_multiple,
                sig.leverage_required, sig.expected_hold_hours,
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
