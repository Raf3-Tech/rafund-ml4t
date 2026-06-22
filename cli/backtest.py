"""CLI commands: backtest, validation, paper trading, live trading, full pipeline."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import pandas as pd

from config.logging_config import get_logger

logger = get_logger(__name__)


def _aligned_pair_frame(db, symbol_a: str, symbol_b: str) -> Optional[pd.DataFrame]:
    pa = db.get_prices(symbol_a, None, None)
    pb = db.get_prices(symbol_b, None, None)
    if pa.empty or pb.empty:
        return None
    left = pa[["timestamp", "close"]].rename(columns={"close": "price_a"})
    right = pb[["timestamp", "close"]].rename(columns={"close": "price_b"})
    merged = pd.merge(left, right, on="timestamp", how="inner").sort_values("timestamp")
    return merged.reset_index(drop=True) if not merged.empty else None


def run_backtest() -> bool:
    """Run strategy evaluation backtest."""
    logger.info("=" * 80)
    logger.info("STARTING BACKTEST")
    logger.info("=" * 80)
    try:
        from backtesting.engine_eval import EvaluationBacktestEngine
        from backtesting.evaluation_rules import PropFirmRules
        from backtesting.persist import persist_evaluation_run
        from cli.db import get_db_connection
        from cli.features import calculate_features

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        from config.loader import get_settings

        cfg = get_settings()

        pairs = db.get_feature_pairs()
        if not pairs:
            if not calculate_features():
                db.close_pool()
                return False
            pairs = db.get_feature_pairs()

        if not pairs:
            pair_a = os.getenv("EVAL_PAIR_A", "BTC/USDT")
            pair_b = os.getenv("EVAL_PAIR_B", "ETH/USDT")
            pairs = [(pair_a, pair_b)]

        rules = PropFirmRules()
        results_summary = []

        for idx, (pair_a, pair_b) in enumerate(pairs, start=1):
            pa = db.get_prices(pair_a, None, None)
            pb = db.get_prices(pair_b, None, None)
            if pa.empty or pb.empty:
                continue

            prices = pd.concat([pa, pb], ignore_index=True)
            engine = EvaluationBacktestEngine(
                rules=rules,
                symbol_a=pair_a,
                symbol_b=pair_b,
                entry_threshold=cfg.entry_threshold,
                exit_threshold=cfg.exit_threshold,
                lookback=60,
                leg_allocation_pct=0.18,
                commission=0.001,
                stop_loss_spread_pct=0.03,
                max_holding_days=30,
            )
            results = engine.run(prices)
            ev = results.get("evaluation", {})

            if (
                os.getenv("SAVE_TO_DB", "1") == "1"
                and results.get("signals_df") is not None
            ):
                backtest_id = (
                    f"backtest_{pair_a.replace('/', '')}_{pair_b.replace('/', '')}_"
                    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                )
                persist_evaluation_run(db, results, backtest_id)

            results_summary.append(
                {
                    "pair": f"{pair_a}/{pair_b}",
                    "status": ev.get("status", "N/A"),
                    "total_return_pct": results.get("total_return_pct", 0.0),
                    "sharpe_ratio": results.get("sharpe_ratio", 0.0),
                    "max_drawdown_pct": results.get("max_drawdown_pct", 0.0),
                    "num_trades": results.get("num_trades", 0),
                    "win_rate_pct": results.get("win_rate_pct", 0.0),
                }
            )

        if not results_summary:
            logger.error("No backtest results were generated")
            db.close_pool()
            return False

        for row in results_summary:
            logger.info(
                "%s | status=%s | return=%.2f%% | sharpe=%.2f | dd=%.2f%% | trades=%d | win=%.1f%%",
                row["pair"],
                row["status"],
                row["total_return_pct"],
                row["sharpe_ratio"],
                row["max_drawdown_pct"],
                row["num_trades"],
                row["win_rate_pct"],
            )
        db.close_pool()
        return True
    except Exception as e:
        logger.error(f"Backtest error: {str(e)}", exc_info=True)
        return False


def run_validation_cmd() -> bool:
    """Walk-forward out-of-sample validation of the stat-arb strategy."""
    logger.info("=" * 80)
    logger.info("STARTING WALK-FORWARD VALIDATION")
    logger.info("=" * 80)
    try:
        from backtesting.validation import run_validation
        from cli.db import get_db_connection
        from config.loader import get_settings
        from strategies.stat_arb import WalkForwardStatArb

        cfg = get_settings()
        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        pairs = db.get_feature_pairs() or [
            (os.getenv("EVAL_PAIR_A", "BTC/USDT"), os.getenv("EVAL_PAIR_B", "ETH/USDT"))
        ]
        any_results = False

        for pair_a, pair_b in pairs:
            prices = _aligned_pair_frame(db, pair_a, pair_b)
            if prices is None:
                continue

            strategy = WalkForwardStatArb(
                entry_threshold=cfg.entry_threshold,
                exit_threshold=cfg.exit_threshold,
                entropy_threshold=cfg.entropy_threshold
                if cfg.entropy_enabled
                else None,
                entropy_window=cfg.entropy_window,
                entropy_embedding=cfg.entropy_embedding,
            )
            result = run_validation(
                prices,
                strategy,
                strategy_name=f"statarb_{pair_a.replace('/', '')}_{pair_b.replace('/', '')}",
            )
            wf = result.walk_forward
            agg = wf.aggregate
            logger.info(
                "RESULT %s/%s: folds=%d oos_sharpe=%.2f dd=%.2f%% pct_prof=%.0f%% gate=%s",
                pair_a,
                pair_b,
                len(wf.folds),
                agg.mean_oos_sharpe,
                agg.worst_oos_drawdown,
                agg.pct_folds_profitable,
                "PASS" if wf.passed_gate else "FAIL",
            )
            any_results = True

        db.close_pool()
        return any_results
    except Exception as e:
        logger.error(f"Validation error: {str(e)}", exc_info=True)
        return False


def run_paper_trading(exchange: Optional[str] = None) -> bool:
    """Real-time paper trading: consume live DB prices, track virtual position with prop-firm rules.

    Runs all configured exchanges in one cycle by default (no real funds at
    risk); pass ``exchange`` to restrict to just one.
    """
    logger.info("=" * 80)
    logger.info("STARTING PAPER TRADING CYCLE")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from trading.paper_trader import run_paper_cycle_all

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False
        result = run_paper_cycle_all(db, exchange=exchange)
        db.close_pool()
        return result
    except Exception as e:
        logger.error("Paper trading error: %s", str(e), exc_info=True)
        return False


def run_live_trading(exchange: Optional[str] = None) -> bool:
    """Live order execution via CCXT with prop-firm risk enforcement.

    Requires LIVE_TRADING_ENABLED=1 in .env.  Without it, runs in shadow mode
    (logs what it would do, places no orders). Single exchange per call —
    pass ``exchange`` or set LIVE_EXCHANGE; real funds means no auto-looping.
    """
    logger.info("=" * 80)
    logger.info("STARTING LIVE TRADING CYCLE")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from trading.live_trader import run_live_cycle

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False
        result = run_live_cycle(db, exchange=exchange)
        db.close_pool()
        return result
    except Exception as e:
        logger.error("Live trading error: %s", str(e), exc_info=True)
        return False


def run_full_pipeline() -> bool:
    """Full bootstrap: collect → backtest → persist."""
    logger.info("=" * 80)
    logger.info("RUNNING FULL ML4T INFRASTRUCTURE PIPELINE")
    logger.info("=" * 80)
    try:
        from backtesting.engine_eval import EvaluationBacktestEngine
        from backtesting.evaluation_rules import PropFirmRules
        from backtesting.persist import persist_evaluation_run
        from cli.collect import collect_data
        from cli.db import get_db_connection

        if not collect_data(full_history=True):
            logger.error("Data collection failed")
            return False

        db = get_db_connection()
        pair_a = os.getenv("EVAL_PAIR_A", "BTC/USDT")
        pair_b = os.getenv("EVAL_PAIR_B", "ETH/USDT")

        pa = db.get_prices(pair_a, None, None)
        pb = db.get_prices(pair_b, None, None)
        prices = pd.concat([pa, pb], ignore_index=True)

        from config.loader import get_settings

        cfg = get_settings()

        rules = PropFirmRules()
        engine = EvaluationBacktestEngine(
            rules=rules,
            symbol_a=pair_a,
            symbol_b=pair_b,
            entry_threshold=cfg.entry_threshold,
            exit_threshold=cfg.exit_threshold,
            lookback=60,
            leg_allocation_pct=0.18,
            commission=0.001,
            stop_loss_spread_pct=0.03,
            max_holding_days=30,
        )
        results = engine.run(prices)
        ev = results.get("evaluation", {})
        backtest_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ev.get('status', 'UNKNOWN')}"
        persist_evaluation_run(db, results, backtest_id)

        logger.info(
            "Pipeline complete — final equity: $%.2f  return: %.2f%%  status: %s",
            results["final_value"],
            results["total_return_pct"],
            ev.get("status"),
        )
        db.close_pool()
        return True
    except Exception as e:
        logger.error(f"Pipeline error: {str(e)}", exc_info=True)
        return False


def run_bootstrap() -> bool:
    return run_full_pipeline()
