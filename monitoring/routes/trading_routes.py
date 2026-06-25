"""Live/paper trading status and chart routes — per-exchange position, signal,
recent orders, and OHLCV+trade-marker data for the dashboard's Trading tab."""

from __future__ import annotations

import os
import sys
from typing import Optional

from flask import Blueprint, current_app, jsonify, request

from monitoring.jobs import has_running_job, submit_job
from monitoring.routes._auth import require_token
from trading.paper_trader import (
    SUPPORTED_EXCHANGES,
    _last_signal,
    _latest_bars,
    force_close_now,
    resume_trading,
)
from trading.position import list_positions, load_position

bp = Blueprint("trading_routes", __name__)


def _risk_dict(pos) -> dict:
    """How close this position is to its daily-loss / drawdown limits."""
    from config.loader import get_settings
    cfg = get_settings()

    daily_pnl = pos.equity - pos.daily_start_equity
    daily_limit = cfg.account_size * cfg.max_daily_loss_pct
    daily_used = max(0.0, -daily_pnl) / daily_limit * 100 if daily_limit > 0 else 0.0

    drawdown = pos.peak_equity - pos.equity
    dd_limit = cfg.account_size * cfg.max_drawdown_pct
    dd_used = max(0.0, drawdown) / dd_limit * 100 if dd_limit > 0 else 0.0

    return {
        "daily_loss_pct_used": round(min(daily_used, 999.0), 1),
        "daily_loss_limit_pct": round(cfg.max_daily_loss_pct * 100, 1),
        "drawdown_pct_used": round(min(dd_used, 999.0), 1),
        "drawdown_limit_pct": round(cfg.max_drawdown_pct * 100, 1),
    }


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
        "daily_pnl": round(pos.equity - pos.daily_start_equity, 2),
        "daily_halt": pos.daily_halt,
        "account_failed": pos.account_failed,
        "manual_halt": pos.manual_halt,
        "last_update": pos.last_update.isoformat() if pos.last_update else None,
        "risk": _risk_dict(pos),
    }


def _signal_for_position(db, exchange: str, pos, params: dict) -> dict:
    """Re-derive the live BUY/SELL/HOLD signal for this position's own
    strategy/symbol — each paper slot can now run a different strategy, so
    there's no longer a single global "top strategy" to re-derive from.
    `params` should be the same params the slot was opened/sized with (from
    _paper_candidates / _top_accepted_strategy), else generate_signals would
    silently fall back to default params and could show a different signal
    than what's actually being traded."""
    strategy_name, symbol = pos.strategy_name, pos.symbol
    if not strategy_name or not symbol:
        return {"strategy_name": strategy_name or None, "symbol": symbol or None, "signal": None}
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


def _records(df) -> list[dict]:
    """df.to_dict(orient="records"), with NaN (pandas' rendering of SQL NULL
    in numeric columns, e.g. pnl on OPEN rows) swapped for None — NaN is not
    valid JSON, so jsonify() would emit a bare `NaN` token that browsers'
    strict JSON parser rejects outright."""
    import pandas as pd
    # astype(object) first — df.where(..., None) on a still-numeric column
    # just re-coerces None back to NaN rather than actually storing None.
    return df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")


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
    return _records(df)


