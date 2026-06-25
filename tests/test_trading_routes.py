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


def test_trading_status_returns_all_exchanges(client):
    with patch(
        "monitoring.routes.trading_routes.list_positions",
        side_effect=lambda db, exchange, run_id_prefix: [_flat_pos(run_id_prefix, exchange)],
    ), patch(
        "monitoring.routes.trading_routes._signal_for_position",
        return_value={"strategy_name": None, "symbol": None, "signal": None},
    ), patch(
        "monitoring.routes.trading_routes._recent_orders", return_value=[],
    ), patch(
        "trading.paper_trader._paper_candidates", return_value=[],
    ):
        resp = client.get("/api/trading-status?mode=paper")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data.keys()) == {"binance", "kraken", "htx"}
        assert data["binance"]["positions"][0]["run_id"] == "paper_binance"
        assert data["kraken"]["positions"][0]["run_id"] == "paper_kraken"
        assert data["htx"]["positions"][0]["run_id"] == "paper_htx"
        assert data["binance"]["positions"][0]["recent_orders"] == []


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
        resp = client.get(
            "/api/trading-chart?exchange=binance&mode=paper&symbol=BTC/USDT&run_id=paper_binance"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["symbol"] == "BTC/USDT"
        assert len(data["bars"]) == 1
        assert len(data["trades"]) == 1
        assert data["trades"][0]["event"] == "OPEN"


def test_trading_chart_rejects_bad_exchange(client):
    resp = client.get("/api/trading-chart?exchange=coinbase")
    assert resp.status_code == 400


def test_position_dict_includes_manual_halt_and_risk(client):
    with patch(
        "monitoring.routes.trading_routes.list_positions",
        side_effect=lambda db, exchange, run_id_prefix: [_flat_pos(run_id_prefix, exchange)],
    ), patch(
        "monitoring.routes.trading_routes._signal_for_position",
        return_value={"strategy_name": None, "symbol": None, "signal": None},
    ), patch(
        "monitoring.routes.trading_routes._recent_orders", return_value=[],
    ), patch(
        "trading.paper_trader._paper_candidates", return_value=[],
    ):
        resp = client.get("/api/trading-status?mode=paper")
        pos = resp.get_json()["binance"]["positions"][0]
        assert pos["manual_halt"] is False
        assert set(pos["risk"].keys()) == {
            "daily_loss_pct_used", "daily_loss_limit_pct",
            "drawdown_pct_used", "drawdown_limit_pct",
        }


def test_position_dict_includes_daily_pnl(client):
    """daily_pnl powers the always-visible risk strip — equity minus today's starting equity."""
    def _pos(db, exchange, run_id_prefix):
        p = _flat_pos(run_id_prefix, exchange)
        p.equity = 5120.0
        p.daily_start_equity = 5000.0
        return [p]

    with patch(
        "monitoring.routes.trading_routes.list_positions", side_effect=_pos,
    ), patch(
        "monitoring.routes.trading_routes._signal_for_position",
        return_value={"strategy_name": None, "symbol": None, "signal": None},
    ), patch(
        "monitoring.routes.trading_routes._recent_orders", return_value=[],
    ), patch(
        "trading.paper_trader._paper_candidates", return_value=[],
    ):
        resp = client.get("/api/trading-status?mode=paper")
        pos = resp.get_json()["binance"]["positions"][0]
        assert pos["daily_pnl"] == 120.0


def test_kill_switch_flattens_all_exchanges(client):
    with patch(
        "monitoring.routes.trading_routes.force_close_now",
        side_effect=lambda db, ex, mode, run_id=None: {"exchange": ex, "closed": True, "pnl": 1.0, "manual_halt": True},
    ) as mock_close:
        resp = client.post("/api/kill-switch", json={"mode": "paper"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["closed"]) == 3
        assert data["errors"] == []
        assert mock_close.call_count == 3


def test_kill_switch_single_exchange(client):
    with patch(
        "monitoring.routes.trading_routes.force_close_now",
        return_value={"exchange": "kraken", "closed": False, "manual_halt": True},
    ) as mock_close:
        resp = client.post("/api/kill-switch", json={"mode": "paper", "exchange": "kraken"})
        assert resp.status_code == 200
        mock_close.assert_called_once_with(client.application.config["DB"], "kraken", mode="paper", run_id=None)


def test_kill_switch_rejects_bad_mode(client):
    resp = client.post("/api/kill-switch", json={"mode": "bogus"})
    assert resp.status_code == 400


def test_kill_switch_rejects_bad_exchange(client):
    resp = client.post("/api/kill-switch", json={"mode": "paper", "exchange": "coinbase"})
    assert resp.status_code == 400


def test_resume_clears_manual_halt(client):
    with patch(
        "monitoring.routes.trading_routes.resume_trading",
        return_value={"exchange": "binance", "manual_halt": False},
    ) as mock_resume:
        resp = client.post("/api/resume", json={"mode": "paper", "exchange": "binance"})
        assert resp.status_code == 200
        assert resp.get_json()["resumed"] == [{"exchange": "binance", "manual_halt": False}]
        mock_resume.assert_called_once_with(client.application.config["DB"], "binance", mode="paper", run_id=None)


def test_equity_curve_empty_when_no_closed_trades(client):
    client.application.config["DB"].read_sql.return_value = pd.DataFrame()
    resp = client.get("/api/equity-curve?exchange=binance&mode=paper&run_id=paper_binance")
    assert resp.status_code == 200
    assert resp.get_json() == {"equity": [], "benchmark": []}


def test_equity_curve_builds_series_from_closed_trades(client):
    closes_df = pd.DataFrame([
        {"symbol": "BTC/USDT", "pnl": 10.0, "created_at": "2026-01-01"},
        {"symbol": "BTC/USDT", "pnl": -5.0, "created_at": "2026-01-02"},
    ])
    prices_df = pd.DataFrame([
        {"timestamp": "2026-01-01", "close": 100.0},
        {"timestamp": "2026-01-02", "close": 110.0},
    ])

    def _read_sql(query, params=None):
        if "FROM paper_orders" in query:
            return closes_df.copy()
        if "FROM prices" in query:
            return prices_df.copy()
        return pd.DataFrame()

    client.application.config["DB"].read_sql.side_effect = _read_sql
    resp = client.get("/api/equity-curve?exchange=binance&mode=paper&run_id=paper_binance")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["equity"]) == 2
    assert len(data["benchmark"]) == 2
    assert data["equity"][0]["equity"] > 0


def test_equity_curve_rejects_bad_exchange(client):
    resp = client.get("/api/equity-curve?exchange=coinbase")
    assert resp.status_code == 400


def test_trading_config_reports_env_var(client, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "1")
    resp = client.get("/api/trading-config")
    assert resp.get_json() == {"live_trading_enabled": True}

    monkeypatch.setenv("LIVE_TRADING_ENABLED", "0")
    resp = client.get("/api/trading-config")
    assert resp.get_json() == {"live_trading_enabled": False}


def test_paper_cycle_submits_job(client):
    with patch("monitoring.routes.trading_routes.has_running_job", return_value=False), patch(
        "monitoring.routes.trading_routes.submit_job", return_value="job123",
    ) as mock_submit:
        resp = client.post("/api/paper-cycle", json={"exchange": "kraken"})
        assert resp.status_code == 202
        assert resp.get_json() == {"job_id": "job123"}
        cmd = mock_submit.call_args[0][1]
        assert "--exchange" in cmd and "kraken" in cmd


def test_paper_cycle_rejects_when_already_running(client):
    with patch("monitoring.routes.trading_routes.has_running_job", return_value=True):
        resp = client.post("/api/paper-cycle", json={})
        assert resp.status_code == 409


def test_live_cycle_requires_exchange(client):
    resp = client.post("/api/live-cycle", json={})
    assert resp.status_code == 400


def test_live_cycle_submits_job(client):
    with patch("monitoring.routes.trading_routes.has_running_job", return_value=False), patch(
        "monitoring.routes.trading_routes.submit_job", return_value="job456",
    ) as mock_submit:
        resp = client.post("/api/live-cycle", json={"exchange": "binance"})
        assert resp.status_code == 202
        assert resp.get_json() == {"job_id": "job456"}
        cmd = mock_submit.call_args[0][1]
        assert "live" in cmd
        assert "--exchange" in cmd and "binance" in cmd


def test_journal_summary_no_filters(client):
    client.application.config["DB"].read_sql.return_value = pd.DataFrame([
        {"setup_tag": "ema_crossover_buy", "n_trades": 4, "win_rate": 0.5, "avg_pnl": 1.0, "total_pnl": 4.0},
    ])
    resp = client.get("/api/trade-journal-summary")
    assert resp.status_code == 200
    rows = resp.get_json()
    assert rows[0]["setup_tag"] == "ema_crossover_buy"
    sql = client.application.config["DB"].read_sql.call_args[0][0]
    assert "run_id LIKE" not in sql and "exchange =" not in sql


def test_journal_summary_applies_filters(client):
    mock_read_sql = client.application.config["DB"].read_sql
    mock_read_sql.return_value = pd.DataFrame()
    resp = client.get("/api/trade-journal-summary?exchange=kraken&mode=live&start=2026-01-01&end=2026-02-01&outcome=win")
    assert resp.status_code == 200
    sql, params = mock_read_sql.call_args[0]
    assert "run_id LIKE" in sql and "exchange = " in sql and "pnl > 0" in sql
    assert params == ["live_%", "kraken", "2026-01-01", "2026-02-01"]


def test_journal_summary_rejects_bad_mode(client):
    resp = client.get("/api/trade-journal-summary?mode=bogus")
    assert resp.status_code == 400


def test_journal_summary_rejects_bad_exchange(client):
    resp = client.get("/api/trade-journal-summary?exchange=coinbase")
    assert resp.status_code == 400


def test_journal_summary_rejects_bad_outcome(client):
    resp = client.get("/api/trade-journal-summary?outcome=sideways")
    assert resp.status_code == 400
