"""CLI commands: data collection and backfill."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from config.logging_config import get_logger

logger = get_logger(__name__)


def collect_data(full_history: bool = True) -> bool:
    """Collect OHLCV from Binance into the prices table (incremental by default)."""
    logger.info("=" * 80)
    logger.info("STARTING DATA COLLECTION")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from config.loader import get_settings
        from data.collectors.binance_collector import BinanceCollector

        settings = get_settings()
        collector = BinanceCollector(testnet=False, rate_limit_ms=int(os.getenv("RATE_LIMIT_MS", 200)))
        db = get_db_connection()

        if not db.test_connection():
            logger.error("Database connection failed")
            return False

        symbols = settings.collect_symbols
        if not symbols:
            logger.error("No symbols configured for collection")
            db.close_pool()
            return False

        end_date = datetime.now(timezone.utc)
        default_start = datetime.strptime(settings.collect_start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        total_inserted = 0

        for symbol in symbols:
            try:
                latest = db.get_latest_timestamp(symbol)
                if full_history or latest is None:
                    start_date = default_start
                    logger.info(f"[{symbol}] Full history from {start_date.date()}")
                else:
                    start_date = latest + timedelta(days=1)
                    if start_date.date() >= end_date.date():
                        logger.info(f"[{symbol}] Already up to date (latest {latest.date()})")
                        continue
                    logger.info(f"[{symbol}] Incremental from {start_date.date()}")

                result = collector.collect_symbol(
                    symbol=symbol,
                    timeframe=settings.timeframe,
                    from_date=start_date,
                    to_date=end_date,
                )
                if not collector.last_collection_df.empty:
                    inserted = db.insert_prices(collector.last_collection_df)
                    total_inserted += inserted
                    logger.info(f"[OK] {symbol}: {inserted} new rows (fetched {result.records_fetched})")
                else:
                    logger.warning(f"[SKIP] {symbol}: No valid data to insert")

                if result.errors:
                    for err in result.errors:
                        logger.warning(err)
            except Exception as e:
                logger.error(f"[ERROR] {symbol}: {str(e)}", exc_info=True)

        stats = db.get_data_stats()
        logger.info(f"Total records: {stats.get('total_price_records', 0)}")
        logger.info(f"Rows inserted this run: {total_inserted}")
        db.close_pool()
        return True
    except Exception as e:
        logger.error(f"Data collection error: {str(e)}", exc_info=True)
        return False


def run_collect_funding_cmd() -> bool:
    """Collect 8-hour funding rate data from Binance perpetual futures."""
    logger.info("=" * 80)
    logger.info("COLLECTING FUNDING RATES")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from data.collectors.binance_funding_collector import BinanceFundingCollector

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        collector = BinanceFundingCollector(rate_limit_ms=int(os.getenv("RATE_LIMIT_MS", 200)))
        inserted = collector.collect_all(db)
        logger.info("Funding rate collection complete — %d rows inserted", inserted)
        db.close_pool()
        return True
    except Exception as e:
        logger.error("Funding rate collection error: %s", str(e), exc_info=True)
        return False


def run_backfill(
    symbol: Optional[str],
    timeframe: Optional[str],
    date_from: datetime,
    date_to: datetime,
    all_symbols: bool = False,
) -> bool:
    """Walk-forward backfill for a date range."""
    logger.info("=" * 80)
    logger.info("STARTING BACKFILL")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from config.loader import get_settings
        from data.collectors.binance_collector import BinanceCollector

        settings = get_settings()
        db = get_db_connection()

        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        if all_symbols:
            symbols = settings.collect_symbols
        elif symbol:
            symbols = [symbol]
        else:
            logger.error("Either --symbol or --all must be provided for backfill")
            db.close_pool()
            return False

        timeframe = timeframe or settings.timeframe
        if timeframe not in ("1m", "5m", "15m", "1h", "1d"):
            logger.error(f"Unsupported timeframe: {timeframe}")
            db.close_pool()
            return False

        collector = BinanceCollector(testnet=False, rate_limit_ms=int(os.getenv("RATE_LIMIT_MS", 200)))
        error_exit = False

        for sym in symbols:
            result = collector.collect_symbol(
                symbol=sym, timeframe=timeframe, from_date=date_from, to_date=date_to
            )
            inserted = 0
            if not collector.last_collection_df.empty:
                inserted = db.insert_prices(collector.last_collection_df)

            print("=" * 72)
            print(f"Symbol: {sym}")
            print(f"Timeframe: {timeframe}")
            print(f"Range: {date_from.date()} to {date_to.date()}")
            print(f"Fetched: {result.records_fetched}  Inserted: {inserted}  Rejected: {result.records_rejected}")
            print("=" * 72)

            if result.records_rejected > 0:
                error_exit = True
            for item in (result.errors or []):
                logger.warning(item)

        db.close_pool()
        return not error_exit
    except Exception as e:
        logger.error(f"Backfill error: {str(e)}", exc_info=True)
        return False
