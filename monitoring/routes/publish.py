"""Phase 5 of the Kraken Prop risk gate: the tablet-facing publish surface.

Authenticated, read-only, over Tailscale (a network/deployment concern —
Tailscale gives this Flask app a private address the tablet reaches over its
own VPN; nothing here is Tailscale-aware code). This module MUST NOT define
a route with any method other than GET — tests/test_publish_routes.py
enforces that structurally, not just by convention, since the brief is
explicit: "No write methods on this surface at all."

Unlike the rest of the dashboard (GET routes unauthenticated, POST routes
require_token — see monitoring/routes/trading_routes.py), every route here
requires the token. This surface is reached by a device outside the
browser-same-origin trust boundary the rest of the dashboard assumes.

Reuses signals.pending_signal_detector.current_account_state — the same
account-state authority the pre-trade gate itself evaluates against — so
the room-remaining figures shown here always match what actually gated the
setups being displayed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from flask import Blueprint, current_app, jsonify

from monitoring.routes._auth import require_token
from monitoring.routes._json import records as _records
from signals.pending_signal_detector import current_account_state, decimal_columns_to_float
from trading.position import load_position

bp = Blueprint("publish", __name__)


@bp.route("/api/publish/setups", methods=["GET"])
@require_token
def api_publish_setups():
    """Approved (status='active' — REJECT decisions never reach this status,
    per the pre-trade gate) setups, plus current daily/lifetime room
    remaining. Polled by the tablet app; no write methods anywhere on this
    blueprint."""
    from config.loader import get_settings

    db = current_app.config["DB"]
    cfg = get_settings()

    df = db.read_sql(
        """
        SELECT id, strategy_name, symbol, exchange, direction, limit_price, stop_price,
               take_profit_price, position_size_usd, qty, leaderboard_score,
               leaderboard_rank, thesis, source_timeframe, created_at, expires_at,
               expected_cost, worst_case_loss, r_multiple, leverage_required,
               expected_hold_hours
        FROM pending_signals
        WHERE status = 'active'
        ORDER BY created_at DESC
        """,
    )
    setups = []
    if not df.empty:
        df["created_at"] = df["created_at"].astype(str)
        df["expires_at"] = df["expires_at"].astype(str)
        decimal_columns_to_float(df)
        setups = _records(df)

    pos = load_position(db, "live_kraken", strategy_name="", symbol="", exchange="kraken")
    account_state = current_account_state(pos, cfg)

    return jsonify({
        "setups": setups,
        "daily_room_remaining": float(account_state.daily_room_remaining),
        "lifetime_room_remaining": float(account_state.lifetime_room_remaining),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })
