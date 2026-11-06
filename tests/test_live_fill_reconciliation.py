"""Live trader must update local position state only from what the exchange
actually filled — never from the requested quantity.

Regression for the codebase-review finding: an unfilled/cancelled close was
recorded as flat while the position stayed open on the exchange, and a
rejected order crashed on order["id"] after the position had already been
closed in memory.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

import trading.live_trader as lt
from trading.execution import ExecutionOrder
from trading.position import PositionState


class FakeAdapter:
    """Scripted exchange: create_status for the placed order, then one
    status per fetch_order call (the last one repeats)."""

    def __init__(self, create_status="open", fetches=None, filled_on_create=None):
        self.create_status = create_status
        self.fetches = list(fetches or [])
        self.filled_on_create = filled_on_create
        self.placed = []
        self.cancelled = []

    def place_limit_order(self, symbol, side, qty, price):
        self.placed.append((symbol, side, qty, price))
        if self.create_status == "error":
            return ExecutionOrder(None, "error", symbol, side, qty, price, None, raw={"error": "rejected"})
        raw = {"id": "oid1", "status": self.create_status}
        if self.filled_on_create is not None:
            raw["filled"] = self.filled_on_create
        return ExecutionOrder("oid1", self.create_status, symbol, side, qty, price, price, raw=raw)

    def fetch_order(self, oid, symbol):
        return self.fetches.pop(0) if len(self.fetches) > 1 else self.fetches[0]

    def cancel_order(self, oid, symbol):
        self.cancelled.append(oid)
        return {"id": oid, "status": "canceled"}


class FakeEngine:
    def __init__(self, adapter):
        self.adapter = adapter

    def place_limit_order(self, *a):
        return self.adapter.place_limit_order(*a)


@pytest.fixture
def journal(monkeypatch):
    orders, alerts = [], []
    monkeypatch.setattr(lt.time, "sleep", lambda s: None)
    monkeypatch.setattr(lt, "log_order", lambda db, pos, event, price, **kw: orders.append((event, price, kw)))
    monkeypatch.setattr(lt, "save_position", lambda db, pos, **kw: None)
    monkeypatch.setattr(lt, "send_alert", lambda title, body: alerts.append(title))
    return orders, alerts


def _long(qty=1.0, entry=100.0):
    return PositionState(run_id="live_binance", strategy_name="S", symbol="BTC/USDT", side="LONG",
                         qty=qty, entry_price=entry, entry_time=datetime.now(timezone.utc))


def test_unfilled_close_keeps_position_open(journal):
    orders, alerts = journal
    adapter = FakeAdapter(fetches=[{"status": "open", "filled": 0.0},
                                   {"status": "canceled", "filled": 0.0}])
    pos = _long()
    assert lt._live_close(None, FakeEngine(adapter), pos, 110.0, "signal_exit") is None
    assert pos.side == "LONG" and pos.qty == 1.0 and pos.equity == 5000.0
    assert adapter.cancelled == ["oid1"]
    assert not any(e == "CLOSE" for e, _, _ in orders)
    assert any("CLOSE NOT FILLED" in a for a in alerts)


def test_partial_close_records_only_filled_slice(journal):
    orders, _ = journal
    adapter = FakeAdapter(fetches=[{"status": "canceled", "filled": 0.4, "average": 110.0}])
    pos = _long()
    assert lt._live_close(None, FakeEngine(adapter), pos, 110.0, "signal_exit") is None
    assert pos.side == "LONG"
    assert pos.qty == pytest.approx(0.6)
    # 0.4 * (110 - 100) minus commission on the filled notional only
    assert pos.equity == pytest.approx(5000.0 + 4.0 - 0.4 * 110.0 * 0.001)
    assert orders[-1][2]["close_reason"] == "signal_exit_partial"


def test_full_close_goes_flat(journal):
    orders, _ = journal
    adapter = FakeAdapter(create_status="closed", filled_on_create=1.0, fetches=[{}])
    pos = _long()
    pnl = lt._live_close(None, FakeEngine(adapter), pos, 110.0, "signal_exit")
    assert pnl == pytest.approx(10.0 - 110.0 * 0.001)
    assert pos.is_flat
    assert orders[-1][0] == "CLOSE" and orders[-1][2]["close_reason"] == "signal_exit"


def test_fill_racing_the_cancel_is_picked_up(journal):
    adapter = FakeAdapter(fetches=[{"status": "open", "filled": 0.0}] * 10
                          + [{"status": "closed", "filled": 1.0, "average": 109.0}])
    pos = _long()
    assert lt._live_close(None, FakeEngine(adapter), pos, 110.0, "signal_exit") is not None
    assert pos.is_flat


def test_rejected_order_does_not_crash_or_change_state(journal):
    orders, _ = journal
    adapter = FakeAdapter(create_status="error")
    pos = _long()
    assert lt._live_close(None, FakeEngine(adapter), pos, 110.0, "signal_exit") is None
    assert pos.qty == 1.0 and not orders


def test_unknown_final_state_halts_for_reconciliation(journal):
    _, alerts = journal
    adapter = FakeAdapter(fetches=[{"status": "error", "error": "timeout"}])
    pos = _long()
    assert lt._live_close(None, FakeEngine(adapter), pos, 110.0, "signal_exit") is None
    assert pos.manual_halt is True and pos.qty == 1.0
    assert any("RECONCILE NEEDED" in a for a in alerts)


def test_open_records_filled_qty_not_requested(journal):
    orders, _ = journal
    adapter = FakeAdapter(fetches=[{"status": "canceled", "filled": 0.25, "average": 100.0}])
    pos = PositionState(run_id="live_binance", strategy_name="S", symbol="BTC/USDT")
    lt._live_open(None, FakeEngine(adapter), pos, "LONG", 1.0, 100.0, "S_buy")
    assert pos.side == "LONG" and pos.qty == pytest.approx(0.25)
    assert orders[-1][0] == "OPEN"


def test_unfilled_open_stays_flat(journal):
    orders, _ = journal
    adapter = FakeAdapter(fetches=[{"status": "canceled", "filled": 0.0}])
    pos = PositionState(run_id="live_binance", strategy_name="S", symbol="BTC/USDT")
    lt._live_open(None, FakeEngine(adapter), pos, "LONG", 1.0, 100.0, "S_buy")
    assert pos.is_flat and not orders


def test_kill_switch_reports_unfilled_close_honestly(journal, monkeypatch):
    adapter = FakeAdapter(fetches=[{"status": "canceled", "filled": 0.0}])
    monkeypatch.setattr(lt, "_live_enabled", lambda: True)
    monkeypatch.setattr(lt, "_check_api_keys", lambda ex: True)
    monkeypatch.setattr(lt, "_build_execution_engine", lambda ex, shadow: FakeEngine(adapter))
    monkeypatch.setattr(lt, "_build_exchange", lambda ex: object())
    monkeypatch.setattr(lt, "_configure_leverage", lambda ex, sym: True)
    monkeypatch.setattr(lt, "_latest_bars", lambda db, sym, n, exchange: pd.DataFrame({"close": [110.0]}))
    pos = _long()
    result = lt.force_close_live(None, pos)
    assert result["closed"] is False and result["manual_halt"] is True
    assert result["remaining_qty"] == 1.0
    assert pos.side == "LONG" and pos.manual_halt is True
