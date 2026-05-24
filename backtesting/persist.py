"""
Persist backtest / evaluation outputs into PostgreSQL per schema.sql.
"""

import logging
from datetime import datetime
from typing import Dict

import pandas as pd

from backtesting.engine_eval import EvaluationBacktestEngine

logger = logging.getLogger(__name__)


def spread_symbol_label(symbol_a: str, symbol_b: str) -> str:
    return f"{symbol_a}|{symbol_b}"


def persist_evaluation_run(db, results: Dict, backtest_id: str) -> Dict[str, int]:
    """
    Write features, signals, trades, portfolio, and backtest_results.

    Returns:
        Dict of table_name -> rows written
    """
    symbol_a = results["symbol_a"]
    symbol_b = results["symbol_b"]
    pair_label = spread_symbol_label(symbol_a, symbol_b)
    counts = {}

    signals_df = results.get("signals_df", pd.DataFrame())
    if signals_df.empty:
        logger.warning("No signals_df to persist")
        return counts

    # --- features ---
    db.delete_features_for_pair(symbol_a, symbol_b)
    features_df = pd.DataFrame(
        {
            "symbol_a": symbol_a,
            "symbol_b": symbol_b,
            "timestamp": signals_df["timestamp"],
            "spread": signals_df["spread"],
            "spread_mean": signals_df["spread_mean"],
            "spread_std": signals_df["spread_std"],
            "z_score": signals_df["z_score"],
            "hedge_ratio": signals_df["hedge_ratio"],
        }
    )
    counts["features"] = db.insert_features(features_df)

    # --- signals (enum: BUY, SELL, HOLD, CLOSE) ---
    db.delete_signals_for_pair(symbol_a, symbol_b)
    db_signals = EvaluationBacktestEngine.signals_for_database(
        signals_df, symbol_a, symbol_b
    )
    counts["signals"] = db.insert_signals(db_signals)

    # --- trades ---
    db.delete_trades_for_symbol(pair_label)
    trade_rows = []
    open_trade = None
    initial = results["initial_capital"]

    for t in results.get("trades", []):
        if t.get("status") == "OPEN":
            open_trade = t
        elif t.get("status") == "CLOSED" and open_trade:
            pnl = t.get("pnl")
            trade_rows.append(
                {
                    "symbol": pair_label,
                    "trade_date": open_trade["date"],
                    "entry_price": float(open_trade["price_a"]),
                    "exit_price": float(t["price_a"]),
                    "quantity": max(1, int(abs(open_trade.get("qty_a", 1)))),
                    "direction": "LONG" if "LONG" in open_trade.get("side", "") else "SHORT",
                    "pnl": float(pnl) if pnl is not None else None,
                    "return_pct": (float(pnl) / initial * 100) if pnl is not None and initial else None,
                    "status": "CLOSED",
                }
            )
            open_trade = None

    if trade_rows:
        counts["trades"] = db.insert_trades(pd.DataFrame(trade_rows))
    else:
        counts["trades"] = 0

    # --- portfolio (daily equity curve, keyed by backtest_id) ---
    portfolio_symbol = f"ACCOUNT|{backtest_id[-12:]}"
    if len(portfolio_symbol) > 20:
        portfolio_symbol = portfolio_symbol[-20:]
    db.delete_portfolio_for_symbol(portfolio_symbol)
    portfolio_rows = []
    for ts, equity in results.get("daily_equity", []):
        portfolio_rows.append(
            {
                "timestamp": ts,
                "symbol": portfolio_symbol,
                "position_size": 1,
                "entry_price": initial,
                "current_price": float(equity),
                "unrealized_pnl": float(equity) - initial,
            }
        )
    if portfolio_rows:
        counts["portfolio"] = db.insert_portfolio(pd.DataFrame(portfolio_rows))
    else:
        counts["portfolio"] = 0

    # --- backtest_results ---
    start = pd.Timestamp(signals_df["timestamp"].min()).date()
    end = pd.Timestamp(signals_df["timestamp"].max()).date()
    eval_status = results.get("evaluation", {}).get("status", "UNKNOWN")
    db_results = {
        "backtest_id": backtest_id,
        "start_date": start,
        "end_date": end,
        "initial_capital": results["initial_capital"],
        "final_value": results["final_value"],
        "total_return": results["total_return"],
        "sharpe_ratio": results["sharpe_ratio"],
        "max_drawdown": results["max_drawdown"],
        "num_trades": results["num_closed_trades"],
        "win_rate": results["win_rate"],
    }
    if db.insert_backtest_results(db_results):
        counts["backtest_results"] = 1
    else:
        counts["backtest_results"] = 0

    logger.info(
        "Persisted evaluation run %s (%s): %s",
        backtest_id,
        eval_status,
        counts,
    )
    return counts
