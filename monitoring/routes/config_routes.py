"""Prop-challenge config and research-decisions routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from config.paths import DECISIONS_LOG_PATH
from config.prop_config import read_prop_config, write_prop_config
from monitoring.routes._auth import require_token

import json
import structlog

logger = structlog.get_logger(__name__)

bp = Blueprint("config_routes", __name__)


@bp.route("/api/prop-config", methods=["GET"])
def api_get_prop_config():
    return jsonify(read_prop_config())


@bp.route("/api/prop-config", methods=["POST"])
@require_token
def api_set_prop_config():
    body = request.get_json(silent=True) or {}
    try:
        saved = write_prop_config(body)
        return jsonify({"ok": True, "config": saved})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/api/research-decisions", methods=["GET"])
def api_research_decisions():
    db = current_app.config["DB"]
    last = min(200, max(1, int(request.args.get("last", 50))))
    # Try DB first (available after migration 0004).
    try:
        rows = db.get_research_decisions(last)
        if rows:
            return jsonify(rows)
    except Exception:
        pass
    # Fall back to JSONL flat file.
    try:
        if not DECISIONS_LOG_PATH.exists():
            return jsonify([])
        lines = DECISIONS_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        tail = lines[-last:] if len(lines) > last else lines
        decisions = []
        for line in tail:
            try:
                decisions.append(json.loads(line))
            except Exception:
                pass
        return jsonify(list(reversed(decisions)))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
