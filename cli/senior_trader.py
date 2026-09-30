"""CLI wrapper for the Senior Trader Phase 1 review cycle
(senior_trader/review_session.py).

Mirrors cli/signal_scan.py's connection-management pattern.
"""
from __future__ import annotations

from config.logging_config import get_logger

logger = get_logger(__name__)


def run_senior_trader_review_cmd(exchange: str = "kraken") -> bool:
    """One review cycle: read current account state and today's
    gate-approved setups, record a TAKE/NO_TRADE decision for each, and
    escalate if the account state looks internally inconsistent. Places no
    orders, executes nothing."""
    logger.info("=" * 80)
    logger.info("STARTING SENIOR TRADER REVIEW — %s", exchange.upper())
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from senior_trader.review_session import run_review_cycle

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        report = run_review_cycle(db, exchange=exchange)
        db.close_pool()

        logger.info(
            "senior_trader_review_complete exchange=%s signals_reviewed=%d take=%d no_trade=%d "
            "incidents=%d daily_room_remaining=%s lifetime_room_remaining=%s",
            report.exchange, report.active_signals_reviewed, report.decisions_take,
            report.decisions_no_trade, report.incidents_raised,
            report.account_daily_room_remaining, report.account_lifetime_room_remaining,
        )
        return True
    except Exception as e:
        logger.error("senior_trader_review_failed: %s", str(e), exc_info=True)
        return False


def run_senior_trader_paper_cmd(exchange: str = "kraken") -> bool:
    """Phase 3: process any TAKE decision the review cycle already wrote
    that has no execution intent yet — build the intent, simulate its
    fill, reconcile, journal, and run the daily audit. A no-op (returns
    True, builds nothing) unless senior_trader.execution_mode is PAPER."""
    logger.info("=" * 80)
    logger.info("STARTING SENIOR TRADER PAPER CYCLE — %s", exchange.upper())
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from senior_trader.paper_session import run_paper_trading_cycle

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        report = run_paper_trading_cycle(db, exchange=exchange)
        db.close_pool()

        if report.skipped_reason:
            logger.info("senior_trader_paper_skipped: %s", report.skipped_reason)
        else:
            logger.info(
                "senior_trader_paper_complete exchange=%s intents_built=%d filled=%d expired=%d "
                "reconciliation_failures=%d audit_passed=%s",
                report.exchange, report.intents_built, report.fills_filled, report.fills_expired,
                report.reconciliation_failures, report.audit_passed,
            )
        return True
    except Exception as e:
        logger.error("senior_trader_paper_failed: %s", str(e), exc_info=True)
        return False
