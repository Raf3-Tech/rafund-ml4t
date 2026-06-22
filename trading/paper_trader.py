"""Paper trading engine.

Runs one cycle per train_loop.sh iteration:
  1. Query top accepted strategy from research_decisions
  2. Fetch latest bars from DB
  3. Generate signal on those bars
  4. Apply prop-firm rules (daily halt, drawdown floor)
  5. Open / close / hold virtual position
  6. Persist state to paper_positions; append to paper_orders
"""

from __future__ import annotations

from datetime import timezone
from typing import Optional, Tuple

import pandas as pd

from config.logging_config import get_logger
from trading.position import PositionState, load_position, log_order, save_position

logger = get_logger(__name__)

_COMMISSION_PCT = 0.001
SUPPORTED_EXCHANGES = ("binance", "kraken", "htx")


def _run_id(exchange: str) -> str:
    return f"paper_{exchange}"


# ── helpers ──────────────────────────────────────────────────────────────────


def _top_strategy(db) -> Optional[Tuple[str, str, dict]]:
    """Return (strategy_name, symbol, params) for the highest-ranked accepted decision."""
    df = db.read_sql(
        """
        SELECT strategy_name, symbol, params, avg_sharpe
        FROM research_decisions
        WHERE accepted = TRUE
        ORDER BY created_at DESC, avg_sharpe DESC
        LIMIT 1
        """
    )
    if df.empty:
        return None
    row = df.iloc[0]
    params = row["params"] if isinstance(row["params"], dict) else {}
    symbol = str(row["symbol"]) if row["symbol"] else None
    return str(row["strategy_name"]), symbol, params


