"""
Kraken data collector using CCXT.

Mirrors BinanceCollector exactly — same validation, same retry policy, same
CollectionResult contract.  Kraken spot trades USD pairs (BTC/USD, ETH/USD …)
so records land in the same `prices` table as Binance without a schema change.
"""

from __future__ import annotations

import ccxt
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from config.logging_config import get_logger
from data.models import CollectionResult, validate_ohlcv_batch

logger = get_logger(__name__)
_retry_logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAME = '1d'
TIMEFRAME_TO_MS = {
    '1m': 60 * 1000,
    '5m': 5 * 60 * 1000,
    '15m': 15 * 60 * 1000,
    '1h': 60 * 60 * 1000,
    '1d': 24 * 60 * 60 * 1000,
}
VALID_TIMEFRAMES = set(TIMEFRAME_TO_MS)

# Kraken spot USD pairs — USD, not USDT
DEFAULT_KRAKEN_SYMBOLS = [
    'BTC/USD',
    'ETH/USD',
    'SOL/USD',
    'ADA/USD',
    'DOT/USD',
    'LINK/USD',
    'XRP/USD',
]


class KrakenCollector:
    """Fetches market data from Kraken exchange with retries and validation."""

    def __init__(self, rate_limit_ms: int = 500):
        self.rate_limit_ms = rate_limit_ms
        self.client: Optional[ccxt.kraken] = None
        self.last_call_time = 0.0
        self.last_collection_df: pd.DataFrame = pd.DataFrame()
        self._initialize_client()

    def _initialize_client(self) -> None:
        try:
            self.client = ccxt.kraken(
                {
                    'enableRateLimit': True,
                    'rateLimit': self.rate_limit_ms,
                }
            )
            logger.info('Kraken client initialized')
        except Exception as exc:
            logger.error('Failed to initialize Kraken client', error=str(exc))
            raise

    def _respect_rate_limit(self) -> None:
        elapsed = time.time() - self.last_call_time
        wait_time = max(0.0, (self.rate_limit_ms / 1000.0) - elapsed)
        if wait_time > 0.0:
            time.sleep(wait_time)
        self.last_call_time = time.time()

    @retry(
        retry=retry_if_exception_type((
            ccxt.NetworkError,
            ccxt.RequestTimeout,
            ccxt.RateLimitExceeded,
            ccxt.ExchangeNotAvailable,
        )),
        wait=wait_exponential(multiplier=2, max=60),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(_retry_logger, logging.WARNING),
        reraise=True,
    )
    def _fetch_ohlcv(self, symbol: str, timeframe: str, since: Optional[int], limit: int) -> List[List[Any]]:
        self._respect_rate_limit()
        return self.client.fetch_ohlcv(symbol, timeframe, since, limit)

    def fetch_ohlcv_safe(
        self,
        symbol: str,
        timeframe: str = DEFAULT_TIMEFRAME,
        limit: int = 720,
        since: Optional[int] = None,
    ) -> tuple[list[Dict[str, Any]], list[str]]:
        if timeframe not in VALID_TIMEFRAMES:
            raise ValueError(f'Unsupported timeframe: {timeframe}')

        try:
            raw_candles = self._fetch_ohlcv(symbol, timeframe, since, limit)
        except RetryError as exc:
            last_attempt = exc.last_attempt
            error = last_attempt.exception()
            logger.error(
                'Kraken request failed after retries',
                symbol=symbol,
                timeframe=timeframe,
                attempts=last_attempt.attempt_number,
                error=str(error),
            )
            raise error
        except Exception as exc:
            logger.error(
                'Kraken request failed',
                symbol=symbol,
                timeframe=timeframe,
                error=str(exc),
            )
            raise

        if not raw_candles:
            logger.warning(
                'No candles returned from Kraken',
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                since=since,
            )
            return [], []

        rows: list[Dict[str, Any]] = []
        for candle in raw_candles:
            rows.append(
                {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'timestamp': datetime.fromtimestamp(candle[0] / 1000.0, timezone.utc),
                    'open': float(candle[1]),
                    'high': float(candle[2]),
                    'low': float(candle[3]),
                    'close': float(candle[4]),
                    'volume': float(candle[5]),
                }
            )

        valid_records, errors = validate_ohlcv_batch(rows)
        logger.info(
            'Fetched Kraken OHLCV batch',
            symbol=symbol,
            timeframe=timeframe,
            batch_size=len(rows),
            records_valid=len(valid_records),
            records_rejected=len(errors),
        )

        valid_dicts = [record.model_dump() for record in valid_records]
        return valid_dicts, errors

    def collect_symbol(
        self,
        symbol: str,
        timeframe: str = DEFAULT_TIMEFRAME,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> CollectionResult:
        if timeframe not in VALID_TIMEFRAMES:
            raise ValueError(f'Unsupported timeframe: {timeframe}')

        if from_date is None:
            from_date = datetime.now(timezone.utc) - timedelta(days=365)
        if to_date is None:
            to_date = datetime.now(timezone.utc)
        if from_date.tzinfo is None:
            from_date = from_date.replace(tzinfo=timezone.utc)
        if to_date.tzinfo is None:
            to_date = to_date.replace(tzinfo=timezone.utc)

        started_at = datetime.now(timezone.utc)
        self.last_collection_df = pd.DataFrame()
        records_fetched = 0
        records_rejected = 0
        records_inserted = 0
        errors: list[str] = []

        since = int(from_date.timestamp() * 1000)
        until_ms = int(to_date.timestamp() * 1000)
        # Kraken returns up to 720 daily candles per request
        batch_limit = 720
        all_rows: list[Dict[str, Any]] = []

        while since <= until_ms:
            try:
                batch_rows, batch_errors = self.fetch_ohlcv_safe(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=batch_limit,
                    since=since,
                )
            except Exception as exc:
                errors.append(str(exc))
                break

            batch_count = len(batch_rows) + len(batch_errors)
            records_fetched += batch_count
            records_rejected += len(batch_errors)
            errors.extend(batch_errors)
            all_rows.extend(batch_rows)

            if not batch_rows:
                break

            last_timestamp = batch_rows[-1]['timestamp']
            next_since = int(last_timestamp.timestamp() * 1000) + TIMEFRAME_TO_MS[timeframe]
            if next_since <= since:
                break
            since = next_since

            if since > until_ms:
                break

        if all_rows:
            self.last_collection_df = pd.DataFrame(all_rows)
            records_inserted = len(self.last_collection_df)

        completed_at = datetime.now(timezone.utc)
        result = CollectionResult(
            symbol=symbol,
            timeframe=timeframe,
            records_fetched=records_fetched,
            records_inserted=records_inserted,
            records_rejected=records_rejected,
            errors=errors,
            started_at=started_at,
            completed_at=completed_at,
        )

        logger.info(
            'Completed Kraken symbol collection',
            symbol=symbol,
            timeframe=timeframe,
            records_fetched=records_fetched,
            records_inserted=records_inserted,
            records_rejected=records_rejected,
            duration_seconds=(completed_at - started_at).total_seconds(),
        )
        return result
