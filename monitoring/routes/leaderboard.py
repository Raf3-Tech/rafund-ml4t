"""Leaderboard and engine-detail routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

import structlog

logger = structlog.get_logger(__name__)

bp = Blueprint("leaderboard", __name__)

# Tier column whitelist — safe to interpolate directly (validated against this set).
_TIER_COLS = {
    "conservative": "conservative_pass",
    "standard": "standard_pass",
    "permissive": "permissive_pass",
}


@bp.route("/api/leaderboard", methods=["GET"])
def api_leaderboard():
    db = current_app.config["DB"]
    tier = request.args.get("tier", "conservative").lower()
    pass_col = _TIER_COLS.get(tier, "conservative_pass")
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


@bp.route("/api/engine-detail", methods=["GET"])
def api_engine_detail():
    db = current_app.config["DB"]
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
        seen: set = set()
        for _, r in df.iterrows():
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