def _latest_bars(db, symbol: str, n: int, exchange: str = "binance") -> Optional[pd.DataFrame]:
    df = db.read_sql(
        """
        SELECT timestamp, open, high, low, close, volume
        FROM prices
        WHERE symbol = %s AND exchange = %s
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        [symbol, exchange, n],
    )
    if df.empty:
        return None
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _last_signal(df: pd.DataFrame, strategy_name: str, params: dict) -> str:
    from strategies.registry import StrategyRegistry
    try:
        strategy = StrategyRegistry.instantiate(strategy_name)
    except KeyError:
        logger.error("paper_trader: strategy not registered: %s", strategy_name)
        return "HOLD"
    signals = strategy.generate_signals(df, params)
    return str(signals.iloc[-1]) if len(signals) > 0 else "HOLD"


# ── position accounting ───────────────────────────────────────────────────────


def _open(pos: PositionState, side: str, price: float, notional: float) -> None:
    commission = notional * _COMMISSION_PCT
    pos.qty = notional / price
    pos.side = side
    pos.entry_price = price
    from datetime import datetime, timezone
    pos.entry_time = datetime.now(timezone.utc)
    pos.equity -= commission


def _close(pos: PositionState, price: float) -> float:
    """Close position in-place; return realised P&L (net of commission)."""
    if pos.is_flat or pos.entry_price is None:
        return 0.0
    if pos.side == "LONG":
        gross_pnl = pos.qty * (price - pos.entry_price)
    else:
        gross_pnl = -pos.qty * (price - pos.entry_price)
    commission = abs(pos.qty * price) * _COMMISSION_PCT
    pnl = gross_pnl - commission
    pos.equity += pnl
    pos.qty = 0.0
    pos.side = "FLAT"
    pos.entry_price = None
    pos.entry_time = None
    return pnl


def _update_propfirm(pos: PositionState, current_equity: float, cfg) -> None:
    """Update peak, daily-halt, and account_failed in-place."""
    if current_equity > pos.peak_equity:
        pos.peak_equity = current_equity
    daily_pnl = current_equity - pos.daily_start_equity
    if daily_pnl <= -cfg.account_size * cfg.max_daily_loss_pct:
        if not pos.daily_halt:
            pos.daily_halt = True
            logger.warning("paper_trader: daily halt — daily_pnl=%.2f", daily_pnl)
    if current_equity <= cfg.account_size * (1.0 - cfg.max_drawdown_pct):
        if not pos.account_failed:
            pos.account_failed = True
            logger.error(
                "paper_trader: ACCOUNT FAILED equity=%.2f floor=%.2f",
                current_equity, cfg.account_size * (1.0 - cfg.max_drawdown_pct),
            )


# ── main cycle ────────────────────────────────────────────────────────────────


def run_paper_cycle(db, exchange: str = "binance") -> bool:
    """One paper-trading cycle for one exchange. Safe to call even before any
    research decisions exist, or before that exchange has price history yet."""
    from config.loader import get_settings
    cfg = get_settings()
    run_id = _run_id(exchange)

    result = _top_strategy(db)
    if result is None:
        logger.info("paper_trader[%s]: no accepted strategy yet — skipping cycle", exchange)
        return True

    strategy_name, symbol, params = result
    if symbol is None:
        symbol = cfg.collect_symbols[0] if cfg.collect_symbols else "BTC/USDT"

    from strategies.registry import StrategyRegistry
    try:
        strategy = StrategyRegistry.instantiate(strategy_name)
    except KeyError:
        logger.error("paper_trader[%s]: unknown strategy %s", exchange, strategy_name)
        return False

    n_bars = strategy.get_min_bars(params) * 2 + 10
    df = _latest_bars(db, symbol, n_bars, exchange=exchange)
    if df is None or len(df) < strategy.get_min_bars(params):
        logger.warning("paper_trader[%s]: not enough bars for %s (%d)",
                        exchange, symbol, 0 if df is None else len(df))
        return True

    signal = _last_signal(df, strategy_name, params)
    price = float(df["close"].iloc[-1])

    pos = load_position(db, run_id, initial_capital=cfg.account_size,
                        strategy_name=strategy_name, symbol=symbol, exchange=exchange)

    # Close stale position if the top strategy/symbol rotated
    if not pos.is_flat and (pos.strategy_name != strategy_name or pos.symbol != symbol):
        pnl = _close(pos, price)
        log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="rotation")
        pos.strategy_name = strategy_name
        pos.symbol = symbol

    # Day rollover: reset daily state
    if pos.day_rolled():
        pos.daily_start_equity = pos.mark_to_market(price)
        pos.daily_halt = False

    current_equity = pos.mark_to_market(price)
    _update_propfirm(pos, current_equity, cfg)

    # Force-close and stop if account is blown
    if pos.account_failed:
        if not pos.is_flat:
            pnl = _close(pos, price)
            log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="account_failed")
        save_position(db, pos)
        return True

    # Determine target side
    if signal == "BUY":
        target = "LONG"
    elif signal == "SELL":
        target = "SHORT"
    else:
        target = None

    setup_tag = f"{strategy_name}_{signal.lower()}" if signal in ("BUY", "SELL") else None

    if target is not None:
        if pos.is_flat:
            if not pos.daily_halt:
                notional = pos.equity * cfg.leg_allocation_pct
                _open(pos, target, price, notional)
                log_order(db, pos, "OPEN", price, setup_tag=setup_tag)
                logger.info("paper_trader: OPEN %s %s @ %.4f  equity=%.2f",
                            target, symbol, price, pos.equity)
        elif pos.side != target:
            pnl = _close(pos, price)
            log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="position_flip")
            logger.info("paper_trader: CLOSE (flip) @ %.4f  pnl=%.2f", price, pnl)
            if not pos.daily_halt:
                notional = pos.equity * cfg.leg_allocation_pct
                _open(pos, target, price, notional)
                log_order(db, pos, "OPEN", price, setup_tag=setup_tag)
                logger.info("paper_trader: OPEN %s %s @ %.4f  equity=%.2f",
                            target, symbol, price, pos.equity)
    else:
        if not pos.is_flat:
            pnl = _close(pos, price)
            log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="signal_exit")
            logger.info("paper_trader: CLOSE %s @ %.4f  pnl=%.2f", symbol, price, pnl)

    pos.equity = pos.mark_to_market(price)
    save_position(db, pos)

    dd_pct = (pos.peak_equity - pos.equity) / pos.peak_equity * 100 if pos.peak_equity > 0 else 0
    logger.info(
        "paper_trader[%s]: %s | %s | signal=%-4s | equity=%.2f | dd=%.2f%% | halt=%s",
        exchange, strategy_name, symbol, signal, pos.equity, dd_pct, pos.daily_halt,
    )
    return True


def run_paper_cycle_all(db, exchange: Optional[str] = None) -> bool:
    """Run one paper cycle for each configured exchange (default: all of them).

    A single ``python main.py paper`` invocation tracks every exchange at once —
    paper trading carries no real-funds risk, so convenience wins over the
    explicit single-exchange-per-call discipline used for live trading.
    """
    exchanges = [exchange] if exchange else list(SUPPORTED_EXCHANGES)
    ok = True
    for ex in exchanges:
        ok = run_paper_cycle(db, exchange=ex) and ok
    return ok
