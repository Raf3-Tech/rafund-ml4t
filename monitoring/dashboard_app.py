"""Flask dashboard for the RAFund ML4T pipeline.

Serves a full single-page control panel (6 tabs) plus the original ops API.
Long-running pipeline operations run as daemon threads via monitoring.jobs;
the frontend polls /api/jobs/<job_id> for status and log lines.

All route logic lives in monitoring/routes/ Blueprints — this file only
wires the application together.
"""

from __future__ import annotations

import functools
import hmac
from typing import Any, Optional

import structlog
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from monitoring.dashboard_data import (  # re-exported for backward-compat patching in tests
    get_drift_status,
    get_model_status,
    row_to_dict,
)
from models.retraining_scheduler import run_drift_check, run_retraining_cycle  # noqa: F401 — test patch target
from monitoring.drift_visualization import psi_bar_html, severity_color  # noqa: F401 — test patch target

logger = structlog.get_logger(__name__)

_DEFAULT_CONFIG: dict[str, Any] = {"retraining": {}}


# ── Application factory ───────────────────────────────────────────────────────

def create_app(
    db,
    config: Optional[dict[str, Any]] = None,
    *,
    api_token: Optional[str] = None,
    cors_origins: Optional[list[str]] = None,
) -> Flask:
    """Build the dashboard Flask application."""
    app = Flask(__name__)
    app.config["DEBUG"] = False
    app.config["DB"] = db
    app.config["RETRAINING_CONFIG"] = config if config is not None else _DEFAULT_CONFIG
    app.config["API_TOKEN"] = api_token or None

    if cors_origins:
        CORS(app, resources={r"/api/*": {"origins": cors_origins}})
    else:
        CORS(app)
        logger.warning("dashboard_cors_unrestricted")

    if not app.config["API_TOKEN"]:
        logger.warning("dashboard_api_token_unset_mutations_unprotected")

    # ── Error handlers ────────────────────────────────────────────────────────

    @app.errorhandler(HTTPException)
    def _handle_http_exc(exc: HTTPException):
        return jsonify({"error": exc.name, "status": exc.code}), exc.code

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        logger.error("dashboard_unhandled_exception", error=str(exc), error_type=type(exc).__name__)
        return jsonify({"error": "internal server error"}), 500

    # ── Register Blueprints ───────────────────────────────────────────────────

    from monitoring.routes.health import bp as health_bp
    from monitoring.routes.pages import bp as pages_bp
    from monitoring.routes.ops import bp as ops_bp
    from monitoring.routes.pipeline import bp as pipeline_bp
    from monitoring.routes.leaderboard import bp as leaderboard_bp
    from monitoring.routes.config_routes import bp as config_bp

    for blueprint in (health_bp, pages_bp, ops_bp, pipeline_bp, leaderboard_bp, config_bp):
        app.register_blueprint(blueprint)

    return app
