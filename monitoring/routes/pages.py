"""Server-rendered page routes: dashboard index and drift detail."""

from __future__ import annotations

from flask import Blueprint, current_app, render_template, request

from models.retraining_scheduler import run_drift_check
from monitoring.dashboard_data import get_drift_status, get_model_status
from monitoring.drift_visualization import psi_bar_html

import structlog

logger = structlog.get_logger(__name__)

bp = Blueprint("pages", __name__)


@bp.route("/", methods=["GET"])
def index():
    db = current_app.config["DB"]
    model_rows = get_model_status(db)
    drift_rows = get_drift_status(db)
    return render_template("dashboard.html", model_rows=model_rows, drift_rows=drift_rows)


@bp.route("/drift", methods=["GET"])
def drift_detail():
    db = current_app.config["DB"]
    model = request.args.get("model")
    symbol = request.args.get("symbol")
    if not model or not symbol:
        return (
            render_template(
                "drift_detail.html", model=model, symbol=symbol,
                error="model and symbol are required", report=None, chart_html=None,
            ),
            400,
        )
    report = run_drift_check(db, model, symbol)
    logger.info("dashboard_drift_detail_viewed", model=model, symbol=symbol, has_report=report is not None)
    chart_html = psi_bar_html(report) if report is not None else None
    return render_template(
        "drift_detail.html", model=model, symbol=symbol,
        report=report, chart_html=chart_html, error=None,
    )
