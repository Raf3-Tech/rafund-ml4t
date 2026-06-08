"""Flask ops-dashboard for the Raf3nd ML4T model lifecycle.

Exposes a small single-page dashboard plus a JSON API for inspecting
production-model status and feature drift, and for triggering on-demand
retraining / drift-check actions. The app is built via the :func:`create_app`
application factory so a real :class:`DatabaseConnection` is never constructed
at import time.
"""

from __future__ import annotations

import dataclasses
import functools
import hmac
from typing import Any, Optional

import structlog
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from models.retraining_scheduler import run_drift_check, run_retraining_cycle
from monitoring.dashboard_data import (
    get_drift_status,
    get_model_status,
    row_to_dict,
)
from monitoring.drift_visualization import psi_bar_html, severity_color

logger = structlog.get_logger(__name__)

_DEFAULT_CONFIG: dict[str, Any] = {"retraining": {}}

# Upper bounds for free-text request fields so a malformed/abusive client
# cannot push arbitrarily large payloads into the scheduler/drift layers.
_MAX_MODEL_LEN = 256
_MAX_SYMBOL_LEN = 64


def _report_to_dict(report: Any) -> Optional[dict[str, Any]]:
    """Convert a DriftReport (or None) into a JSON-serializable dict."""
    if report is None:
        return None
    if dataclasses.is_dataclass(report) and not isinstance(report, type):
        return row_to_dict(report)
    return report


def create_app(
    db,
    config: Optional[dict[str, Any]] = None,
    *,
    api_token: Optional[str] = None,
    cors_origins: Optional[list[str]] = None,
) -> Flask:
    """Build the ops-dashboard Flask application.

    Args:
        db: A ``DatabaseConnection``-like handle passed through to the data
            and scheduler layers. Never constructed here.
        config: Retraining config dict shaped like
            ``{"retraining": {"models": [...], "symbols": [...]}}``. When
            ``None`` a no-op default is used so a retraining cycle returns [].
        api_token: Bearer token required on the mutating POST endpoints
            (``/api/retrain``, ``/api/drift-check``). When ``None`` those
            endpoints are left unprotected and a warning is logged at startup
            — set it in any networked deployment.
        cors_origins: Explicit allow-list of origins for the ``/api/*`` JSON
            endpoints. When ``None`` CORS is left wide-open (``*``) and a
            warning is logged; pass the frontend origin(s) in production.
    """
    app = Flask(__name__)
    # Never serve interactive debugger / stack traces over the network.
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

    def _require_token(view):
        """Gate a view behind the configured bearer token.

        No-op when no token is configured (local/dev and the existing test
        fixtures), so enabling auth is opt-in via ``api_token``. Uses a
        constant-time comparison to avoid leaking the token by timing.
        """

        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            token = app.config.get("API_TOKEN")
            if token:
                provided = request.headers.get("Authorization", "")
                if not hmac.compare_digest(provided, f"Bearer {token}"):
                    return jsonify({"error": "unauthorized"}), 401
            return view(*args, **kwargs)

        return wrapped

    @app.errorhandler(HTTPException)
    def _handle_http_exc(exc: HTTPException):
        # Keep routing/HTTP errors (404, 405, ...) as JSON so API clients
        # never receive Flask's HTML error pages.
        return jsonify({"error": exc.name, "status": exc.code}), exc.code

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        # Any uncaught error in a data/scheduler call becomes a JSON 500
        # instead of an HTML stack trace; the detail goes to the logs only.
        logger.error(
            "dashboard_unhandled_exception",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return jsonify({"error": "internal server error"}), 500

    @app.route("/", methods=["GET"])
    def index():
        model_rows = get_model_status(db)
        drift_rows = get_drift_status(db)
        return render_template(
            "dashboard.html",
            model_rows=model_rows,
            drift_rows=drift_rows,
        )

    @app.route("/drift", methods=["GET"])
    def drift_detail():
        """Per-model drift detail view.

        Uses query params (``?model=...&symbol=...``) rather than path
        segments because symbols contain ``/`` (e.g. ``BTC/USDT``). Never
        blocks trading: the drift check runs with ``block_on_critical=False``.
        """
        model = request.args.get("model")
        symbol = request.args.get("symbol")
        if not model or not symbol:
            return (
                render_template(
                    "drift_detail.html",
                    model=model,
                    symbol=symbol,
                    error="model and symbol are required",
                    report=None,
                    chart_html=None,
                ),
                400,
            )

        report = run_drift_check(db, model, symbol)
        logger.info(
            "dashboard_drift_detail_viewed",
            model=model,
            symbol=symbol,
            has_report=report is not None,
        )
        chart_html = psi_bar_html(report) if report is not None else None
        return render_template(
            "drift_detail.html",
            model=model,
            symbol=symbol,
            report=report,
            chart_html=chart_html,
            error=None,
        )

    @app.route("/api/models", methods=["GET"])
    def api_models():
        rows = get_model_status(db)
        return jsonify([row_to_dict(row) for row in rows])

    @app.route("/api/drift", methods=["GET"])
    def api_drift():
        rows = get_drift_status(db)
        return jsonify([row_to_dict(row) for row in rows])

    @app.route("/api/health", methods=["GET"])
    def api_health():
        # Lightweight liveness probe for load balancers / the React frontend.
        # Intentionally does not touch the DB so it stays cheap and stable.
        return jsonify({"status": "ok"}), 200

    @app.route("/api/retrain", methods=["POST"])
    @_require_token
    def api_retrain():
        cfg = app.config["RETRAINING_CONFIG"]
        results = run_retraining_cycle(db, cfg)
        logger.info("dashboard_retrain_triggered", count=len(results))
        return jsonify({"results": [dataclasses.asdict(r) for r in results]}), 200

    @app.route("/api/drift-check", methods=["POST"])
    @_require_token
    def api_drift_check():
        body = request.get_json(silent=True) or {}
        model = body.get("model")
        symbol = body.get("symbol")
        if not model or not symbol:
            return jsonify({"error": "model and symbol are required"}), 400
        if not isinstance(model, str) or not isinstance(symbol, str):
            return jsonify({"error": "model and symbol must be strings"}), 400
        if len(model) > _MAX_MODEL_LEN or len(symbol) > _MAX_SYMBOL_LEN:
            return jsonify({"error": "model or symbol too long"}), 400

        report = run_drift_check(db, model, symbol)
        logger.info(
            "dashboard_drift_check_triggered",
            model=model,
            symbol=symbol,
            has_report=report is not None,
        )
        return jsonify({"report": _report_to_dict(report)}), 200

    return app
