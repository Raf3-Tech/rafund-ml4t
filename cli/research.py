"""CLI commands: research pipeline and leaderboard."""

from __future__ import annotations

from typing import Optional

from config.logging_config import get_logger

logger = get_logger(__name__)


def run_research_cmd(
    strategy_filter: Optional[str] = None,
    symbol_filter: Optional[str] = None,
    top_n: int = 5,
    qualifying_only: bool = False,
    dry_run: bool = False,
) -> bool:
    try:
        from cli.db import get_db_connection
        from research.pipeline import print_research_summary, run_research_pipeline

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        decisions = run_research_pipeline(
            db=db,
            strategy_filter=strategy_filter,
            symbol_filter=symbol_filter,
            top_n=top_n,
            qualifying_only=qualifying_only,
            dry_run=dry_run,
        )
        db.close_pool()
        print_research_summary(decisions)
        return len(decisions) > 0
    except Exception as e:
        logger.error("Research pipeline error: %s", str(e), exc_info=True)
        return False


def run_leaderboard_cmd_wrapper(tier: Optional[str] = None) -> bool:
    try:
        from cli.db import get_db_connection
        from monitoring.leaderboard import run_leaderboard_cmd

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        result = run_leaderboard_cmd(db, tier=tier)
        db.close_pool()
        return result
    except Exception as e:
        logger.error("Leaderboard error: %s", str(e), exc_info=True)
        return False
