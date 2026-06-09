"""Flask dashboard for the RAFund ML4T pipeline.

Serves a full single-page control panel (6 tabs) plus the original ops
API.  Long-running pipeline operations (collect, engine, research) run as
daemon threads; the frontend polls /api/jobs/<job_id> for status and log
lines.

Existing endpoints are unchanged so existing tests stay green.
"""

from __future__ import annotations

import dataclasses
import functools
import hmac
import json
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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
_MAX_MODEL_LEN = 256
_MAX_SYMBOL_LEN = 64

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROP_CONFIG_PATH = _PROJECT_ROOT / "config" / "prop_challenge.json"
_DECISIONS_LOG = _PROJECT_ROOT / "tmp" / "research_decisions.jsonl"

# ── ANSI strip ────────────────────────────────────────────────────────────────
_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _clean(line: str) -> str:
    return _ANSI.sub("", line).rstrip()


# ── Job registry ──────────────────────────────────────────────────────────────
_JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_LOG_LINES = 300

# Tier column whitelist — used in leaderboard ORDER BY (safe to format directly).
_TIER_COLS = {
    "conservative": "conservative_pass",
    "standard": "standard_pass",
    "permissive": "permissive_pass",
}


def _run_job(job_id: str, cmd: list) -> None:
    with _JOBS_LOCK:
        _JOBS[job_id]["status"] = "running"
        _JOBS[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()

    lines: list = []
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with _JOBS_LOCK:
            _JOBS[job_id]["pid"] = proc.pid

        for raw in proc.stdout:  # type: ignore[union-attr]
            line = _clean(raw)
            if not line:
                continue
            lines.append(line)
            if len(lines) > _MAX_LOG_LINES:
                lines = lines[-_MAX_LOG_LINES:]
            with _JOBS_LOCK:
                _JOBS[job_id]["lines"] = list(lines)

        proc.wait()
        status = "done" if proc.returncode == 0 else "failed"
    except Exception as exc:
        lines.append(f"ERROR: {exc}")
        status = "failed"

    with _JOBS_LOCK:
        _JOBS[job_id]["status"] = status
        _JOBS[job_id]["lines"] = lines
        _JOBS[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


def _submit_job(job_type: str, cmd: list) -> str:
    job_id = str(uuid.uuid4())[:8]
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "type": job_type,
            "status": "pending",
            "lines": [],
            "started_at": None,
            "finished_at": None,
            "pid": None,
        }
    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return job_id


def _has_running_job(job_type: str) -> bool:
    with _JOBS_LOCK:
        return any(
            j["type"] == job_type and j["status"] in ("pending", "running")
            for j in _JOBS.values()
        )


# ── Prop-challenge config (UI-managed; separate from settings.yaml) ───────────
def _read_prop_config() -> dict:
    defaults = {
        "account_size": 5000.0,
        "profit_pct": 9.0,
        "daily_loss_pct": 3.0,
        "max_dd_pct": 3.0,
        "max_leverage": 5.0,
        "win_prob_target": 70.0,
    }
    if _PROP_CONFIG_PATH.exists():
        try:
            defaults.update(json.loads(_PROP_CONFIG_PATH.read_text()))
        except Exception:
            pass
    return defaults


def _write_prop_config(cfg: dict) -> dict:
    sanitized = {
        "account_size": max(100.0, float(cfg.get("account_size", 5000))),
        "profit_pct": max(0.1, min(50.0, float(cfg.get("profit_pct", 9)))),
        "daily_loss_pct": max(0.1, min(20.0, float(cfg.get("daily_loss_pct", 3)))),
        "max_dd_pct": max(0.1, min(20.0, float(cfg.get("max_dd_pct", 3)))),
        "max_leverage": max(1.0, min(100.0, float(cfg.get("max_leverage", 5)))),
        "win_prob_target": max(0.0, min(100.0, float(cfg.get("win_prob_target", 70)))),
    }
    _PROP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROP_CONFIG_PATH.write_text(json.dumps(sanitized, indent=2))
    return sanitized


# ── Application factory ───────────────────────────────────────────────────────
def create_app(
    db,
    config: Optional[dict[str, Any]] = None,
    *,
    api_token: Optional[str] = None,
    cors_origins: Optional[list[str]] = None,
) -> Flask:
    """Build the dashboard Flask application.

    Args:
        db: DatabaseConnection passed through to data and scheduler layers.
        config: Retraining config dict.  None → no-op default.
        api_token: Bearer token for mutating POST endpoints.  None → unprotected.
        cors_origins: Allowed CORS origins for /api/*.  None → wide-open.
    """
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

    def _require_token(view):
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
        return jsonify({"error": exc.name, "status": exc.code}), exc.code

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        logger.error("dashboard_unhandled_exception", error=str(exc), error_type=type(exc).__name__)
        return jsonify({"error": "internal server error"}), 500

    # ── Page routes ───────────────────────────────────────────────────────────

    @app.route("/", methods=["GET"])
    def index():
        model_rows = get_model_status(db)
        drift_rows = get_drift_status(db)
        return render_template("dashboard.html", model_rows=model_rows, drift_rows=drift_rows)

    @app.route("/drift", methods=["GET"])
    def drift_detail():
        model = request.args.get("model")
        symbol = request.args.get("symbol")
        if not model or not symbol:
            return (
                render_template("drift_detail.html", model=model, symbol=symbol,
                                error="model and symbol are required", report=None, chart_html=None),
                400,
            )
        report = run_drift_check(db, model, symbol)
        logger.info("dashboard_drift_detail_viewed", model=model, symbol=symbol, has_report=report is not None)
        chart_html = psi_bar_html(report) if report is not None else None
        return render_template("drift_detail.html", model=model, symbol=symbol,
                               report=report, chart_html=chart_html, error=None)

    # ── Original ops API (unchanged) ──────────────────────────────────────────

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
        logger.info("dashboard_drift_check_triggered", model=model, symbol=symbol, has_report=report is not None)
        return jsonify({"report": _report_to_dict(report)}), 200

    # ── Pipeline control API ──────────────────────────────────────────────────

    @app.route("/api/strategies", methods=["GET"])
    def api_strategies():
        try:
            import strategies as _strat_pkg  # noqa: F401 — populates registry
            from strategies.registry import StrategyRegistry
            return jsonify(StrategyRegistry.as_dict())
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/symbols", methods=["GET"])
    def api_symbols():
        try:
            df = db.read_sql("SELECT DISTINCT symbol FROM prices ORDER BY symbol")
            return jsonify(df["symbol"].tolist())
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/data-status", methods=["GET"])
    def api_data_status():
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

    @app.route("/api/collect", methods=["POST"])
    @_require_token
    def api_collect():
        body = request.get_json(silent=True) or {}
        funding = bool(body.get("funding", False))
        job_type = "collect_funding" if funding else "collect"
        if _has_running_job(job_type):
            return jsonify({"error": f"{job_type} already running"}), 409
        cmd = [sys.executable, "main.py", "collect"]
        if funding:
            cmd.append("--funding")
        job_id = _submit_job(job_type, cmd)
        logger.info("pipeline_collect_started", job_id=job_id, funding=funding)
        return jsonify({"job_id": job_id}), 202

    @app.route("/api/engine", methods=["POST"])
    @_require_token
    def api_engine():
        if _has_running_job("engine"):
            return jsonify({"error": "engine already running"}), 409
        body = request.get_json(silent=True) or {}
        cmd = [sys.executable, "main.py", "engine"]
        strategy = body.get("strategy", "")
        symbol = body.get("symbol", "")
        if strategy and isinstance(strategy, str) and len(strategy) < 128:
            cmd += ["--strategy", strategy]
        if symbol and isinstance(symbol, str) and len(symbol) < 64:
            cmd += ["--symbol", symbol]
        job_id = _submit_job("engine", cmd)
        logger.info("pipeline_engine_started", job_id=job_id, strategy=strategy, symbol=symbol)
        return jsonify({"job_id": job_id}), 202

    @app.route("/api/research", methods=["POST"])
    @_require_token
    def api_research():
        if _has_running_job("research"):
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
        job_id = _submit_job("research", cmd)
        logger.info("pipeline_research_started", job_id=job_id)
        return jsonify({"job_id": job_id}), 202

    @app.route("/api/jobs/<job_id>", methods=["GET"])
    def api_job_status(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        return jsonify(dict(job))

    @app.route("/api/leaderboard", methods=["GET"])
    def api_leaderboard():
        tier = request.args.get("tier", "conservative").lower()
        pass_col = _TIER_COLS.get(tier, "conservative_pass")  # whitelist-validated
        try:
            df = db.read_sql(f"""
                SELECT strategy_name, symbol,
                       AVG(CASE WHEN conservative_pass THEN 1.0 ELSE 0.0 END) AS cons_ratio,
                       AVG(CASE WHEN standard_pass     THEN 1.0 ELSE 0.0 END) AS std_ratio,
                       AVG(CASE WHEN permissive_pass   THEN 1.0 ELSE 0.0 END) AS perm_ratio,
                       COUNT(*)               AS n_windows,
                       AVG(sharpe_ratio)      AS avg_sharpe,
                       AVG(max_drawdown_pct)  AS avg_dd,
                       AVG(total_return_pct)  AS avg_ret,
                       AVG(win_rate_pct)      AS avg_win_rate
                FROM engine_results
                WHERE num_trades >= 2
                GROUP BY strategy_name, symbol
                HAVING COUNT(*) >= 5
                ORDER BY AVG(CASE WHEN {pass_col} THEN 1.0 ELSE 0.0 END) DESC,
                         AVG(sharpe_ratio) DESC
                LIMIT 60
            """)
            rows = []
            for _, r in df.iterrows():
                rows.append({
                    "strategy": str(r["strategy_name"]),
                    "symbol": str(r["symbol"]),
                    "cons_pct": round(float(r["cons_ratio"]) * 100, 1),
                    "std_pct": round(float(r["std_ratio"]) * 100, 1),
                    "perm_pct": round(float(r["perm_ratio"]) * 100, 1),
                    "n_windows": int(r["n_windows"]),
                    "avg_sharpe": round(float(r["avg_sharpe"]), 3),
                    "avg_dd": round(float(r["avg_dd"]), 1),
                    "avg_ret": round(float(r["avg_ret"]), 1),
                    "avg_win_rate": round(float(r["avg_win_rate"]), 1),
                })
            return jsonify(rows)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/engine-detail", methods=["GET"])
    def api_engine_detail():
        strategy = request.args.get("strategy", "")
        symbol = request.args.get("symbol", "")
        if not strategy or not symbol:
            return jsonify({"error": "strategy and symbol required"}), 400
        try:
            df = db.read_sql(
                """
                SELECT window_start, window_end, window_years, window_type,
                       total_return_pct, max_drawdown_pct, sharpe_ratio,
                       win_rate_pct, num_trades,
                       conservative_pass, standard_pass, permissive_pass,
                       params
                FROM engine_results
                WHERE strategy_name = %s AND symbol = %s
                ORDER BY window_end DESC, mutation_generation ASC
                LIMIT 200
                """,
                [strategy, symbol],
            )
            rows = []
            seen = set()
            for _, r in df.iterrows():
                # Deduplicate same window by keeping first (lowest mutation gen)
                key = (str(r["window_start"]), str(r["window_end"]))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "start": str(r["window_start"])[:10],
                    "end": str(r["window_end"])[:10],
                    "years": round(float(r["window_years"]), 1),
                    "type": str(r["window_type"]),
                    "ret": round(float(r["total_return_pct"]), 2),
                    "dd": round(float(r["max_drawdown_pct"]), 2),
                    "sharpe": round(float(r["sharpe_ratio"]), 3),
                    "wr": round(float(r["win_rate_pct"]), 1),
                    "trades": int(r["num_trades"]),
                    "cons": bool(r["conservative_pass"]),
                    "std": bool(r["standard_pass"]),
                    "perm": bool(r["permissive_pass"]),
                    "params": r["params"] if isinstance(r["params"], dict) else {},
                })
            return jsonify(rows)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/prop-config", methods=["GET"])
    def api_get_prop_config():
        return jsonify(_read_prop_config())

    @app.route("/api/prop-config", methods=["POST"])
    @_require_token
    def api_set_prop_config():
        body = request.get_json(silent=True) or {}
        try:
            saved = _write_prop_config(body)
            return jsonify({"ok": True, "config": saved})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/research-decisions", methods=["GET"])
    def api_research_decisions():
        try:
            last = min(200, max(1, int(request.args.get("last", 50))))
            if not _DECISIONS_LOG.exists():
                return jsonify([])
            lines = _DECISIONS_LOG.read_text(encoding="utf-8").strip().splitlines()
            tail = lines[-last:] if len(lines) > last else lines
            decisions = []
            for line in tail:
                try:
                    decisions.append(json.loads(line))
                except Exception:
                    pass
            return jsonify(list(reversed(decisions)))  # newest-first
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return app


def _report_to_dict(report: Any) -> Optional[dict[str, Any]]:
    if report is None:
        return None
    if dataclasses.is_dataclass(report) and not isinstance(report, type):
        return row_to_dict(report)
    return report
