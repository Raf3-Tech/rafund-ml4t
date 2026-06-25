"""Offline tests for the new one-click CLI-coverage routes in pipeline.py.

Follows the pattern in test_trading_routes.py: submit_job/has_running_job are
patched at their use site in monitoring.routes.pipeline so no real subprocess
is ever launched.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from monitoring.dashboard_app import create_app


@pytest.fixture
def client():
    app = create_app(db=MagicMock(), config={"retraining": {}})
    app.config.update(TESTING=True)
    return app.test_client()


def test_collect_passes_exchange_through(client):
    with patch("monitoring.routes.pipeline.has_running_job", return_value=False), patch(
        "monitoring.routes.pipeline.submit_job", return_value="job1",
    ) as mock_submit:
        resp = client.post("/api/collect", json={"exchange": "htx"})
        assert resp.status_code == 202
        cmd = mock_submit.call_args[0][1]
        assert "--exchange" in cmd and "htx" in cmd


def test_collect_rejects_bad_exchange(client):
    resp = client.post("/api/collect", json={"exchange": "coinbase"})
    assert resp.status_code == 400


def test_collect_accepts_all_exchanges_sentinel(client):
    with patch("monitoring.routes.pipeline.has_running_job", return_value=False), patch(
        "monitoring.routes.pipeline.submit_job", return_value="job1",
    ) as mock_submit:
        resp = client.post("/api/collect", json={"exchange": "all"})
        assert resp.status_code == 202
        cmd = mock_submit.call_args[0][1]
        assert "all" in cmd


@pytest.mark.parametrize("route,job_type,cli_arg", [
    ("/api/features", "features", "features"),
    ("/api/backtest", "backtest", "backtest"),
    ("/api/validate", "validate", "validate"),
    ("/api/train-classifier", "train_classifier", "train-classifier"),
])
def test_simple_job_routes_submit_expected_command(client, route, job_type, cli_arg):
    with patch("monitoring.routes.pipeline.has_running_job", return_value=False), patch(
        "monitoring.routes.pipeline.submit_job", return_value="jobX",
    ) as mock_submit:
        resp = client.post(route, json={})
        assert resp.status_code == 202
        assert resp.get_json() == {"job_id": "jobX"}
        assert mock_submit.call_args[0][0] == job_type
        cmd = mock_submit.call_args[0][1]
        assert cli_arg in cmd


@pytest.mark.parametrize("route", [
    "/api/features", "/api/backtest", "/api/validate", "/api/train-classifier",
])
def test_simple_job_routes_reject_when_already_running(client, route):
    with patch("monitoring.routes.pipeline.has_running_job", return_value=True):
        resp = client.post(route, json={})
        assert resp.status_code == 409
