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
