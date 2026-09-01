"""Execution engine abstraction and exchange adapter layer.

This module provides a clean separation between the trading decision logic and
order placement. It supports paper/shadow execution and a CCXT-backed live
adapter for Binance, Kraken, and HTX.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ExecutionOrder:
    order_id: Optional[str]
    status: str
    symbol: str
    side: str
    qty: float
    price: float
    filled_price: Optional[float]
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def closed(self) -> bool:
        return self.status == "closed"


class ExecutionAdapter:
    def __init__(self, exchange_name: str, shadow: bool = False) -> None:
        self.exchange_name = exchange_name
        self.shadow = shadow
        self._client = None

    def describe(self) -> Dict[str, Any]:
        return {
            "exchange": self.exchange_name,
            "adapter": self.__class__.__name__,
            "shadow": self.shadow,
            "api_keys_present": self.has_api_keys(),
        }

    def has_api_keys(self) -> bool:
        prefix = self.exchange_name.upper()
        return bool(os.environ.get(f"{prefix}_API_KEY")) and bool(os.environ.get(f"{prefix}_API_SECRET"))

    def connect(self):
        raise NotImplementedError

    def place_limit_order(self, symbol: str, side: str, qty: float, price: float) -> ExecutionOrder:
        raise NotImplementedError

    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        raise NotImplementedError

    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        raise NotImplementedError


class PaperExecutionAdapter(ExecutionAdapter):
    def __init__(self, exchange_name: str) -> None:
        super().__init__(exchange_name, shadow=True)

    def connect(self):
        return None

    def place_limit_order(self, symbol: str, side: str, qty: float, price: float) -> ExecutionOrder:
        return ExecutionOrder(
            order_id=None,
            status="shadow",
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            filled_price=price,
            raw={"shadow": True, "symbol": symbol, "side": side, "qty": qty, "price": price},
        )

    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return {"id": order_id, "status": "closed", "symbol": symbol}

    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return {"id": order_id, "status": "cancelled", "symbol": symbol}


class CCXTExecutionAdapter(ExecutionAdapter):
    def __init__(self, exchange_name: str, shadow: bool = False) -> None:
        super().__init__(exchange_name, shadow=shadow)

    def connect(self):
        if self.shadow:
            return None
        if self._client is not None:
            return self._client

        import ccxt

        prefix = self.exchange_name.upper()
        account_type = os.environ.get("LIVE_ACCOUNT_TYPE", "spot").strip().lower()
        creds = {
            "apiKey": os.environ.get(f"{prefix}_API_KEY", ""),
            "secret": os.environ.get(f"{prefix}_API_SECRET", ""),
            "enableRateLimit": True,
        }
        if self.exchange_name == "kraken":
            if account_type in ("future", "margin"):
                creds["options"] = {"defaultType": "margin"}
            self._client = ccxt.kraken(creds)
        elif self.exchange_name == "htx":
            if account_type == "future":
                creds["options"] = {"defaultType": "swap"}
            self._client = ccxt.htx(creds)
        else:
            creds["options"] = {
                "defaultType": account_type if account_type in ("future", "margin") else "spot"
            }
            self._client = ccxt.binance(creds)

        return self._client

    def place_limit_order(self, symbol: str, side: str, qty: float, price: float) -> ExecutionOrder:
        if self.shadow:
            return PaperExecutionAdapter(self.exchange_name).place_limit_order(symbol, side, qty, price)

        exchange = self.connect()
        try:
            order = exchange.create_limit_order(symbol, side, qty, price)
        except Exception as exc:
            return ExecutionOrder(
                order_id=None,
                status="error",
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                filled_price=None,
                raw={"error": str(exc)},
            )

        filled_price = float(order.get("average") or order.get("price") or price)
        return ExecutionOrder(
            order_id=order.get("id"),
            status=order.get("status", "open"),
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            filled_price=filled_price,
            raw=order,
        )

    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        exchange = self.connect()
        try:
            return exchange.fetch_order(order_id, symbol)
        except Exception as exc:
            return {"id": order_id, "status": "error", "error": str(exc)}

    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        exchange = self.connect()
        try:
            return exchange.cancel_order(order_id, symbol)
        except Exception as exc:
            return {"id": order_id, "status": "error", "error": str(exc)}


class ExecutionEngine:
    def __init__(self, adapter: ExecutionAdapter, max_notional: float = float("inf")) -> None:
        self.adapter = adapter
        self.max_notional = max_notional

    def describe(self) -> Dict[str, Any]:
        return self.adapter.describe()

    def place_limit_order(self, symbol: str, side: str, qty: float, price: float) -> ExecutionOrder:
        return self.adapter.place_limit_order(symbol, side, qty, price)

    def close_position(self, symbol: str, side: str, qty: float, price: float) -> ExecutionOrder:
        return self.place_limit_order(symbol, side, qty, price)


def build_execution_adapter(exchange_name: str, shadow: bool = False) -> ExecutionAdapter:
    if shadow:
        return PaperExecutionAdapter(exchange_name)
    return CCXTExecutionAdapter(exchange_name, shadow=shadow)


def build_execution_engine(exchange_name: str, shadow: bool = False, max_notional: float = float("inf")) -> ExecutionEngine:
    return ExecutionEngine(build_execution_adapter(exchange_name, shadow=shadow), max_notional=max_notional)
