"""Tests for data/collectors/binance_funding_collector.py — Phase B coverage.

All HTTP calls are mocked. No network required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.collectors.binance_funding_collector import (
    SYMBOL_MAP,
    BinanceFundingCollector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _funding_record(t_ms: int, rate: float = 0.0001, mark: float = 30000.0) -> dict:
    return {"fundingTime": t_ms, "fundingRate": str(rate), "markPrice": str(mark)}


def _make_collector() -> BinanceFundingCollector:
    return BinanceFundingCollector(rate_limit_ms=0)


# ---------------------------------------------------------------------------
# collect_symbol — single page (< limit)
# ---------------------------------------------------------------------------


def test_collect_symbol_single_page_returns_dataframe():
    """A response shorter than the 1000-record limit must stop pagination."""
    records = [_funding_record(1_000_000 + i * 28_800_000) for i in range(5)]

    collector = _make_collector()
    collector._get = MagicMock(return_value=records)

    df = collector.collect_symbol("BTC/USDT")

    assert not df.empty
    assert list(df.columns) == ["symbol", "funding_time", "funding_rate", "mark_price"]
    assert len(df) == 5
    # _get called exactly once (single page)
    assert collector._get.call_count == 1


def test_collect_symbol_maps_slash_symbol_to_binance_style():
    """BTC/USDT must be translated to BTCUSDT in the API request."""
    records = [_funding_record(1_000_000)]
    collector = _make_collector()
    collector._get = MagicMock(return_value=records)

    collector.collect_symbol("BTC/USDT")

    call_params = collector._get.call_args[0][1]  # second positional arg = params dict
    assert call_params["symbol"] == "BTCUSDT"


# ---------------------------------------------------------------------------
# collect_symbol — pagination
# ---------------------------------------------------------------------------


def test_collect_symbol_paginates_when_full_page_returned():
    """When API returns exactly `limit` records, collector must advance start_time and page again."""
    limit = 1000
    page1 = [_funding_record(i * 28_800_000) for i in range(limit)]
    page2 = [_funding_record(limit * 28_800_000 + i * 28_800_000) for i in range(3)]

    call_results = [page1, page2]
    collector = _make_collector()
    collector._get = MagicMock(side_effect=call_results)

    df = collector.collect_symbol("ETH/USDT")

    assert collector._get.call_count == 2
    assert len(df) == limit + 3


def test_collect_symbol_advances_start_time_by_one_ms():
    """start_time on the second page must be last_fundingTime + 1."""
    limit = 1000
    last_ms = 999_000_000
    page1 = [_funding_record(i * 1000) for i in range(limit - 1)] + [_funding_record(last_ms)]
    page2 = [_funding_record(last_ms + 28_800_000)]

    call_results = [page1, page2]
    collector = _make_collector()
    collector._get = MagicMock(side_effect=call_results)

    collector.collect_symbol("BTC/USDT")

    second_call_params = collector._get.call_args_list[1][0][1]
    assert second_call_params["startTime"] == last_ms + 1


def test_collect_symbol_passes_from_timestamp_ms():
    """from_timestamp_ms must be forwarded as startTime on the first request."""
    records = [_funding_record(5_000_000)]
    collector = _make_collector()
    collector._get = MagicMock(return_value=records)

    collector.collect_symbol("BTC/USDT", from_timestamp_ms=5_000_000)

    first_call_params = collector._get.call_args[0][1]
    assert first_call_params["startTime"] == 5_000_000


# ---------------------------------------------------------------------------
# collect_symbol — empty / error responses
# ---------------------------------------------------------------------------


def test_collect_symbol_empty_response_returns_empty_dataframe():
    collector = _make_collector()
    collector._get = MagicMock(return_value=[])
    df = collector.collect_symbol("BTC/USDT")
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_collect_symbol_none_response_returns_empty_dataframe():
    """A network error (_get returns None) must not raise."""
    collector = _make_collector()
    collector._get = MagicMock(return_value=None)
    df = collector.collect_symbol("BTC/USDT")
    assert df.empty


def test_collect_symbol_deduplicates_on_funding_time():
    """Overlapping pages must not produce duplicate rows."""
    t = 1_000_000
    records = [_funding_record(t), _funding_record(t)]  # duplicate timestamp
    collector = _make_collector()
    collector._get = MagicMock(return_value=records)
    df = collector.collect_symbol("BTC/USDT")
    assert len(df) == 1


# ---------------------------------------------------------------------------
# collect_symbol — output types
# ---------------------------------------------------------------------------


def test_collect_symbol_funding_time_is_datetime():
    records = [_funding_record(1_609_459_200_000)]  # 2021-01-01 UTC
    collector = _make_collector()
    collector._get = MagicMock(return_value=records)
    df = collector.collect_symbol("BTC/USDT")
    assert pd.api.types.is_datetime64_any_dtype(df["funding_time"])
    assert df["funding_time"].dt.tz is not None  # UTC-aware


def test_collect_symbol_funding_rate_is_float():
    records = [_funding_record(1_000_000, rate=0.0003)]
    collector = _make_collector()
    collector._get = MagicMock(return_value=records)
    df = collector.collect_symbol("BTC/USDT")
    assert df["funding_rate"].dtype == float
    assert df["funding_rate"].iloc[0] == pytest.approx(0.0003)


# ---------------------------------------------------------------------------
# collect_all — incremental collection
# ---------------------------------------------------------------------------


def test_collect_all_uses_latest_stored_time_for_incremental():
    """collect_all must call collect_symbol with from_timestamp_ms = latest + 1."""
    latest_ms = 1_700_000_000_000

    mock_db = MagicMock()
    mock_db.get_latest_funding_time_ms.return_value = latest_ms
    mock_db.insert_funding_rates.return_value = 1

    records = [_funding_record(latest_ms + 28_800_000)]
    collector = _make_collector()
    # Stub verify_perp_exists to avoid real HTTP
    collector.verify_perp_exists = MagicMock(return_value=True)
    collector._get = MagicMock(return_value=records)

    collector.collect_all(mock_db, symbols=["BTC/USDT"])

    call_params = collector._get.call_args_list[0][0][1]
    assert call_params["startTime"] == latest_ms + 1


def test_collect_all_skips_unrecognised_perp():
    """Symbols not in SYMBOL_MAP and failing verify_perp_exists must be skipped."""
    mock_db = MagicMock()
    collector = _make_collector()
    collector.verify_perp_exists = MagicMock(return_value=False)
    collector._get = MagicMock()

    collector.collect_all(mock_db, symbols=["BTC/USDT"])

    collector._get.assert_not_called()
    mock_db.insert_funding_rates.assert_not_called()


# ---------------------------------------------------------------------------
# SYMBOL_MAP completeness
# ---------------------------------------------------------------------------


def test_symbol_map_covers_expected_perps():
    expected = {"BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "ADA/USDT", "LINK/USDT", "XRP/USDT"}
    assert expected.issubset(set(SYMBOL_MAP.keys()))
