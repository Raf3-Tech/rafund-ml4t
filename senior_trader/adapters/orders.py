"""Read-only adapter onto paper_orders — recent automated (paper or CCXT
live) fills, for cross-referencing context only. Kraken Prop itself has no
order history to read here yet: nothing executes a Kraken Prop trade in
this codebase (state/repository_map.md Section 6), so there is no fill
data for it beyond what a later phase's trade_journal will hold. This
adapter is useful for a narrower question Senior Trader can honestly ask
today — e.g. "is this strategy/symbol already open as an automated paper
position elsewhere while I'm reviewing a manual Kraken Prop setup for it."
"""
from __future__ import annotations

from typing import List, Optional


def recent_fills(
    db,
    *,
    strategy_name: Optional[str] = None,
    symbol: Optional[str] = None,
    exchange: Optional[str] = None,
    limit: int = 20,
) -> List[dict]:
    clauses = []
    params: list = []
    if strategy_name is not None:
        clauses.append("strategy_name = %s")
        params.append(strategy_name)
    if symbol is not None:
        clauses.append("symbol = %s")
        params.append(symbol)
    if exchange is not None:
        clauses.append("exchange = %s")
        params.append(exchange)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    df = db.read_sql(
        f"""
        SELECT run_id, strategy_name, symbol, exchange, event, side, qty, price, pnl,
               commission, order_type, setup_tag, close_reason, created_at
        FROM paper_orders
        {where}
        ORDER BY created_at DESC
        LIMIT %s
        """,
        params=params,
    )
    return df.to_dict("records") if not df.empty else []
