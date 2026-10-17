"""CLI commands: data collection and backfill."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from config.logging_config import get_logger

logger = get_logger(__name__)


# Timeframes the exchanges don't serve natively are built from a finer series
# already in the DB. Kraken, Binance and HTX all lack 3h.
DERIVED_TIMEFRAMES = {"3h": ("1h", 3)}

_TF_DELTA = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


def _drop_open_candle(df, timeframe: str, now: datetime):
    """Remove bars whose period hasn't closed yet. Inserts are ON CONFLICT DO
    NOTHING, so a half-formed bar stored mid-period would never be corrected."""
    if df.empty:
        return df
    ts = df["timestamp"]
    closes = ts + _TF_DELTA[timeframe]
    return df[closes <= now]


def _derive_timeframe(db, symbol: str, exchange: str, timeframe: str) -> int:
    """Build `timeframe` bars from the finer source series, epoch-aligned in UTC,
    keeping only buckets that contain every source bar (no partial 3h bars)."""
    import pandas as pd

    source_tf, n = DERIVED_TIMEFRAMES[timeframe]
    latest = db.get_latest_timestamp(symbol, exchange=exchange, timeframe=timeframe)
    src = db.get_prices(symbol, exchange=exchange, timeframe=source_tf, start_date=latest)
    if src.empty:
        return 0
    src = src.copy()
    src["timestamp"] = pd.to_datetime(src["timestamp"])
    src = src.set_index("timestamp").sort_index()
    agg = src.resample(timeframe, origin="epoch").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    counts = src["close"].resample(timeframe, origin="epoch").count()
    agg = agg[counts == n].dropna().reset_index()
    if agg.empty:
        return 0
    agg["symbol"] = symbol
    agg["timeframe"] = timeframe
    return db.insert_prices(agg, exchange=exchange)


def _collect_exchange(exchange: str, collector, symbols: list[str], full_history: bool) -> bool:
    """Collect every configured timeframe for every symbol on one exchange."""
    import pandas as pd

    from cli.db import get_db_connection
    from config.loader import get_settings

    settings = get_settings()
    db = get_db_connection()
    if not db.test_connection():
        logger.error("Database connection failed")
        return False
    if not symbols:
        logger.error(f"No symbols configured for {exchange}")
        db.close_pool()
        return False

    timeframes = list(settings.timeframes)
    native = [tf for tf in timeframes if tf not in DERIVED_TIMEFRAMES]
    derived = [tf for tf in timeframes if tf in DERIVED_TIMEFRAMES]
    # A derived frame needs its source collected first, even if not listed.
    for tf in derived:
        src = DERIVED_TIMEFRAMES[tf][0]
        if src not in native:
            native.insert(0, src)

    end_date = datetime.now(timezone.utc)
    default_start = datetime.strptime(settings.collect_start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    total_inserted = 0
    failures = 0

    for symbol in symbols:
        for timeframe in native:
            try:
                latest = db.get_latest_timestamp(symbol, exchange=exchange, timeframe=timeframe)
                if full_history or latest is None:
                    start_date = default_start
                    logger.info(f"[{exchange} {symbol} {timeframe}] Full history from {start_date.date()}")
                else:
                    # Re-request from the last stored bar; ON CONFLICT skips it.
                    start_date = latest.replace(tzinfo=timezone.utc)
                    if start_date + 2 * _TF_DELTA[timeframe] > end_date:
                        logger.info(f"[{exchange} {symbol} {timeframe}] Already up to date (latest {latest})")
                        continue
                    logger.info(f"[{exchange} {symbol} {timeframe}] Incremental from {start_date}")

                result = collector.collect_symbol(
                    symbol=symbol, timeframe=timeframe, from_date=start_date, to_date=end_date,
                )
                df = collector.last_collection_df
                if not df.empty:
                    df = df.copy()
                    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                    df = _drop_open_candle(df, timeframe, end_date)
                if not df.empty:
                    inserted = db.insert_prices(df, exchange=exchange)
                    total_inserted += inserted
                    logger.info(f"[OK] {exchange} {symbol} {timeframe}: {inserted} new rows (fetched {result.records_fetched})")
                else:
                    logger.warning(f"[SKIP] {exchange} {symbol} {timeframe}: No closed bars to insert")
                for err in result.errors:
                    logger.warning(err)
            except Exception as e:
                failures += 1
                logger.error(f"[ERROR] {exchange} {symbol} {timeframe}: {str(e)}", exc_info=True)

        for timeframe in derived:
            try:
                inserted = _derive_timeframe(db, symbol, exchange, timeframe)
                total_inserted += inserted
                logger.info(f"[OK] {exchange} {symbol} {timeframe} (derived): {inserted} new rows")
            except Exception as e:
                failures += 1
                logger.error(f"[ERROR] {exchange} {symbol} {timeframe} (derived): {str(e)}", exc_info=True)

    stats = db.get_data_stats()
    logger.info(f"Total records: {stats.get('total_price_records', 0)}")
    logger.info(f"{exchange} rows inserted this run: {total_inserted} ({failures} failures)")
    db.close_pool()
    return failures == 0


def collect_data(full_history: bool = False) -> bool:
    """Collect OHLCV from Binance into the prices table (incremental by default)."""
    logger.info("=" * 80)
    logger.info("STARTING DATA COLLECTION")
    logger.info("=" * 80)
    try:
        from config.loader import get_settings
        from data.collectors.binance_collector import BinanceCollector

        collector = BinanceCollector(testnet=False, rate_limit_ms=int(os.getenv("RATE_LIMIT_MS", 200)))
        return _collect_exchange("binance", collector, get_settings().collect_symbols, full_history)
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


def collect_kraken_data(full_history: bool = False) -> bool:
    """Collect OHLCV from Kraken into the prices table (incremental by default).

    Kraken's public OHLC endpoint only serves the most recent 720 bars per
    timeframe, so 1h/4h history is ~30/120 days deep; it grows from here on.
    """
    logger.info("=" * 80)
    logger.info("STARTING KRAKEN DATA COLLECTION")
    logger.info("=" * 80)
    try:
        from config.loader import get_settings
        from data.collectors.kraken_collector import KrakenCollector

        collector = KrakenCollector(rate_limit_ms=int(os.getenv("RATE_LIMIT_MS", 500)))
        return _collect_exchange("kraken", collector, get_settings().kraken_symbols, full_history)
    except Exception as e:
        logger.error(f"Kraken data collection error: {str(e)}", exc_info=True)
        return False


def collect_htx_data(full_history: bool = False) -> bool:
    """Collect OHLCV from HTX into the prices table (incremental by default)."""
    logger.info("=" * 80)
    logger.info("STARTING HTX DATA COLLECTION")
    logger.info("=" * 80)
    try:
        from config.loader import get_settings
        from data.collectors.htx_collector import HTXCollector

        collector = HTXCollector(rate_limit_ms=int(os.getenv("RATE_LIMIT_MS", 500)))
        return _collect_exchange("htx", collector, get_settings().htx_symbols, full_history)
    except Exception as e:
        logger.error(f"HTX data collection error: {str(e)}", exc_info=True)
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
        if timeframe not in ("1m", "5m", "15m", "1h", "4h", "1d", "1w"):
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
