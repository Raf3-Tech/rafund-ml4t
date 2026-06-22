"""Offline tests for the Trading tab routes (trading_routes.py).

Follows the pattern in test_dashboard_app.py: db is a MagicMock, and the
data-layer calls used inside monitoring.routes.trading_routes are patched
at their use site so no real database is touched.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from monitoring.dashboard_app import create_app
from trading.position import PositionState


def _flat_pos(run_id, exchange):
    return PositionState(run_id=run_id, strategy_name="", symbol="", exchange=exchange)


@pytest.fixture
def client():
    app = create_app(db=MagicMock(), config={"retraining": {}})
    app.config.update(TESTING=True)
    return app.test_client()


def test_trading_status_returns_both_exchanges(client):
    with patch(
        "monitoring.routes.trading_routes.load_position",
        side_effect=lambda db, run_id, **kw: _flat_pos(run_id, kw.get("exchange")),
    ), patch(
        "monitoring.routes.trading_routes._current_signal",
        return_value={"strategy_name": None, "symbol": None, "signal": None},
    ), patch(
        "monitoring.routes.trading_routes._recent_orders", return_value=[],
    ):
        resp = client.get("/api/trading-status?mode=paper")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data.keys()) == {"binance", "kraken"}
        assert data["binance"]["position"]["run_id"] == "paper_binance"
        assert data["kraken"]["position"]["run_id"] == "paper_kraken"
        assert data["binance"]["recent_orders"] == []


def test_trading_status_rejects_bad_mode(client):
    resp = client.get("/api/trading-status?mode=bogus")
    assert resp.status_code == 400


def test_trading_chart_returns_bars_and_trades(client):
    bars_df = pd.DataFrame([
        {"timestamp": "2026-01-01", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100.0},
    ])
    trades_df = pd.DataFrame([
        {"event": "OPEN", "side": "LONG", "qty": 1.0, "price": 1.2, "pnl": None, "created_at": "2026-01-01"},
    ])

    def _read_sql(query, params=None):
        if "FROM prices" in query:
            return bars_df.copy()
        if "FROM paper_orders" in query:
            return trades_df.copy()
        return pd.DataFrame()

    client.application.config["DB"].read_sql.side_effect = _read_sql

    with patch(
        "monitoring.routes.trading_routes.load_position",
        return_value=_flat_pos("paper_binance", "binance"),
    ):
        resp = client.get("/api/trading-chart?exchange=binance&mode=paper&symbol=BTC/USDT")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["symbol"] == "BTC/USDT"
        assert len(data["bars"]) == 1
        assert len(data["trades"]) == 1
        assert data["trades"][0]["event"] == "OPEN"


def test_trading_chart_rejects_bad_exchange(client):
    resp = client.get("/api/trading-chart?exchange=coinbase")
    assert resp.status_code == 400
