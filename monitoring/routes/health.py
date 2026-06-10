from flask import Blueprint, jsonify

bp = Blueprint("health", __name__)


@bp.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"status": "ok"}), 200
