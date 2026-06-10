"""CLI commands: walk-forward engine."""

from __future__ import annotations

from typing import Optional

from config.logging_config import get_logger

logger = get_logger(__name__)


def run_engine_cmd(
    strategy_filter: Optional[str] = None,
    symbol_filter: Optional[str] = None,
) -> bool:
    """Run the walk-forward window engine across all strategies and symbols."""
    logger.info("=" * 80)
    logger.info("STARTING WALK-FORWARD ENGINE")
    logger.info("=" * 80)
    if strategy_filter:
        logger.info("Strategy filter: %s", strategy_filter)
    if symbol_filter:
        logger.info("Symbol filter: %s", symbol_filter)

    try:
        import strategies as _strat_pkg  # noqa: F401 — populates StrategyRegistry
        from backtesting.window_engine import WalkForwardWindowEngine
        from cli.db import get_db_connection
        from config.loader import get_settings
        from strategies.registry import StrategyRegistry

        cfg = get_settings()
        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        symbols = db.get_symbols_with_data()
        if not symbols:
            logger.error("No price data found. Run: python main.py collect first.")
            db.close_pool()
            return False

        strategies = StrategyRegistry.instantiate_all()
        logger.info("Registry loaded %d strategies: %s", len(strategies), [s.name for s in strategies])

        engine = WalkForwardWindowEngine(
            db=db, strategies=strategies, symbols=symbols, commission=cfg.commission
        )
        results = engine.run(strategy_filter=strategy_filter, symbol_filter=symbol_filter)
        logger.info("Engine run complete — %d results generated", len(results))
        db.close_pool()
        return len(results) > 0
    except Exception as e:
        logger.error("Engine error: %s", str(e), exc_info=True)
        return False