@bp.route("/api/trading-status", methods=["GET"])
def api_trading_status():
    db = current_app.config["DB"]
    mode = request.args.get("mode", "paper")
    if mode not in ("paper", "live"):
        return jsonify({"error": "mode must be 'paper' or 'live'"}), 400

    if mode == "paper":
        from trading.paper_trader import _paper_candidates
        candidate_params = {(name, symbol): params for name, symbol, params in _paper_candidates(db)}
    else:
        from trading.paper_trader import _top_accepted_strategy
        accepted = _top_accepted_strategy(db)
        candidate_params = {(accepted[0], accepted[1]): accepted[2]} if accepted else {}

    out: dict = {}
    for exchange in SUPPORTED_EXCHANGES:
        if mode == "paper":
            positions = list_positions(db, exchange, run_id_prefix=f"paper_{exchange}")
        else:
            positions = [load_position(db, f"live_{exchange}", symbol="", strategy_name="", exchange=exchange)]
        out[exchange] = {
            "positions": [
                {
                    **_position_dict(pos),
                    "current_signal": _signal_for_position(
                        db, exchange, pos, candidate_params.get((pos.strategy_name, pos.symbol), {}),
                    ),
                    "recent_orders": _recent_orders(db, pos.run_id),
                }
                for pos in positions
            ],
        }
    return jsonify(out)


def _default_run_id(db, exchange: str, mode: str) -> Optional[str]:
    """First/only open slot for an exchange+mode — live always has exactly
    one (`live_{exchange}`); paper may have several, so pick the first."""
    if mode == "live":
        return f"live_{exchange}"
    positions = list_positions(db, exchange, run_id_prefix=f"paper_{exchange}")
    return positions[0].run_id if positions else None


@bp.route("/api/trading-chart", methods=["GET"])
def api_trading_chart():
    db = current_app.config["DB"]
    exchange = request.args.get("exchange", "binance")
    mode = request.args.get("mode", "paper")
    limit = min(1000, max(10, int(request.args.get("limit", 300))))

    if exchange not in SUPPORTED_EXCHANGES:
        return jsonify({"error": f"exchange must be one of {SUPPORTED_EXCHANGES}"}), 400

    run_id = request.args.get("run_id") or _default_run_id(db, exchange, mode)
    if not run_id:
        return jsonify({"bars": [], "trades": []})
    pos = load_position(db, run_id, symbol="", strategy_name="", exchange=exchange)
    symbol = request.args.get("symbol") or pos.symbol
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
        bars = _records(bars_df)

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
        trades = _records(trades_df)

    return jsonify({"symbol": symbol, "bars": bars, "trades": trades})


@bp.route("/api/trade-journal-summary", methods=["GET"])
def api_trade_journal_summary():
    """Win rate and avg P&L per setup_tag, across closed paper/live trades.

    Answers "what setups actually work" without manual SQL — the whole point
    of tagging trades in the first place. Optional filters narrow the same
    question to a venue, mode, date range, or win/loss outcome.
    """
    db = current_app.config["DB"]
    mode = request.args.get("mode") or None
    exchange = request.args.get("exchange") or None
    start = request.args.get("start") or None
    end = request.args.get("end") or None
    outcome = request.args.get("outcome") or None

    if mode and mode not in ("paper", "live"):
        return jsonify({"error": "mode must be 'paper' or 'live'"}), 400
    if exchange and exchange not in SUPPORTED_EXCHANGES:
        return jsonify({"error": f"exchange must be one of {SUPPORTED_EXCHANGES}"}), 400
    if outcome and outcome not in ("win", "loss"):
        return jsonify({"error": "outcome must be 'win' or 'loss'"}), 400

    # setup_tag is only ever recorded on OPEN rows (it's "why this trade was
    # taken"; CLOSE rows record close_reason instead) — pull it from the
    # OPEN that most recently preceded each CLOSE on the same run_id, rather
    # than filtering CLOSE.setup_tag directly (which is always NULL and
    # would make this query return nothing).
    where = ["c.event = 'CLOSE'", "c.pnl IS NOT NULL", "o.setup_tag IS NOT NULL"]
    params: list = []
    if mode:
        where.append("c.run_id LIKE %s")
        params.append(f"{mode}_%")
    if exchange:
        where.append("c.exchange = %s")
        params.append(exchange)
    if start:
        where.append("c.created_at >= %s")
        params.append(start)
    if end:
        where.append("c.created_at <= %s")
        params.append(end)
    if outcome == "win":
        where.append("c.pnl > 0")
    elif outcome == "loss":
        where.append("c.pnl <= 0")

    sql = f"""
        SELECT o.setup_tag                                   AS setup_tag,
               COUNT(*)                                       AS n_trades,
               AVG(CASE WHEN c.pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
               AVG(c.pnl)                                      AS avg_pnl,
               SUM(c.pnl)                                      AS total_pnl
        FROM paper_orders c
        JOIN LATERAL (
            SELECT setup_tag FROM paper_orders op
            WHERE op.run_id = c.run_id AND op.event = 'OPEN' AND op.created_at <= c.created_at
            ORDER BY op.created_at DESC
            LIMIT 1
        ) o ON TRUE
        WHERE {' AND '.join(where)}
        GROUP BY o.setup_tag
        ORDER BY SUM(c.pnl) DESC
    """
    try:
        df = db.read_sql(sql, params)
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


