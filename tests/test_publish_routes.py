"""Tests for monitoring/routes/publish.py — the Phase 5 tablet-facing surface.

Two things the brief is explicit about, both tested structurally rather
than by convention: "authenticated" (token required, unlike the rest of the
dashboard's GET routes) and "no write methods on this surface at all."
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from monitoring.dashboard_app import create_app
from risk.prop_account import PropAccountState


def _fake_account_state(**overrides) -> PropAccountState:
    from datetime import datetime, timezone
    base = dict(
        balance=Decimal("9900"), unrealized_pnl=Decimal("0"), accrued_funding_cost=Decimal("0"),
        daily_start_balance=Decimal("10000"), peak_balance=Decimal("10000"),
        kraken_mdd_pct=Decimal("0.03"), last_rollover=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return PropAccountState(**base)


@pytest.fixture
def app_with_token():
    app = create_app(db=MagicMock(), config={"retraining": {}}, api_token="secret-token")
    app.config.update(TESTING=True)
    return app


def test_registered_publish_routes_are_get_only():
    """Structural guarantee, not a convention: no rule on this blueprint may
    accept POST/PUT/PATCH/DELETE — the brief is explicit that this surface
    has no write methods at all."""
    app = create_app(db=MagicMock(), config={"retraining": {}})
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    publish_rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith("publish.")]
    assert publish_rules, "expected at least one route registered on the publish blueprint"
    for rule in publish_rules:
        assert not (rule.methods & write_methods), f"{rule} allows a write method: {rule.methods}"


def test_requires_token_when_one_is_configured(app_with_token):
    client = app_with_token.test_client()
    resp = client.get("/api/publish/setups")
    assert resp.status_code == 401


def test_returns_setups_and_room_remaining_with_valid_token(app_with_token):
    df = pd.DataFrame([{
        "id": 1, "strategy_name": "EMA Crossover", "symbol": "BTC/USD", "exchange": "kraken",
        "direction": "LONG", "limit_price": Decimal("60000"), "stop_price": Decimal("59500"),
        "take_profit_price": Decimal("61000"), "position_size_usd": Decimal("3000"),
        "qty": Decimal("0.05"), "leaderboard_score": 0.5, "leaderboard_rank": 1,
        "thesis": "test", "source_timeframe": "1d",
        "created_at": pd.Timestamp("2026-01-01", tz="UTC"), "expires_at": pd.Timestamp("2026-01-01 00:30", tz="UTC"),
        "expected_cost": Decimal("2.40"), "worst_case_loss": Decimal("27.40"),
        "r_multiple": Decimal("1.9"), "leverage_required": Decimal("0.6"),
        "expected_hold_hours": Decimal("24"),
    }])
    app_with_token.config["DB"].read_sql.return_value = df

    with patch("monitoring.routes.publish.load_position", return_value=MagicMock()), \
         patch("monitoring.routes.publish.current_account_state", return_value=_fake_account_state()):
        client = app_with_token.test_client()
        resp = client.get("/api/publish/setups", headers={"Authorization": "Bearer secret-token"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["setups"]) == 1
    assert body["setups"][0]["symbol"] == "BTC/USD"
    assert isinstance(body["setups"][0]["limit_price"], float)  # not a Decimal-as-string
    # mdl_floor = mdd_floor = 10000*(1-0.03) = 9700; equity = balance = 9900 -> both rooms = 200.
    assert body["daily_room_remaining"] == pytest.approx(200.0)
    assert body["lifetime_room_remaining"] == pytest.approx(200.0)
    assert "generated_at" in body


def test_empty_setups_returns_empty_list_not_error(app_with_token):
    app_with_token.config["DB"].read_sql.return_value = pd.DataFrame()

    with patch("monitoring.routes.publish.load_position", return_value=MagicMock()), \
         patch("monitoring.routes.publish.current_account_state", return_value=_fake_account_state()):
        client = app_with_token.test_client()
        resp = client.get("/api/publish/setups", headers={"Authorization": "Bearer secret-token"})

    assert resp.status_code == 200
    assert resp.get_json()["setups"] == []
