"""Ops API routes: model status, drift list, retrain, drift-check."""

from __future__ import annotations

import dataclasses

from flask import Blueprint, current_app, jsonify, request

from models.retraining_scheduler import run_drift_check, run_retraining_cycle
from monitoring.dashboard_data import get_drift_status, get_model_status, row_to_dict
from monitoring.routes._auth import require_token

import structlog

logger = structlog.get_logger(__name__)

bp = Blueprint("ops", __name__)

_MAX_MODEL_LEN = 256
_MAX_SYMBOL_LEN = 64


def _report_to_dict(report):
    if report is None:
        return None
    if dataclasses.is_dataclass(report) and not isinstance(report, type):
        return row_to_dict(report)
    return report


@bp.route("/api/models", methods=["GET"])
def api_models():
    db = current_app.config["DB"]
    rows = get_model_status(db)
    return jsonify([row_to_dict(row) for row in rows])


@bp.route("/api/drift", methods=["GET"])
def api_drift():
    db = current_app.config["DB"]
    rows = get_drift_status(db)
    return jsonify([row_to_dict(row) for row in rows])


@bp.route("/api/retrain", methods=["POST"])
@require_token
def api_retrain():
    db = current_app.config["DB"]
    cfg = current_app.config["RETRAINING_CONFIG"]
    results = run_retraining_cycle(db, cfg)
    logger.info("dashboard_retrain_triggered", count=len(results))
    return jsonify({"results": [dataclasses.asdict(r) for r in results]}), 200


@bp.route("/api/drift-check", methods=["POST"])
@require_token
def api_drift_check():
    db = current_app.config["DB"]
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
    logger.info("dashboard_drift_check_triggered", model=model, symbol=symbol, has_report=report is not None)
    return jsonify({"report": _report_to_dict(report)}), 200
