"""Pipeline control routes: collect, engine, research, jobs, strategies, symbols, data-status."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from monitoring.jobs import get_job, has_running_job, submit_job
from monitoring.routes._auth import require_token

import structlog

logger = structlog.get_logger(__name__)

bp = Blueprint("pipeline", __name__)


@bp.route("/api/strategies", methods=["GET"])
def api_strategies():
    try:
        import strategies as _strat_pkg  # noqa: F401 — populates registry
        from strategies.registry import StrategyRegistry
        return jsonify(StrategyRegistry.as_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/symbols", methods=["GET"])
def api_symbols():
    db = current_app.config["DB"]
    try:
        df = db.read_sql("SELECT DISTINCT symbol FROM prices ORDER BY symbol")
        return jsonify(df["symbol"].tolist())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/data-status", methods=["GET"])
def api_data_status():
    db = current_app.config["DB"]
    try:
        df = db.read_sql("""
            SELECT symbol,
                   MIN(timestamp) AS earliest,
                   MAX(timestamp) AS latest,
                   COUNT(*)       AS bars
            FROM prices
            GROUP BY symbol
            ORDER BY symbol
        """)
        now = datetime.now(timezone.utc)
        rows = []
        for _, r in df.iterrows():
            latest = r["latest"]
            if hasattr(latest, "tzinfo") and latest.tzinfo is None:
                import pandas as pd
                latest = pd.Timestamp(latest).tz_localize("UTC")
            stale = max(0, (now - latest).days)
            rows.append({
                "symbol": r["symbol"],
                "earliest": str(r["earliest"])[:10],
                "latest": str(r["latest"])[:10],
                "bars": int(r["bars"]),
                "stale_days": stale,
                "status": "fresh" if stale <= 1 else ("stale" if stale <= 7 else "old"),
            })
        return jsonify(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/collect", methods=["POST"])
@require_token
def api_collect():
    body = request.get_json(silent=True) or {}
    funding = bool(body.get("funding", False))
    job_type = "collect_funding" if funding else "collect"
    if has_running_job(job_type):
        return jsonify({"error": f"{job_type} already running"}), 409
    cmd = [sys.executable, "main.py", "collect"]
    if funding:
        cmd.append("--funding")
    job_id = submit_job(job_type, cmd)
    logger.info("pipeline_collect_started", job_id=job_id, funding=funding)
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/engine", methods=["POST"])
@require_token
def api_engine():
    if has_running_job("engine"):
        return jsonify({"error": "engine already running"}), 409
    body = request.get_json(silent=True) or {}
    cmd = [sys.executable, "main.py", "engine"]
    strategy = body.get("strategy", "")
    symbol = body.get("symbol", "")
    if strategy and isinstance(strategy, str) and len(strategy) < 128:
        cmd += ["--strategy", strategy]
    if symbol and isinstance(symbol, str) and len(symbol) < 64:
        cmd += ["--symbol", symbol]
    job_id = submit_job("engine", cmd)
    logger.info("pipeline_engine_started", job_id=job_id, strategy=strategy, symbol=symbol)
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/research", methods=["POST"])
@require_token
def api_research():
    if has_running_job("research"):
        return jsonify({"error": "research already running"}), 409
    body = request.get_json(silent=True) or {}
    cmd = [sys.executable, "main.py", "research", "--dry-run"]
    tier = body.get("tier", "conservative")
    if tier in ("conservative", "standard", "permissive"):
        cmd += ["--tier", tier]
    top_n = min(50, max(1, int(body.get("top_n", 10))))
    cmd += ["--top-n", str(top_n)]
    strategy = body.get("strategy", "")
    if strategy and isinstance(strategy, str) and len(strategy) < 128:
        cmd += ["--strategy", strategy]
    job_id = submit_job("research", cmd)
    logger.info("pipeline_research_started", job_id=job_id)
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/jobs/<job_id>", methods=["GET"])
def api_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)