@bp.route("/api/kill-switch", methods=["POST"])
@require_token
def api_kill_switch():
    """Flatten one slot, one exchange (every paper slot on it), or all three
    exchanges, and halt new opens until resumed."""
    db = current_app.config["DB"]
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "paper")
    if mode not in ("paper", "live"):
        return jsonify({"error": "mode must be 'paper' or 'live'"}), 400
    exchange = body.get("exchange")
    if exchange is not None and exchange not in SUPPORTED_EXCHANGES:
        return jsonify({"error": f"exchange must be one of {SUPPORTED_EXCHANGES}"}), 400
    run_id = body.get("run_id")

    exchanges = [exchange] if exchange else list(SUPPORTED_EXCHANGES)
    closed, errors = [], []
    for ex in exchanges:
        try:
            result = force_close_now(db, ex, mode=mode, run_id=run_id)
            results = result if isinstance(result, list) else [result]
            for r in results:
                (errors if r.get("error") else closed).append(r)
        except Exception as exc:
            errors.append({"exchange": ex, "error": str(exc)})

    import structlog
    structlog.get_logger(__name__).warning(
        "dashboard_kill_switch_triggered", mode=mode, exchanges=exchanges,
        closed=len(closed), errors=len(errors),
    )
    return jsonify({"closed": closed, "errors": errors}), 200


@bp.route("/api/resume", methods=["POST"])
@require_token
def api_resume():
    """Clear manual_halt for one slot, one exchange (every paper slot on it),
    or all three exchanges, so trading can restart."""
    db = current_app.config["DB"]
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "paper")
    if mode not in ("paper", "live"):
        return jsonify({"error": "mode must be 'paper' or 'live'"}), 400
    exchange = body.get("exchange")
    if exchange is not None and exchange not in SUPPORTED_EXCHANGES:
        return jsonify({"error": f"exchange must be one of {SUPPORTED_EXCHANGES}"}), 400
    run_id = body.get("run_id")

    exchanges = [exchange] if exchange else list(SUPPORTED_EXCHANGES)
    resumed = []
    for ex in exchanges:
        result = resume_trading(db, ex, mode=mode, run_id=run_id)
        resumed.extend(result if isinstance(result, list) else [result])

    import structlog
    structlog.get_logger(__name__).warning(
        "dashboard_resume_triggered", mode=mode, exchanges=exchanges,
    )
    return jsonify({"resumed": resumed}), 200


