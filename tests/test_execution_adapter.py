"""Tests for the execution adapter layer."""

import os

from trading.execution import CCXTExecutionAdapter, PaperExecutionAdapter, build_execution_adapter


def test_paper_execution_adapter_shadow_order():
    adapter = PaperExecutionAdapter("binance")
    order = adapter.place_limit_order("BTC/USDT", "buy", 0.001, 30000.0)
    assert order.status == "shadow"
    assert order.filled_price == 30000.0
    assert order.raw["shadow"] is True


def test_build_execution_adapter_uses_ccxt_when_not_shadow():
    adapter = build_execution_adapter("binance", shadow=False)
    assert adapter.__class__ is CCXTExecutionAdapter


def test_ccxt_execution_adapter_describe_detects_env_keys(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "abc")
    monkeypatch.setenv("BINANCE_API_SECRET", "def")
    adapter = CCXTExecutionAdapter("binance")
    desc = adapter.describe()
    assert desc["exchange"] == "binance"
    assert desc["api_keys_present"] is True
    assert desc["shadow"] is False


class _FakeCCXT:
    """Minimal CCXT stand-in: 3-decimal amounts, 1-decimal prices."""

    def __init__(self, min_amount=0.001, min_cost=10.0):
        self.limits = {"amount": {"min": min_amount}, "cost": {"min": min_cost}}
        self.created = []

    def load_markets(self):
        return {}

    def market(self, symbol):
        return {"symbol": symbol, "limits": self.limits}

    def amount_to_precision(self, symbol, amount):
        import math
        return f"{math.floor(amount * 1000) / 1000:.3f}"  # CCXT truncates

    def price_to_precision(self, symbol, price):
        return f"{round(price, 1):.1f}"

    def create_limit_order(self, symbol, side, qty, price):
        self.created.append((symbol, side, qty, price))
        return {"id": "x1", "status": "open", "price": price}


def _adapter_with(fake):
    adapter = CCXTExecutionAdapter("binance")
    adapter._client = fake
    return adapter


def test_ccxt_adapter_rounds_qty_and_price_to_market_precision():
    fake = _FakeCCXT()
    order = _adapter_with(fake).place_limit_order("BTC/USDT", "sell", 0.0123456, 30000.06)
    assert fake.created == [("BTC/USDT", "sell", 0.012, 30000.1)]
    assert order.qty == 0.012 and order.price == 30000.1
    assert order.status == "open"


def test_ccxt_adapter_rejects_below_min_amount_without_sending():
    fake = _FakeCCXT(min_amount=0.01)
    order = _adapter_with(fake).place_limit_order("BTC/USDT", "buy", 0.005, 30000.0)
    assert order.status == "error" and "minimum amount" in order.raw["error"]
    assert fake.created == []


def test_ccxt_adapter_rejects_below_min_cost_without_sending():
    fake = _FakeCCXT(min_cost=100.0)
    order = _adapter_with(fake).place_limit_order("BTC/USDT", "buy", 0.002, 30000.0)  # $60
    assert order.status == "error" and "minimum cost" in order.raw["error"]
    assert fake.created == []


def test_ccxt_adapter_rejects_qty_that_truncates_to_zero():
    fake = _FakeCCXT(min_amount=None, min_cost=None)
    order = _adapter_with(fake).place_limit_order("BTC/USDT", "buy", 0.0004, 30000.0)
    assert order.status == "error"
    assert fake.created == []
