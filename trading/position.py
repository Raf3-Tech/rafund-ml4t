"""Persistent position state for paper and live trading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


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

    def day_rolled(self) -> bool:
        """True if last_update was on a different calendar day than now (UTC)."""
        if self.last_update is None:
            return False
        now = datetime.now(timezone.utc)
        last = self.last_update
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last.date() < now.date()


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
            """
            SELECT run_id, strategy_name, symbol, exchange, side, qty, entry_price, entry_time,
                   equity, peak_equity, daily_start_equity, daily_halt, account_failed,
                   last_update
            FROM paper_positions
            WHERE run_id = %s
            """,
            (run_id,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        db.return_connection(conn)

    if row:
        return PositionState(
            run_id=row[0], strategy_name=row[1], symbol=row[2], exchange=row[3], side=row[4],
            qty=float(row[5]), entry_price=float(row[6]) if row[6] is not None else None,
            entry_time=row[7], equity=float(row[8]), peak_equity=float(row[9]),
            daily_start_equity=float(row[10]), daily_halt=bool(row[11]),
            account_failed=bool(row[12]), last_update=row[13],
        )
    return PositionState(
        run_id=run_id, strategy_name=strategy_name, symbol=symbol, exchange=exchange,
        equity=initial_capital, peak_equity=initial_capital,
        daily_start_equity=initial_capital,
    )


def save_position(db, pos: PositionState) -> None:
    """Upsert position state. Sets last_update = NOW() server-side."""
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO paper_positions
                (run_id, strategy_name, symbol, exchange, side, qty, entry_price, entry_time,
                 equity, peak_equity, daily_start_equity, daily_halt, account_failed,
                 last_update)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
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
                last_update        = NOW()
            """,
            (
                pos.run_id, pos.strategy_name, pos.symbol, pos.exchange, pos.side, pos.qty,
                pos.entry_price, pos.entry_time, pos.equity, pos.peak_equity,
                pos.daily_start_equity, pos.daily_halt, pos.account_failed,
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
) -> None:
    """Append one order event to paper_orders.

    setup_tag identifies why an OPEN was taken (e.g. "ema_crossover_buy");
    close_reason identifies why a CLOSE happened (e.g. "signal_exit",
    "rotation", "account_failed", "position_flip") — together these make up
    the trade journal so wins/losses can be traced back to a cause.
    """
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO paper_orders
                (run_id, strategy_name, symbol, exchange, event, side, qty, price, pnl,
                 commission, order_type, exchange_order_id, setup_tag, close_reason)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                pos.run_id, pos.strategy_name, pos.symbol, pos.exchange, event, pos.side,
                pos.qty, price, pnl, commission, order_type, exchange_order_id,
                setup_tag, close_reason,
            ),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)
