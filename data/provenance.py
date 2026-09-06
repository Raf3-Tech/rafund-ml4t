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


def backfill_engine_results_exchange(db) -> Dict[str, int]:
    """One-time backfill for engine_results rows persisted before migration
    0021 added the exchange columns (all NULL/'unknown' by default).

    Only sets exchange_provenance = 'inferred_from_symbol' where current
    instrument_provenance genuinely establishes a single source for that
    row's symbol — this is inferred backward from today's provenance state,
    not a per-row fact recorded at insert time, which is exactly why it's
    not 'verified'. Everything else (ambiguous or unestablished provenance)
    is left as 'unknown' — no confident value is invented for it.

    Pairs symbols ("A|B") are backfilled only when BOTH legs resolve to a
    single exchange; exchange is recorded as "A+B" when the legs differ,
    or the shared exchange when they match.

    Returns {"single_leg_backfilled": n, "pairs_backfilled": n}.
    """
    audit = audit_instrument_provenance(db)
    single_source = {
        row["symbol"]: row["source_exchange"]
        for _, row in audit.iterrows()
        if row["source_exchange"] is not None
    }

    conn = db.get_connection()
    counts = {"single_leg_backfilled": 0, "pairs_backfilled": 0}
    try:
        cur = conn.cursor()

        for symbol, exchange in single_source.items():
            cur.execute(
                """
                UPDATE engine_results SET exchange = %s, exchange_provenance = 'inferred_from_symbol'
                WHERE symbol = %s AND exchange_provenance = 'unknown'
                """,
                (exchange, symbol),
            )
            counts["single_leg_backfilled"] += cur.rowcount

        cur.execute(
            "SELECT DISTINCT symbol FROM engine_results WHERE symbol LIKE '%%|%%' AND exchange_provenance = 'unknown'"
        )
        pair_symbols = [row[0] for row in cur.fetchall()]
        for pair_symbol in pair_symbols:
            legs = pair_symbol.split("|")
            if len(legs) != 2:
                continue
            ex_a, ex_b = single_source.get(legs[0]), single_source.get(legs[1])
            if ex_a is None or ex_b is None:
                continue
            exchange = ex_a if ex_a == ex_b else f"{ex_a}+{ex_b}"
            cur.execute(
                """
                UPDATE engine_results SET exchange = %s, exchange_provenance = 'inferred_from_symbol'
                WHERE symbol = %s AND exchange_provenance = 'unknown'
                """,
                (exchange, pair_symbol),
            )
            counts["pairs_backfilled"] += cur.rowcount

        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)

    logger.info("engine_results_exchange_backfilled", **counts)
    return counts


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
