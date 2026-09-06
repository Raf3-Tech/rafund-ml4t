"""Per-instrument data-source provenance — Kraken Prop gate condition.

Kraken Prop trades manually against Kraken's own price feed; we never trade
an instrument on Kraken using another venue's price history, because the
basis between venues is exactly the kind of silent error that survives
backtesting and fails live. `prices` rows already carry an `exchange`
column per row, but nothing upstream of this module checked whether a
given symbol's rows all came from one exchange before treating that
symbol's backtest as representative of Kraken — `backtesting.window_engine`
pulls `db.get_prices(symbol, None, None)` with no exchange filter, so a
symbol like BTC/USDT can silently mix Binance and HTX bars into one series.

This module derives provenance from `prices` (not hand-maintained) and
persists it to `instrument_provenance` (migration 0020) so
monitoring.leaderboard can gate on it without re-deriving it from raw price
rows on every leaderboard build. Re-running the audit updates
`source_exchange`/`is_kraken_sourced` but never touches `prop_verified` —
that flag is set by hand from the Prop market selector and this module has
no opinion on it.
"""
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


def audit_instrument_provenance(db) -> pd.DataFrame:
    """Read-only: one row per symbol in `prices`, with every exchange that
    has contributed rows for it and whether that provenance is single-
    sourced. Does not touch the database."""
    df = db.read_sql(
        "SELECT symbol, exchange, COUNT(*) AS n_rows FROM prices GROUP BY symbol, exchange ORDER BY symbol, exchange"
    )
    if df.empty:
        return pd.DataFrame(columns=["symbol", "exchanges", "source_exchange", "is_kraken_sourced"])

    records = []
    for symbol, group in df.groupby("symbol"):
        exchanges = sorted(group["exchange"].tolist())
        single_sourced = len(exchanges) == 1
        source_exchange = exchanges[0] if single_sourced else None
        records.append({
            "symbol": symbol,
            "exchanges": exchanges,
            "source_exchange": source_exchange,
            "is_kraken_sourced": bool(single_sourced and source_exchange == "kraken"),
        })
    return pd.DataFrame(records)


def sync_instrument_provenance(db) -> int:
    """Upsert audit_instrument_provenance()'s findings into
    instrument_provenance. Never writes prop_verified — ON CONFLICT updates
    only source_exchange/is_kraken_sourced/last_audited_at, so a manually-set
    prop_verified survives re-audits. Returns the number of symbols synced."""
    audit = audit_instrument_provenance(db)
    if audit.empty:
        return 0

    conn = db.get_connection()
    try:
        cur = conn.cursor()
        for _, row in audit.iterrows():
            cur.execute(
                """
                INSERT INTO instrument_provenance (symbol, source_exchange, is_kraken_sourced, prop_verified)
                VALUES (%s, %s, %s, FALSE)
                ON CONFLICT (symbol) DO UPDATE SET
                    source_exchange   = EXCLUDED.source_exchange,
                    is_kraken_sourced = EXCLUDED.is_kraken_sourced,
                    last_audited_at   = NOW()
                """,
                (row["symbol"], row["source_exchange"], row["is_kraken_sourced"]),
            )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)

    logger.info("instrument_provenance_synced", n_symbols=len(audit))
    return len(audit)


def load_provenance_map(db) -> Dict[str, Dict[str, Optional[bool]]]:
    """{symbol: {"is_kraken_sourced": bool, "prop_verified": bool}} for
    every symbol in instrument_provenance — the lookup
    monitoring.leaderboard joins against. A symbol absent from this map
    (never audited/synced) must be treated as ineligible, not silently
    passed — "any instrument whose source cannot be established is
    ineligible.\""""
    df = db.read_sql("SELECT symbol, is_kraken_sourced, prop_verified FROM instrument_provenance")
    return {
        row["symbol"]: {
            "is_kraken_sourced": bool(row["is_kraken_sourced"]),
            "prop_verified": bool(row["prop_verified"]),
        }
        for _, row in df.iterrows()
    }
