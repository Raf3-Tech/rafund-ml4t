"""Offline tests for the ops-dashboard Flask app.

All data-layer and sibling-module entrypoints are patched at their use site in
``monitoring.dashboard_app`` so no real database, data layer, or scheduler code
is exercised.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from monitoring.dashboard_app import create_app
from monitoring.drift_detector import DriftReport


@dataclass
class _FakeModelRow:
    model_name: str
    symbol: str
    stage: str
    age_days: Optional[int]
    oos_sharpe: Optional[float]
    blocked: bool
    block_reason: Optional[str]
    safe_to_trade: bool


@dataclass
class _FakeDriftRow:
    model_name: str
    symbol: str
    severity: str
    detected_at: Optional[datetime]


@dataclass
class _FakeRetrainResult:
    model_type: str
    symbol: str
    success: bool
    promoted: bool
    run_id: Optional[str]
    model_name: Optional[str]
    reason: Optional[str]
    error: Optional[str]


@dataclass
class _FakeReport:
    symbol: str
    severity: str
    max_psi: float


def _model_row() -> _FakeModelRow:
    return _FakeModelRow(
        model_name="rafund_factor_model_BTCUSDT",
        symbol="BTC/USDT",
        stage="Production",
        age_days=5,
        oos_sharpe=1.42,
        blocked=False,
        block_reason=None,
        safe_to_trade=True,
    )


def _drift_row() -> _FakeDriftRow:
    return _FakeDriftRow(
        model_name="rafund_factor_model_BTCUSDT",
        symbol="BTC/USDT",
        severity="warning",
        detected_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
    )


@pytest.fixture
def client():
    app = create_app(db=MagicMock(), config={"retraining": {}})
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_renders_rows(client):
    with patch(
        "monitoring.dashboard_app.get_model_status", return_value=[_model_row()]
    ), patch(
        "monitoring.dashboard_app.get_drift_status", return_value=[_drift_row()]
    ):
        resp = client.get("/")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "rafund_factor_model_BTCUSDT" in body
    assert "BTC/USDT" in body
    assert "warning" in body


def test_index_renders_empty_state(client):
    with patch(
        "monitoring.dashboard_app.get_model_status", return_value=[]
    ), patch("monitoring.dashboard_app.get_drift_status", return_value=[]):
        resp = client.get("/")

    assert resp.status_code == 200
    assert "No production models found." in resp.get_data(as_text=True)


def test_api_models_returns_json_list(client):
    row = _model_row()
    with patch(
        "monitoring.dashboard_app.get_model_status", return_value=[row]
    ):
        resp = client.get("/api/models")

    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["model_name"] == "rafund_factor_model_BTCUSDT"
    assert data[0]["safe_to_trade"] is True


def test_api_drift_returns_json_list(client):
    row = _drift_row()
    with patch(
        "monitoring.dashboard_app.get_drift_status", return_value=[row]
    ):
        resp = client.get("/api/drift")

    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["severity"] == "warning"
    # datetime serialized to ISO string
    assert data[0]["detected_at"].startswith("2026-06-04")


def test_api_retrain_invokes_cycle_and_returns_results(client):
    result = _FakeRetrainResult(
        model_type="factor",
        symbol="BTC/USDT",
        success=True,
        promoted=True,
        run_id="run-123",
        model_name="rafund_factor_model_BTCUSDT",
        reason="approved",
        error=None,
    )
    with patch(
        "monitoring.dashboard_app.run_retraining_cycle", return_value=[result]
    ) as mock_cycle:
        resp = client.post("/api/retrain")

    assert resp.status_code == 200
    mock_cycle.assert_called_once()
    data = resp.get_json()
    assert "results" in data
    assert len(data["results"]) == 1
    assert data["results"][0]["model_type"] == "factor"
    assert data["results"][0]["promoted"] is True


def test_api_retrain_empty_results(client):
    with patch(
        "monitoring.dashboard_app.run_retraining_cycle", return_value=[]
    ) as mock_cycle:
        resp = client.post("/api/retrain")

    assert resp.status_code == 200
    mock_cycle.assert_called_once()
    assert resp.get_json() == {"results": []}


def test_api_drift_check_valid_body_returns_report(client):
    report = _FakeReport(symbol="BTC/USDT", severity="critical", max_psi=0.42)
    with patch(
        "monitoring.dashboard_app.run_drift_check", return_value=report
    ) as mock_check:
        resp = client.post(
            "/api/drift-check",
            json={"model": "rafund_factor_model_BTCUSDT", "symbol": "BTC/USDT"},
        )

    assert resp.status_code == 200
    mock_check.assert_called_once()
    args, _ = mock_check.call_args
    # db, model, symbol
    assert args[1] == "rafund_factor_model_BTCUSDT"
    assert args[2] == "BTC/USDT"
    data = resp.get_json()
    assert data["report"]["severity"] == "critical"
    assert data["report"]["max_psi"] == 0.42


def test_api_drift_check_none_report(client):
    with patch(
        "monitoring.dashboard_app.run_drift_check", return_value=None
    ):
        resp = client.post(
            "/api/drift-check",
            json={"model": "m", "symbol": "BTC/USDT"},
        )

    assert resp.status_code == 200
    assert resp.get_json() == {"report": None}


def test_api_drift_check_missing_field_returns_400(client):
    with patch(
        "monitoring.dashboard_app.run_drift_check"
    ) as mock_check:
        resp = client.post("/api/drift-check", json={"model": "m"})

    assert resp.status_code == 400
    mock_check.assert_not_called()


def test_api_drift_check_empty_body_returns_400(client):
    resp = client.post("/api/drift-check", json={})
    assert resp.status_code == 400


def _drift_report() -> DriftReport:
    return DriftReport(
        symbol="BTC/USDT",
        detected_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
        features_checked=3,
        features_drifted=["rsi_14", "macd"],
        max_psi=0.42,
        mean_psi=0.21,
        drift_detected=True,
        severity="critical",
        recommended_action="halt_trading",
        raw_psi_scores={"rsi_14": 0.42, "macd": 0.30, "vol": 0.05},
    )


def test_drift_detail_renders_chart(client):
    report = _drift_report()
    with patch(
        "monitoring.dashboard_app.run_drift_check", return_value=report
    ) as mock_check, patch(
        "monitoring.dashboard_app.psi_bar_html",
        return_value="<div>PLOTLY_CHART</div>",
    ):
        resp = client.get(
            "/drift?model=rafund_factor_model_BTCUSDT&symbol=BTC/USDT"
        )

    assert resp.status_code == 200
    mock_check.assert_called_once()
    args, _ = mock_check.call_args
    assert args[1] == "rafund_factor_model_BTCUSDT"
    assert args[2] == "BTC/USDT"
    body = resp.get_data(as_text=True)
    assert "PLOTLY_CHART" in body
    assert "rafund_factor_model_BTCUSDT" in body
    assert "halt_trading" in body


def test_drift_detail_none_report_shows_no_data(client):
    with patch(
        "monitoring.dashboard_app.run_drift_check", return_value=None
    ), patch(
        "monitoring.dashboard_app.psi_bar_html",
        return_value="<div>PLOTLY_CHART</div>",
    ) as mock_chart:
        resp = client.get("/drift?model=m&symbol=BTC/USDT")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "No drift data available." in body
    assert "PLOTLY_CHART" not in body
    mock_chart.assert_not_called()


def test_drift_detail_missing_model_returns_400(client):
    with patch(
        "monitoring.dashboard_app.run_drift_check"
    ) as mock_check:
        resp = client.get("/drift?symbol=BTC/USDT")

    assert resp.status_code == 400
    mock_check.assert_not_called()


def test_drift_detail_missing_symbol_returns_400(client):
    with patch(
        "monitoring.dashboard_app.run_drift_check"
    ) as mock_check:
        resp = client.get("/drift?model=m")

    assert resp.status_code == 400
    mock_check.assert_not_called()


def test_index_contains_drift_detail_link(client):
    with patch(
        "monitoring.dashboard_app.get_model_status", return_value=[]
    ), patch(
        "monitoring.dashboard_app.get_drift_status", return_value=[_drift_row()]
    ):
        resp = client.get("/")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Jinja's urlencode filter treats "/" as a safe path char, so the symbol
    # passes through unescaped; it is still a valid query-string value.
    assert "/drift?model=" in body
    assert "symbol=BTC/USDT" in body


# --- Hardening: health, auth, error handling, input validation -------------


def test_health_returns_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


@pytest.fixture
def token_client():
    """Client for an app with the bearer token enabled."""
    app = create_app(
        db=MagicMock(), config={"retraining": {}}, api_token="s3cret"
    )
    app.config.update(TESTING=True)
    return app.test_client()


def test_retrain_rejects_missing_token_when_configured(token_client):
    with patch("monitoring.dashboard_app.run_retraining_cycle") as mock_cycle:
        resp = token_client.post("/api/retrain")
    assert resp.status_code == 401
    mock_cycle.assert_not_called()


def test_retrain_rejects_wrong_token(token_client):
    with patch("monitoring.dashboard_app.run_retraining_cycle") as mock_cycle:
        resp = token_client.post(
            "/api/retrain", headers={"Authorization": "Bearer nope"}
        )
    assert resp.status_code == 401
    mock_cycle.assert_not_called()


def test_retrain_accepts_valid_token(token_client):
    with patch(
        "monitoring.dashboard_app.run_retraining_cycle", return_value=[]
    ) as mock_cycle:
        resp = token_client.post(
            "/api/retrain", headers={"Authorization": "Bearer s3cret"}
        )
    assert resp.status_code == 200
    mock_cycle.assert_called_once()


def test_drift_check_requires_token_when_configured(token_client):
    with patch("monitoring.dashboard_app.run_drift_check") as mock_check:
        resp = token_client.post(
            "/api/drift-check", json={"model": "m", "symbol": "BTC/USDT"}
        )
    assert resp.status_code == 401
    mock_check.assert_not_called()


def test_drift_check_rejects_oversized_input(client):
    with patch("monitoring.dashboard_app.run_drift_check") as mock_check:
        resp = client.post(
            "/api/drift-check",
            json={"model": "m" * 300, "symbol": "BTC/USDT"},
        )
    assert resp.status_code == 400
    mock_check.assert_not_called()


def test_drift_check_rejects_non_string_fields(client):
    with patch("monitoring.dashboard_app.run_drift_check") as mock_check:
        resp = client.post(
            "/api/drift-check", json={"model": 123, "symbol": ["x"]}
        )
    assert resp.status_code == 400
    mock_check.assert_not_called()


def test_unhandled_exception_returns_json_500():
    # A fresh app (no TESTING flag) so the error handler runs instead of the
    # exception propagating to the test client.
    app = create_app(db=MagicMock(), config={"retraining": {}})
    cli = app.test_client()
    with patch(
        "monitoring.dashboard_app.get_model_status",
        side_effect=RuntimeError("boom"),
    ):
        resp = cli.get("/api/models")
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "internal server error"}
    assert resp.is_json


def test_unknown_route_returns_json_404(client):
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()
