"""CLI wrapper for the manual-execution signal detector (signals/pending_signal_detector.py).

Mirrors cli/backtest.py's run_paper_trading connection-management pattern.
"""
from __future__ import annotations

from config.logging_config import get_logger

logger = get_logger(__name__)


def run_signal_scan_cmd(exchange: str = "kraken") -> bool:
    """One signal-detector cycle: expire stale/expired signals, then scan
    qualifying Kraken single-symbol strategies for fresh entry setups."""
    logger.info("=" * 80)
    logger.info("STARTING SIGNAL SCAN — %s", exchange.upper())
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from signals.pending_signal_detector import expire_stale_signals, scan_kraken_signals

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        n_expired = expire_stale_signals(db, exchange=exchange)
        signals = scan_kraken_signals(db, exchange=exchange)
        db.close_pool()

        logger.info(
            "signal_scan_complete exchange=%s expired=%d active_written=%d",
            exchange, n_expired, len(signals),
        )
        for sig in signals:
            logger.info(
                "  %s %s %s @ %.4g stop=%.4g tp=%.4g size=$%.2f — %s",
                sig.direction, sig.strategy_name, sig.symbol, sig.limit_price,
                sig.stop_price or 0.0, sig.take_profit_price or 0.0,
                sig.position_size_usd or 0.0, sig.thesis,
            )
        return True
    except Exception as e:
        logger.error("signal_scan_failed: %s", str(e), exc_info=True)
        return False
