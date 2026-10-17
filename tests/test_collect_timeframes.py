"""Multi-timeframe collection: every configured timeframe is fetched per symbol,
open candles are never stored, and 3h is derived from complete 1h buckets."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from cli import collect


def _bars(start: datetime, n: int, step: timedelta, symbol="BTC/USD", tf="1h") -> pd.DataFrame:
    rows = []
    for i in range(n):
        p = 100.0 + i
        rows.append({
            "symbol": symbol, "timeframe": tf, "timestamp": start + i * step,
            "open": p, "high": p + 1, "low": p - 1, "close": p + 0.5, "volume": 1.0,
        })
    return pd.DataFrame(rows)


def test_drop_open_candle_removes_unclosed_bar():
    now = datetime(2026, 10, 17, 12, 30, tzinfo=timezone.utc)
    df = _bars(datetime(2026, 10, 17, 10, tzinfo=timezone.utc), 3, timedelta(hours=1))
    out = collect._drop_open_candle(df, "1h", now)
    # 10:00 and 11:00 bars have closed; the 12:00 bar closes at 13:00.
    assert list(out["timestamp"].dt.hour) == [10, 11]


def test_derive_3h_keeps_only_complete_epoch_aligned_buckets():
    # 1h bars 01:00..07:00 -> bucket 00:00 is partial (2 bars), 03:00 complete,
    # 06:00 partial (2 bars). Only the 03:00 bucket may be written.
    src = _bars(datetime(2026, 10, 17, 1), 7, timedelta(hours=1))
    db = MagicMock()
    db.get_latest_timestamp.return_value = None
    db.get_prices.return_value = src
    db.insert_prices.side_effect = lambda df, exchange: len(df)

    inserted = collect._derive_timeframe(db, "BTC/USD", "kraken", "3h")

    assert inserted == 1
    written = db.insert_prices.call_args.args[0]
    row = written.iloc[0]
    assert row["timestamp"] == pd.Timestamp("2026-10-17 03:00")
    assert row["timeframe"] == "3h"
    assert row["open"] == 102.0 and row["close"] == 104.5
    assert row["high"] == 105.0 and row["low"] == 101.0 and row["volume"] == 3.0


def test_collect_exchange_fetches_every_timeframe_and_derives_3h():
    settings = SimpleNamespace(timeframes=["1h", "3h", "4h", "1d", "1w"], collect_start_date="2024-01-01")
    db = MagicMock()
    db.test_connection.return_value = True
    db.get_latest_timestamp.return_value = None
    db.get_data_stats.return_value = {}
    db.insert_prices.return_value = 1

    fetched = []
    collector = MagicMock()

    def _collect_symbol(symbol, timeframe, from_date, to_date):
        fetched.append(timeframe)
        collector.last_collection_df = _bars(
            datetime(2024, 1, 1, tzinfo=timezone.utc), 2, collect._TF_DELTA[timeframe], symbol, timeframe
        )
        return SimpleNamespace(records_fetched=2, errors=[])

    collector.collect_symbol.side_effect = _collect_symbol

    with patch("config.loader.get_settings", return_value=settings), \
         patch("cli.db.get_db_connection", return_value=db), \
         patch.object(collect, "_derive_timeframe", return_value=0) as derive:
        ok = collect._collect_exchange("kraken", collector, ["BTC/USD"], full_history=False)

    assert ok
    assert fetched == ["1h", "4h", "1d", "1w"]  # 3h is never requested from the exchange
    derive.assert_called_once_with(db, "BTC/USD", "kraken", "3h")
    for call in db.get_latest_timestamp.call_args_list:
        assert call.kwargs["timeframe"] in {"1h", "4h", "1d", "1w"}


def test_derived_source_is_collected_even_if_not_listed():
    settings = SimpleNamespace(timeframes=["3h", "1d"], collect_start_date="2024-01-01")
    db = MagicMock()
    db.test_connection.return_value = True
    db.get_latest_timestamp.return_value = None
    db.get_data_stats.return_value = {}
    collector = MagicMock()
    collector.last_collection_df = pd.DataFrame()
    collector.collect_symbol.return_value = SimpleNamespace(records_fetched=0, errors=[])

    with patch("config.loader.get_settings", return_value=settings), \
         patch("cli.db.get_db_connection", return_value=db), \
         patch.object(collect, "_derive_timeframe", return_value=0):
        collect._collect_exchange("kraken", collector, ["BTC/USD"], full_history=False)

    tfs = [c.kwargs["timeframe"] for c in collector.collect_symbol.call_args_list]
    assert tfs == ["1h", "1d"]
