"""One-time (and re-runnable, if more Kraken history is ingested later)
rebuild of the Kraken Prop track record — deletes the contaminated legacy
EXPANDING engine_results rows for Kraken-sourced symbols and replaces them
with a fixed, non-overlapping window scheme.

Background (AGENTS.md, "Track Record Depth" session, 2026-09-06): every
Kraken symbol's reported T=32-33 windows resolved to only 8 distinct
(window_start, window_end) pairs, all EXPANDING and sharing the same start
— an artifact of ~16 repeated `python main.py engine` invocations over
calendar time, not independent observations. This script replaces those
rows with backtesting.window_engine.generate_kraken_track_record_windows's
non-overlapping partition (currently 3 windows across 798 days of history).

Computes new results FIRST, entirely in memory — nothing is deleted unless
the new run actually produces results, so a failure here never leaves the
table in a worse state than before.

Usage: python scripts/rebuild_kraken_track_record.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys

import structlog

from backtesting.window_engine import WalkForwardWindowEngine
from cli.db import get_db_connection
from data.provenance import audit_instrument_provenance
from strategies.registry import StrategyRegistry
import strategies  # noqa: F401 — populates StrategyRegistry

logger = structlog.get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Compute and report, write nothing.")
    args = parser.parse_args()

    db = get_db_connection()
    if not db.test_connection():
        logger.error("Database connection failed")
        return 1

    provenance = audit_instrument_provenance(db)
    kraken_symbols = sorted(provenance[provenance["is_kraken_sourced"]]["symbol"].tolist())
    if not kraken_symbols:
        logger.error("No Kraken-sourced symbols found — nothing to rebuild.")
        db.close_pool()
        return 1
    logger.info("kraken_symbols", symbols=kraken_symbols)

    strategies_list = StrategyRegistry.instantiate_all()
    single_leg = [s for s in strategies_list if s.leg_count == 1]
    logger.info("single_leg_strategies", names=[s.name for s in single_leg], excluded_multi_leg=[
        s.name for s in strategies_list if s.leg_count != 1
    ])

    engine = WalkForwardWindowEngine(db=db, strategies=single_leg, symbols=kraken_symbols)
    new_results = engine.run_kraken_track_record_windows(single_leg, kraken_symbols)

    if not new_results:
        logger.error("New window run produced zero results — aborting, nothing deleted.")
        db.close_pool()
        return 1

    by_strategy_symbol = {}
    for r in new_results:
        key = (r.strategy_name, r.symbol)
        by_strategy_symbol.setdefault(key, []).append(r)
    logger.info(
        "new_results_summary",
        n_results=len(new_results),
        n_strategy_symbol_combos=len(by_strategy_symbol),
        windows_per_combo=sorted({len(v) for v in by_strategy_symbol.values()}),
    )

    if args.dry_run:
        logger.info("--dry-run: not deleting or writing anything.")
        db.close_pool()
        return 0

    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM engine_results WHERE symbol = ANY(%s) AND window_type = 'EXPANDING'",
            (kraken_symbols,),
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)
    logger.info("deleted_legacy_rows", n=deleted)

    engine._persist_results(new_results)
    logger.info("rebuild_complete", deleted=deleted, inserted=len(new_results))
    db.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
