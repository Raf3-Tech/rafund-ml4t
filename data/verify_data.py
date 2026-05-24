"""
Verify the dataset loaded into the database and scan for missing candles.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, time, timezone
from pathlib import Path
from typing import Dict, List, Optional

from config.loader import get_settings
from config.logging_config import get_logger
from data.db import DatabaseConnection

logger = get_logger(__name__)


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, '%Y-%m-%d').date()


def _normalize_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _start_of_day(dt: date) -> datetime:
    return datetime.combine(dt, time.min, tzinfo=timezone.utc)


def _end_of_day(dt: date) -> datetime:
    return datetime.combine(dt, time.max, tzinfo=timezone.utc)


def find_gaps(
    db: DatabaseConnection,
    symbol: str,
    timeframe: str,
    start_date: date,
    end_date: date,
) -> list[Dict[str, object]]:
    interval_minutes = {
        '1d': 1440,
        '1h': 60,
        '15m': 15,
        '5m': 5,
        '1m': 1,
    }
    if timeframe not in interval_minutes:
        raise ValueError(f'Unsupported timeframe: {timeframe}')

    expected_delta = timedelta(minutes=interval_minutes[timeframe])
    start_ts = _start_of_day(start_date)
    end_ts = _end_of_day(end_date)

    conn = db.get_connection()
    if conn.__class__.__module__.startswith('sqlite3'):
        query = (
            'SELECT timestamp FROM prices '
            'WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? '
            'ORDER BY timestamp'
        )
    else:
        query = (
            'SELECT timestamp FROM prices '
            'WHERE symbol = %s AND timestamp >= %s AND timestamp <= %s '
            'ORDER BY timestamp'
        )
    rows = []
    try:
        cursor = conn.cursor()
        if conn.__class__.__module__.startswith('sqlite3'):
            cursor.execute(query, (symbol, start_ts.isoformat(), end_ts.isoformat()))
        else:
            cursor.execute(query, (symbol, start_ts, end_ts))
        rows = [row[0] for row in cursor.fetchall()]
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        db.return_connection(conn)

    actual_timestamps = set()
    for ts in rows:
        if ts is None:
            continue
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        actual_timestamps.add(_normalize_timestamp(ts))

    gaps: list[Dict[str, object]] = []
    current = start_ts
    while current <= end_ts:
        if current not in actual_timestamps:
            gaps.append(
                {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'expected_timestamp': current,
                    'gap_minutes': interval_minutes[timeframe],
                }
            )
        current += expected_delta

    return gaps


def check_all_symbols(db: DatabaseConnection, symbols: list[str], timeframe: str, start_date: date, end_date: date) -> list[Dict[str, object]]:
    report: list[Dict[str, object]] = []
    for symbol in symbols:
        report.extend(find_gaps(db, symbol, timeframe, start_date, end_date))
    return report


def main() -> bool:
    parser = argparse.ArgumentParser(
        description='Verify database candles and find missing data gaps.'
    )
    parser.add_argument('--symbol', help='Single symbol to verify, e.g. BTC/USDT')
    parser.add_argument('--all', action='store_true', help='Verify all configured symbols')
    parser.add_argument('--from', dest='date_from', required=True, help='Start date YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', help='End date YYYY-MM-DD (default today)')
    parser.add_argument('--timeframe', default=None, help='Candle timeframe, default from config')

    args = parser.parse_args()
    settings = get_settings()

    if not args.all and not args.symbol:
        logger.error('Either --symbol or --all is required')
        return False

    start_date = _parse_iso_date(args.date_from)
    end_date = _parse_iso_date(args.date_to) if args.date_to else datetime.now(timezone.utc).date()
    if end_date < start_date:
        logger.error('--to date must be the same or after --from date')
        return False

    timeframe = args.timeframe or settings.timeframe
    if timeframe not in {'1d', '1h', '15m', '5m', '1m'}:
        logger.error(f'Unsupported timeframe: {timeframe}')
        return False

    db = DatabaseConnection()
    if not db.test_connection():
        logger.error('Database connection failed!')
        return False

    symbols = settings.collect_symbols if args.all else [args.symbol]
    combined_report = check_all_symbols(db, symbols, timeframe, start_date, end_date)

    if not combined_report:
        print(f"✓ No gaps found for {', '.join(symbols)} {timeframe} ({start_date} to {end_date})")
        return True

    print('Missing candles detected:')
    print(f"{'symbol':15} {'timeframe':8} {'expected_timestamp':25} {'gap_minutes':12}")
    print('-' * 70)
    for gap in combined_report:
        print(
            f"{gap['symbol']:15} {gap['timeframe']:8} "
            f"{gap['expected_timestamp'].isoformat():25} {gap['gap_minutes']:12}"
        )
    return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
