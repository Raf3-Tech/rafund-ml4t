"""Persistent position state for paper and live trading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class PositionState:
    run_id: str
    strategy_name: str
    symbol: str
    exchange: str = "binance"
    side: str = "FLAT"           # FLAT | LONG | SHORT
    qty: float = 0.0
    entry_price: Optional[float] = None
    entry_time: Optional[datetime] = None
    equity: float = 5000.0       # realised equity (updated on close)
    peak_equity: float = 5000.0
    daily_start_equity: float = 5000.0
    daily_halt: bool = False
    account_failed: bool = False
    manual_halt: bool = False
    stop_price: Optional[float] = None  # structural stop (e.g. SMCBreakout's order block edge); None = no stop
    last_update: Optional[datetime] = None

    @property
    def is_flat(self) -> bool:
        return self.side == "FLAT" or self.qty == 0.0

    def mark_to_market(self, current_price: float) -> float:
        """Total equity including unrealised P&L."""
        if self.is_flat or self.entry_price is None or self.entry_price == 0:
            return self.equity
        if self.side == "LONG":
            return self.equity + self.qty * (current_price - self.entry_price)
        return self.equity - self.qty * (current_price - self.entry_price)

    def day_rolled(self, as_of: Optional[datetime] = None) -> bool:
        """True if last_update was on a different calendar day than `as_of`
        (UTC). `as_of` defaults to now — pass the bar's own timestamp during
        historical replay, where "now" has no meaning."""
        if self.last_update is None:
            return False
        now = as_of if as_of is not None else datetime.now(timezone.utc)
        last = self.last_update
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last.date() < now.date()


def max_safe_notional(pos: PositionState, cfg) -> float:
    """Cap a new position so a `cfg.max_adverse_move_pct` move right after
    entry burns at most `cfg.risk_buffer_pct` of the remaining headroom to
    either the daily-loss or drawdown floor — whichever is tighter.

    Without this, position sizing (`leg_allocation_pct`) is blind to how
    close equity already is to a hard limit and to single-bar gap risk, so a
    flash-crash-sized move can blow straight through a prop-firm floor in
    one bar (observed in stress testing: -7.1% in one bar vs. a 6% floor).
    """
    dd_floor = cfg.account_size * (1.0 - cfg.max_drawdown_pct)
    dd_headroom = max(0.0, pos.equity - dd_floor)

    daily_floor = pos.daily_start_equity - cfg.account_size * cfg.max_daily_loss_pct
    daily_headroom = max(0.0, pos.equity - daily_floor)

    headroom = min(dd_headroom, daily_headroom)
    return headroom * cfg.risk_buffer_pct / cfg.max_adverse_move_pct


_POSITION_COLUMNS = """run_id, strategy_name, symbol, exchange, side, qty, entry_price, entry_time,
                   equity, peak_equity, daily_start_equity, daily_halt, account_failed,
                   manual_halt, stop_price, last_update"""


def _row_to_position(row) -> PositionState:
    return PositionState(
        run_id=row[0], strategy_name=row[1], symbol=row[2], exchange=row[3], side=row[4],
        qty=float(row[5]), entry_price=float(row[6]) if row[6] is not None else None,
        entry_time=row[7], equity=float(row[8]), peak_equity=float(row[9]),
        daily_start_equity=float(row[10]), daily_halt=bool(row[11]),
        account_failed=bool(row[12]), manual_halt=bool(row[13]),
        stop_price=float(row[14]) if row[14] is not None else None, last_update=row[15],
    )


def load_position(
    db,
    run_id: str,
    *,
    initial_capital: float = 5000.0,
    strategy_name: str = "",
    symbol: str = "",
    exchange: str = "binance",
) -> PositionState:
    """Load from DB; returns a fresh state if the run_id has no record yet."""
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_POSITION_COLUMNS} FROM paper_positions WHERE run_id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        db.return_connection(conn)

    if row:
        return _row_to_position(row)
    return PositionState(
        run_id=run_id, strategy_name=strategy_name, symbol=symbol, exchange=exchange,
        equity=initial_capital, peak_equity=initial_capital,
        daily_start_equity=initial_capital,
    )


def list_positions(db, exchange: str, run_id_prefix: str) -> List[PositionState]:
    """All persisted positions for one exchange whose run_id starts with the given prefix.

    Used by the paper-trading multi-slot model (one position per
    strategy-symbol candidate) to enumerate every currently-open slot for an
    exchange, e.g. for kill-switch/resume or the dashboard's position list.
    """
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_POSITION_COLUMNS} FROM paper_positions "
            "WHERE exchange = %s AND run_id LIKE %s ORDER BY run_id",
            (exchange, f"{run_id_prefix}%"),
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        db.return_connection(conn)
    return [_row_to_position(row) for row in rows]


def save_position(db, pos: PositionState, last_update: Optional[datetime] = None) -> None:
    """Upsert position state. `last_update` defaults to NOW() server-side;
    pass the last historical bar's timestamp when finishing a backfill
    replay so the next live cycle correctly sees day_rolled() == True and
    rolls cleanly into the present."""
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO paper_positions
                (run_id, strategy_name, symbol, exchange, side, qty, entry_price, entry_time,
                 equity, peak_equity, daily_start_equity, daily_halt, account_failed,
                 manual_halt, stop_price, last_update)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, COALESCE(%s, NOW()))
            ON CONFLICT (run_id) DO UPDATE SET
                strategy_name      = EXCLUDED.strategy_name,
                symbol             = EXCLUDED.symbol,
                exchange           = EXCLUDED.exchange,
                side               = EXCLUDED.side,
                qty                = EXCLUDED.qty,
                entry_price        = EXCLUDED.entry_price,
                entry_time         = EXCLUDED.entry_time,
                equity             = EXCLUDED.equity,
                peak_equity        = EXCLUDED.peak_equity,
                daily_start_equity = EXCLUDED.daily_start_equity,
                daily_halt         = EXCLUDED.daily_halt,
                account_failed     = EXCLUDED.account_failed,
                manual_halt        = EXCLUDED.manual_halt,
                stop_price         = EXCLUDED.stop_price,
                last_update        = EXCLUDED.last_update
            """,
            (
                pos.run_id, pos.strategy_name, pos.symbol, pos.exchange, pos.side, pos.qty,
                pos.entry_price, pos.entry_time, pos.equity, pos.peak_equity,
                pos.daily_start_equity, pos.daily_halt, pos.account_failed, pos.manual_halt,
                pos.stop_price, last_update,
            ),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)


def log_order(
    db,
    pos: PositionState,
    event: str,
    price: float,
    *,
    pnl: Optional[float] = None,
    commission: float = 0.0,
    order_type: str = "paper",
    exchange_order_id: Optional[str] = None,
    setup_tag: Optional[str] = None,
    close_reason: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> None:
    """Append one order event to paper_orders.

    setup_tag identifies why an OPEN was taken (e.g. "ema_crossover_buy");
    close_reason identifies why a CLOSE happened (e.g. "signal_exit",
    "rotation", "account_failed", "position_flip") — together these make up
    the trade journal so wins/losses can be traced back to a cause.

    `created_at` defaults to NOW() server-side; pass the historical bar's
    timestamp during backfill replay so journal entries land on the date
    the trade actually happened, not the date the replay ran.
    """
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO paper_orders
                (run_id, strategy_name, symbol, exchange, event, side, qty, price, pnl,
                 commission, order_type, exchange_order_id, setup_tag, close_reason, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, COALESCE(%s, NOW()))
            """,
            (
                pos.run_id, pos.strategy_name, pos.symbol, pos.exchange, event, pos.side,
                pos.qty, price, pnl, commission, order_type, exchange_order_id,
                setup_tag, close_reason, created_at,
            ),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)
