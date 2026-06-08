"""Binance perpetual futures funding rate collector.

Endpoint: GET /fapi/v1/fundingRate
Frequency: 8-hour funding payments.
Paginates through all available history (1000 records per request).

Symbols that have active Binance perp markets:
  BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, ADA/USDT, LINK/USDT, XRP/USDT

DOT/USDT is excluded by default (thin liquidity).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FAPI_BASE = "https://fapi.binance.com"

PERP_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "ADAUSDT",
    "LINKUSDT",
    "XRPUSDT",
]

# Map from collector-style "BTC/USDT" → Binance perp symbol "BTCUSDT"
SYMBOL_MAP = {
    "BTC/USDT": "BTCUSDT",
    "ETH/USDT": "ETHUSDT",
    "BNB/USDT": "BNBUSDT",
    "SOL/USDT": "SOLUSDT",
    "ADA/USDT": "ADAUSDT",
    "LINK/USDT": "LINKUSDT",
    "XRP/USDT": "XRPUSDT",
}


class BinanceFundingCollector:
    def __init__(self, rate_limit_ms: int = 200):
        self.rate_limit_ms = rate_limit_ms
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, params: dict) -> Optional[list]:
        url = FAPI_BASE + path
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error("Binance fapi request failed %s %s: %s", url, params, e)
            return None

    def verify_perp_exists(self, binance_symbol: str) -> bool:
        """Check that the symbol has an active perpetual contract."""
        data = self._get("/fapi/v1/exchangeInfo", {})
        if data is None:
            return False
        symbols = {s["symbol"] for s in data.get("symbols", [])}
        return binance_symbol in symbols

    def collect_symbol(
        self,
        symbol: str,
        from_timestamp_ms: Optional[int] = None,
    ) -> pd.DataFrame:
        """Collect full funding rate history for one symbol.

        Args:
            symbol: Collector-style e.g. "BTC/USDT", or Binance-style "BTCUSDT".
            from_timestamp_ms: Start time in milliseconds (inclusive). If None, fetch all.

        Returns:
            DataFrame with columns: symbol, funding_time, funding_rate, mark_price.
        """
        binance_sym = SYMBOL_MAP.get(symbol, symbol.replace("/", ""))

        records: List[dict] = []
        start_time = from_timestamp_ms
        limit = 1000

        while True:
            params = {"symbol": binance_sym, "limit": limit}
            if start_time is not None:
                params["startTime"] = start_time

            data = self._get("/fapi/v1/fundingRate", params)
            if not data:
                break

            records.extend(data)
            if len(data) < limit:
                break

            # Advance start_time past the last record
            last_time = data[-1].get("fundingTime", 0)
            start_time = int(last_time) + 1
            time.sleep(self.rate_limit_ms / 1000.0)

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df = df.rename(columns={
            "fundingTime": "funding_time",
            "fundingRate": "funding_rate",
            "markPrice": "mark_price",
        })
        df["symbol"] = symbol
        df["funding_time"] = pd.to_datetime(df["funding_time"].astype(int), unit="ms", utc=True)
        df["funding_rate"] = df["funding_rate"].astype(float)
        df["mark_price"] = df.get("mark_price", pd.Series(dtype=float)).astype(float)

        return df[["symbol", "funding_time", "funding_rate", "mark_price"]].drop_duplicates(
            subset=["symbol", "funding_time"]
        )

    def collect_all(self, db, symbols: Optional[List[str]] = None) -> int:
        """Collect and store funding rates for all configured symbols.

        Returns:
            Total number of rows inserted.
        """
        target_symbols = symbols or list(SYMBOL_MAP.keys())
        total_inserted = 0

        for symbol in target_symbols:
            binance_sym = SYMBOL_MAP.get(symbol, symbol.replace("/", ""))

            logger.info("Verifying perp market for %s (%s)...", symbol, binance_sym)
            if not self.verify_perp_exists(binance_sym):
                logger.warning("[SKIP] %s: no active Binance perp market found", symbol)
                continue

            # Find latest stored timestamp to enable incremental collection
            latest_ms = db.get_latest_funding_time_ms(symbol)
            from_ms = latest_ms + 1 if latest_ms else None

            logger.info("Collecting %s funding rates from %s...",
                        symbol, "beginning" if from_ms is None else f"ts={from_ms}")
            df = self.collect_symbol(symbol, from_timestamp_ms=from_ms)

            if df.empty:
                logger.info("[OK] %s: already up to date", symbol)
                continue

            inserted = db.insert_funding_rates(df)
            total_inserted += inserted
            logger.info("[OK] %s: %d funding rate rows inserted", symbol, inserted)
            time.sleep(self.rate_limit_ms / 1000.0)

        return total_inserted
