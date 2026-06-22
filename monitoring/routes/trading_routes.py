"""Live/paper trading status and chart routes — per-exchange position, signal,
recent orders, and OHLCV+trade-marker data for the dashboard's Trading tab."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from trading.paper_trader import SUPPORTED_EXCHANGES, _last_signal, _latest_bars, _top_strategy
from trading.position import load_position

bp = Blueprint("trading_routes", __name__)


def _position_dict(pos) -> dict:
    return {
        "run_id": pos.run_id,
        "strategy_name": pos.strategy_name,
        "symbol": pos.symbol,
        "side": pos.side,
        "qty": pos.qty,
        "entry_price": pos.entry_price,
        "equity": pos.equity,
        "peak_equity": pos.peak_equity,
        "daily_halt": pos.daily_halt,
        "account_failed": pos.account_failed,
        "last_update": pos.last_update.isoformat() if pos.last_update else None,
    }


def _current_signal(db, exchange: str) -> dict:
    """Re-derive the live BUY/SELL/HOLD signal the same way paper/live trading does."""
    result = _top_strategy(db)
    if result is None:
        return {"strategy_name": None, "symbol": None, "signal": None}
    strategy_name, symbol, params = result
    if symbol is None:
        return {"strategy_name": strategy_name, "symbol": None, "signal": None}
    try:
        from strategies.registry import StrategyRegistry
        strategy = StrategyRegistry.instantiate(strategy_name)
    except KeyError:
        return {"strategy_name": strategy_name, "symbol": symbol, "signal": None}
    n_bars = strategy.get_min_bars(params) * 2 + 10
    df = _latest_bars(db, symbol, n_bars, exchange=exchange)
    if df is None or len(df) < strategy.get_min_bars(params):
        return {"strategy_name": strategy_name, "symbol": symbol, "signal": None}
    signal = _last_signal(df, strategy_name, params)
    return {"strategy_name": strategy_name, "symbol": symbol, "signal": signal}


def _recent_orders(db, run_id: str, limit: int = 20) -> list[dict]:
    df = db.read_sql(
        """
        SELECT event, side, qty, price, pnl, order_type, setup_tag, close_reason, created_at
        FROM paper_orders
        WHERE run_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        [run_id, limit],
    )
    if df.empty:
        return []
    df["created_at"] = df["created_at"].astype(str)
    return df.to_dict(orient="records")


@bp.route("/api/trading-status", methods=["GET"])
def api_trading_status():
    db = current_app.config["DB"]
    mode = request.args.get("mode", "paper")
    if mode not in ("paper", "live"):
        return jsonify({"error": "mode must be 'paper' or 'live'"}), 400

    out: dict = {}
    for exchange in SUPPORTED_EXCHANGES:
        run_id = f"{mode}_{exchange}"
        pos = load_position(db, run_id, symbol="", strategy_name="", exchange=exchange)
        out[exchange] = {
            "position": _position_dict(pos),
            "current_signal": _current_signal(db, exchange),
            "recent_orders": _recent_orders(db, run_id),
        }
    return jsonify(out)


@bp.route("/api/trading-chart", methods=["GET"])
def api_trading_chart():
    db = current_app.config["DB"]
    exchange = request.args.get("exchange", "binance")
    mode = request.args.get("mode", "paper")
    limit = min(1000, max(10, int(request.args.get("limit", 300))))

    if exchange not in SUPPORTED_EXCHANGES:
        return jsonify({"error": f"exchange must be one of {SUPPORTED_EXCHANGES}"}), 400

    run_id = f"{mode}_{exchange}"
    pos = load_position(db, run_id, symbol="", strategy_name="", exchange=exchange)
    symbol = request.args.get("symbol") or pos.symbol
    if not symbol:
        signal = _current_signal(db, exchange)
        symbol = signal.get("symbol")
    if not symbol:
        return jsonify({"bars": [], "trades": []})

    bars_df = db.read_sql(
        """
        SELECT timestamp, open, high, low, close, volume
        FROM prices
        WHERE symbol = %s AND exchange = %s
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        [symbol, exchange, limit],
    )
    bars_df = bars_df.sort_values("timestamp") if not bars_df.empty else bars_df
    bars = []
    if not bars_df.empty:
        bars_df["timestamp"] = bars_df["timestamp"].astype(str)
        bars = bars_df.to_dict(orient="records")

    trades_df = db.read_sql(
        """
        SELECT event, side, qty, price, pnl, created_at
        FROM paper_orders
        WHERE run_id = %s AND symbol = %s
        ORDER BY created_at ASC
        LIMIT %s
        """,
        [run_id, symbol, limit],
    )
    trades = []
    if not trades_df.empty:
        trades_df["created_at"] = trades_df["created_at"].astype(str)
        trades = trades_df.to_dict(orient="records")

    return jsonify({"symbol": symbol, "bars": bars, "trades": trades})


@bp.route("/api/trade-journal-summary", methods=["GET"])
def api_trade_journal_summary():
    """Win rate and avg P&L per setup_tag, across all closed paper/live trades.

    Answers "what setups actually work" without manual SQL — the whole point
    of tagging trades in the first place.
    """
    db = current_app.config["DB"]
    try:
        df = db.read_sql("""
            SELECT setup_tag,
                   COUNT(*)                                   AS n_trades,
                   AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                   AVG(pnl)                                    AS avg_pnl,
                   SUM(pnl)                                    AS total_pnl
            FROM paper_orders
            WHERE event = 'CLOSE' AND pnl IS NOT NULL AND setup_tag IS NOT NULL
            GROUP BY setup_tag
            ORDER BY SUM(pnl) DESC
        """)
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "setup_tag": str(r["setup_tag"]),
                "n_trades": int(r["n_trades"]),
                "win_rate_pct": round(float(r["win_rate"]) * 100, 1),
                "avg_pnl": round(float(r["avg_pnl"]), 2),
                "total_pnl": round(float(r["total_pnl"]), 2),
            })
        return jsonify(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