@bp.route("/api/equity-curve", methods=["GET"])
def api_equity_curve():
    """Cumulative equity from closed trades vs. a buy-and-hold benchmark."""
    db = current_app.config["DB"]
    exchange = request.args.get("exchange", "binance")
    mode = request.args.get("mode", "paper")
    if exchange not in SUPPORTED_EXCHANGES:
        return jsonify({"error": f"exchange must be one of {SUPPORTED_EXCHANGES}"}), 400
    if mode not in ("paper", "live"):
        return jsonify({"error": "mode must be 'paper' or 'live'"}), 400

    from config.loader import get_settings
    cfg = get_settings()
    run_id = request.args.get("run_id") or _default_run_id(db, exchange, mode)
    if not run_id:
        return jsonify({"equity": [], "benchmark": []})

    closes_df = db.read_sql(
        """
        SELECT symbol, pnl, created_at
        FROM paper_orders
        WHERE run_id = %s AND event = 'CLOSE' AND pnl IS NOT NULL
        ORDER BY created_at ASC
        """,
        [run_id],
    )
    if closes_df.empty:
        return jsonify({"equity": [], "benchmark": []})

    closes_df["created_at"] = closes_df["created_at"].astype(str)
    equity = cfg.account_size
    equity_series = []
    for _, r in closes_df.iterrows():
        equity += float(r["pnl"])
        equity_series.append({"timestamp": r["created_at"], "equity": round(equity, 2)})

    symbol = str(closes_df["symbol"].iloc[-1])
    start_ts = closes_df["created_at"].iloc[0]
    prices_df = db.read_sql(
        """
        SELECT timestamp, close
        FROM prices
        WHERE symbol = %s AND exchange = %s AND timestamp >= %s
        ORDER BY timestamp ASC
        """,
        [symbol, exchange, start_ts],
    )
    benchmark = []
    if not prices_df.empty:
        first_close = float(prices_df["close"].iloc[0])
        prices_df["timestamp"] = prices_df["timestamp"].astype(str)
        for _, r in prices_df.iterrows():
            bench_equity = cfg.account_size * (float(r["close"]) / first_close)
            benchmark.append({"timestamp": r["timestamp"], "equity": round(bench_equity, 2)})

    return jsonify({"equity": equity_series, "benchmark": benchmark})


@bp.route("/api/trading-config", methods=["GET"])
def api_trading_config():
    """Informational only — the dashboard cannot flip this, it's a server env var."""
    return jsonify({"live_trading_enabled": os.environ.get("LIVE_TRADING_ENABLED", "0").strip() == "1"})


@bp.route("/api/paper-cycle", methods=["POST"])
@require_token
def api_paper_cycle():
    if has_running_job("paper_cycle"):
        return jsonify({"error": "paper cycle already running"}), 409
    body = request.get_json(silent=True) or {}
    cmd = [sys.executable, "main.py", "paper"]
    exchange = body.get("exchange")
    if exchange and exchange in SUPPORTED_EXCHANGES:
        cmd += ["--exchange", exchange]
    job_id = submit_job("paper_cycle", cmd)
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/paper-backfill", methods=["POST"])
@require_token
def api_paper_backfill():
    """Replay paper trading over already-collected price history so the
    journal/equity-curve populate with real results immediately, instead of
    waiting for live signals to trickle in one bar at a time."""
    if has_running_job("paper_backfill"):
        return jsonify({"error": "paper backfill already running"}), 409
    body = request.get_json(silent=True) or {}
    days = min(365, max(1, int(body.get("lookback_days", 180))))
    cmd = [sys.executable, "main.py", "paper", "--replay-days", str(days)]
    exchange = body.get("exchange")
    if exchange and exchange in SUPPORTED_EXCHANGES:
        cmd += ["--exchange", exchange]
    job_id = submit_job("paper_backfill", cmd)
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/live-cycle", methods=["POST"])
@require_token
def api_live_cycle():
    if has_running_job("live_cycle"):
        return jsonify({"error": "live cycle already running"}), 409
    body = request.get_json(silent=True) or {}
    exchange = body.get("exchange")
    if not exchange or exchange not in SUPPORTED_EXCHANGES:
        return jsonify({"error": f"exchange is required and must be one of {SUPPORTED_EXCHANGES}"}), 400
    cmd = [sys.executable, "main.py", "live", "--exchange", exchange]
    job_id = submit_job("live_cycle", cmd)
    return jsonify({"job_id": job_id}), 202
