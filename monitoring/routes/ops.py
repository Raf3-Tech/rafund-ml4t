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


@bp.route("/api/model-confidence", methods=["GET"])
def api_model_confidence():
    """Regime classifier's pass-probability + feature importance for a symbol.

    Scoped to the regime classifier specifically (not an arbitrary production
    model) because its feature-engineering is fully known (models/regime_classifier.py
    `_prepare_features`) — surfacing a confidence number for an arbitrary MLflow
    model would require guessing its feature schema, which risks showing a
    mislabeled or wrong value. This is real, not fabricated: it's the same
    `predict_proba` call regime_classifier already makes internally.
    """
    import pickle
    from models.regime_classifier import MODEL_PATH, _prepare_features

    db = current_app.config["DB"]
    symbol = request.args.get("symbol")
    target = request.args.get("target", "standard")
    if not symbol:
        return jsonify({"available": False, "reason": "symbol is required"}), 400
    if not MODEL_PATH.exists():
        return jsonify({"available": False, "reason": "regime classifier not trained yet"})

    row_df = db.read_sql(
        """
        SELECT regime_trend, regime_volatility, regime_direction, window_years, symbol
        FROM engine_results
        WHERE symbol = %s AND regime_trend IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [symbol],
    )
    if row_df.empty:
        return jsonify({"available": False, "reason": f"no regime data yet for {symbol}"})

    with open(MODEL_PATH, "rb") as f:
        bundle = pickle.load(f)
    model = bundle.get(f"model_{target}") or bundle.get("model")
    if model is None:
        return jsonify({"available": False, "reason": "no trained model in bundle"})

    encoder_state = bundle.get("encoder_state", [])
    X, feature_names, _ = _prepare_features(row_df, fit_encoder=False, encoder_state=encoder_state)
    try:
        confidence = float(model.predict_proba(X)[:, 1][0])
    except Exception as exc:
        return jsonify({"available": False, "reason": str(exc)}), 500

    importances = None
    if hasattr(model, "feature_importances_"):
        importances = dict(zip(feature_names, [float(v) for v in model.feature_importances_]))
    elif hasattr(model, "coef_"):
        coefs = model.coef_[0] if model.coef_.ndim > 1 else model.coef_
        importances = dict(zip(feature_names, [float(v) for v in coefs]))

    top_features = None
    if importances:
        top_features = dict(sorted(importances.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5])

    return jsonify({
        "available": True,
        "symbol": symbol,
        "target": target,
        "confidence": round(confidence, 4),
        "feature_importance": top_features,
    })


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
