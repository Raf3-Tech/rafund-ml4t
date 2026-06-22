"""Live order execution via CCXT with prop-firm risk enforcement.

Safety gates (all must pass before any order is placed):
  1. LIVE_TRADING_ENABLED=1  — explicit opt-in required
  2. BINANCE_API_KEY / BINANCE_API_SECRET set and non-empty
  3. account_failed == False
  4. daily_halt == False  (for opens; force-close still runs on breach)
  5. Notional capped at LIVE_MAX_NOTIONAL_USDT (default 200)

Order execution: limit orders only, never market. Every order is placed at
the signal bar's close price and given LIVE_LIMIT_FILL_TIMEOUT_S (default
30s) to fill; if it hasn't filled by then it's cancelled rather than chased
at a worse price. This system trades on daily-bar structure, not speed —
there's no reason to cross the spread and pay for urgency that isn't needed.

Run via:  python main.py live
Or add to train_loop.sh after paper trading step.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

from config.logging_config import get_logger
from trading.paper_trader import (
    _close,
    _last_signal,
    _latest_bars,
    _open,
    _top_strategy,
    _update_propfirm,
)
from trading.position import load_position, log_order, save_position

logger = get_logger(__name__)

_COMMISSION_PCT = 0.001


# ── guards ────────────────────────────────────────────────────────────────────


def _live_enabled() -> bool:
    return os.environ.get("LIVE_TRADING_ENABLED", "0").strip() == "1"


def _max_notional() -> float:
    return float(os.environ.get("LIVE_MAX_NOTIONAL_USDT", "200"))


def _check_api_keys(exchange_name: str) -> bool:
    if exchange_name == "kraken":
        key = os.environ.get("KRAKEN_API_KEY", "")
        secret = os.environ.get("KRAKEN_API_SECRET", "")
    else:
        key = os.environ.get("BINANCE_API_KEY", "")
        secret = os.environ.get("BINANCE_API_SECRET", "")
    if not key or not secret:
        logger.error("live_trader: API keys not set for %s — cannot trade", exchange_name)
        return False
    return True


# ── exchange helpers ──────────────────────────────────────────────────────────


def _build_exchange(exchange_name: str):
    import ccxt
    if exchange_name == "kraken":
        return ccxt.kraken({
            "apiKey": os.environ.get("KRAKEN_API_KEY", ""),
            "secret": os.environ.get("KRAKEN_API_SECRET", ""),
            "enableRateLimit": True,
        })
    return ccxt.binance({
        "apiKey": os.environ.get("BINANCE_API_KEY", ""),
        "secret": os.environ.get("BINANCE_API_SECRET", ""),
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })


_LIMIT_FILL_TIMEOUT_S = float(os.environ.get("LIVE_LIMIT_FILL_TIMEOUT_S", "30"))
_LIMIT_POLL_INTERVAL_S = 3.0


def _place_limit_order(exchange, symbol: str, side: str, qty: float, price: float) -> Optional[dict]:
    """Place a limit order at `price` and wait briefly for a fill.

    No market orders — ever. If the order hasn't filled within
    _LIMIT_FILL_TIMEOUT_S it's cancelled rather than chased at a worse
    price; the caller treats a None return as "no trade happened."
    """
    try:
        order = exchange.create_limit_order(symbol, side, qty, price)
    except Exception as exc:
        logger.error("live_trader: limit order failed — %s", exc)
        return None

    oid = order.get("id")
    waited = 0.0
    while waited < _LIMIT_FILL_TIMEOUT_S:
        try:
            status = exchange.fetch_order(oid, symbol)
        except Exception as exc:
            logger.warning("live_trader: fetch_order failed — %s", exc)
            break
        if status.get("status") == "closed":
            logger.info("live_trader: LIMIT %s %s qty=%.6f @ %.4f filled  id=%s",
                        side.upper(), symbol, qty, price, oid)
            return status
        time.sleep(_LIMIT_POLL_INTERVAL_S)
        waited += _LIMIT_POLL_INTERVAL_S

    try:
        exchange.cancel_order(oid, symbol)
        logger.warning("live_trader: limit order unfilled after %.0fs — cancelled  id=%s",
                        _LIMIT_FILL_TIMEOUT_S, oid)
    except Exception as exc:
        logger.warning("live_trader: cancel failed (order may have just filled) — %s", exc)
        try:
            status = exchange.fetch_order(oid, symbol)
            if status.get("status") == "closed":
                return status
        except Exception:
            pass
    return None


def _fill_price(order: dict, fallback: float) -> float:
    return float(order.get("average") or order.get("price") or fallback)


# ── main cycle ────────────────────────────────────────────────────────────────


def run_live_cycle(db, exchange: Optional[str] = None) -> bool:
    """One live-trading cycle.

    In shadow mode (LIVE_TRADING_ENABLED=0) it logs what it *would* do but
    places no orders — useful for sanity-checking before going live.
    """
    shadow = not _live_enabled()
    if shadow:
        logger.info("live_trader: shadow mode (LIVE_TRADING_ENABLED=0) — no orders placed")

    from config.loader import get_settings
    cfg = get_settings()
    exchange_name = (exchange or os.environ.get("LIVE_EXCHANGE", "binance")).lower()
    run_id = f"live_{exchange_name}"

    if not shadow and not _check_api_keys(exchange_name):
        return False

    result = _top_strategy(db)
    if result is None:
        logger.info("live_trader[%s]: no accepted strategy yet — skipping", exchange_name)
        return True

    strategy_name, symbol, params = result
    if symbol is None:
        symbol = cfg.collect_symbols[0] if cfg.collect_symbols else "BTC/USDT"

    from strategies.registry import StrategyRegistry
    try:
        strategy = StrategyRegistry.instantiate(strategy_name)
    except KeyError:
        logger.error("live_trader: unknown strategy %s", strategy_name)
        return False

    n_bars = strategy.get_min_bars(params) * 2 + 10
    df = _latest_bars(db, symbol, n_bars, exchange=exchange_name)
    if df is None or len(df) < strategy.get_min_bars(params):
        logger.warning("live_trader[%s]: insufficient data for %s", exchange_name, symbol)
        return True

    signal = _last_signal(df, strategy_name, params)
    price = float(df["close"].iloc[-1])

    pos = load_position(db, run_id, initial_capital=cfg.account_size,
                        strategy_name=strategy_name, symbol=symbol, exchange=exchange_name)

    exchange = _build_exchange(exchange_name) if not shadow else None

    # Close stale position when top strategy rotated
    if not pos.is_flat and (pos.strategy_name != strategy_name or pos.symbol != symbol):
        logger.info("live_trader: top strategy changed — closing stale %s %s", pos.side, pos.symbol)
        if not shadow:
            ccxt_side = "sell" if pos.side == "LONG" else "buy"
            order = _place_limit_order(exchange, pos.symbol, ccxt_side, abs(pos.qty), price)
            fill = _fill_price(order, price) if order else price
            oid = order["id"] if order else None
            pnl = _close(pos, fill)
            log_order(db, pos, "CLOSE", fill, pnl=pnl, order_type="live", exchange_order_id=oid,
                      close_reason="rotation")
        else:
            logger.info("live_trader: [shadow] would CLOSE %s %s @ %.4f", pos.side, pos.symbol, price)
            _close(pos, price)
        pos.strategy_name = strategy_name
        pos.symbol = symbol

    # Day rollover
    if pos.day_rolled():
        pos.daily_start_equity = pos.mark_to_market(price)
        pos.daily_halt = False

    current_equity = pos.mark_to_market(price)
    _update_propfirm(pos, current_equity, cfg)

    # Force-close on limit breach
    if (pos.account_failed or pos.daily_halt) and not pos.is_flat:
        reason = "account_failed" if pos.account_failed else "daily_halt"
        logger.warning("live_trader: %s — force-closing position", reason)
        if not shadow:
            ccxt_side = "sell" if pos.side == "LONG" else "buy"
            order = _place_limit_order(exchange, pos.symbol, ccxt_side, abs(pos.qty), price)
            fill = _fill_price(order, price) if order else price
            oid = order["id"] if order else None
            pnl = _close(pos, fill)
            log_order(db, pos, "CLOSE", fill, pnl=pnl, order_type="live", exchange_order_id=oid,
                      close_reason=reason)
        else:
            logger.info("live_trader: [shadow] would CLOSE %s @ %.4f (risk breach)", pos.symbol, price)
            _close(pos, price)
        save_position(db, pos)
        return True

    if pos.account_failed:
        logger.error("live_trader: account_failed — no further trading")
        save_position(db, pos)
        return True

    # Trade logic
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
                notional = min(pos.equity * cfg.leg_allocation_pct, _max_notional())
                qty = notional / price
                if shadow:
                    logger.info("live_trader: [shadow] would OPEN %s %s qty=%.6f @ %.4f",
                                target, symbol, qty, price)
                    _open(pos, target, price, notional)
                    log_order(db, pos, "OPEN", price, order_type="shadow", setup_tag=setup_tag)
                else:
                    ccxt_side = "buy" if target == "LONG" else "sell"
                    order = _place_limit_order(exchange, symbol, ccxt_side, qty, price)
                    if order:
                        fill = _fill_price(order, price)
                        _open(pos, target, fill, notional)
                        log_order(db, pos, "OPEN", fill, commission=notional * _COMMISSION_PCT,
                                  order_type="live", exchange_order_id=order["id"], setup_tag=setup_tag)
                        logger.info("live_trader: OPEN %s %s qty=%.6f @ %.4f", target, symbol, qty, fill)
        elif pos.side != target:
            # Flip
            if shadow:
                logger.info("live_trader: [shadow] would FLIP %s→%s %s @ %.4f", pos.side, target, symbol, price)
                pnl = _close(pos, price)
                log_order(db, pos, "CLOSE", price, pnl=pnl, order_type="shadow", close_reason="position_flip")
                if not pos.daily_halt:
                    notional = min(pos.equity * cfg.leg_allocation_pct, _max_notional())
                    _open(pos, target, price, notional)
                    log_order(db, pos, "OPEN", price, order_type="shadow", setup_tag=setup_tag)
            else:
                ccxt_side = "sell" if pos.side == "LONG" else "buy"
                order = _place_limit_order(exchange, symbol, ccxt_side, abs(pos.qty), price)
                fill = _fill_price(order, price) if order else price
                oid = order["id"] if order else None
                pnl = _close(pos, fill)
                log_order(db, pos, "CLOSE", fill, pnl=pnl, order_type="live", exchange_order_id=oid,
                          close_reason="position_flip")
                logger.info("live_trader: CLOSE (flip) @ %.4f  pnl=%.2f", fill, pnl)
                if not pos.daily_halt:
                    notional = min(pos.equity * cfg.leg_allocation_pct, _max_notional())
                    qty = notional / fill
                    ccxt_side2 = "buy" if target == "LONG" else "sell"
                    order2 = _place_limit_order(exchange, symbol, ccxt_side2, qty, fill)
                    if order2:
                        fill2 = _fill_price(order2, fill)
                        _open(pos, target, fill2, notional)
                        log_order(db, pos, "OPEN", fill2, commission=notional * _COMMISSION_PCT,
                                  order_type="live", exchange_order_id=order2["id"], setup_tag=setup_tag)
    else:
        if not pos.is_flat:
            if shadow:
                logger.info("live_trader: [shadow] would CLOSE %s %s @ %.4f", pos.side, symbol, price)
                pnl = _close(pos, price)
                log_order(db, pos, "CLOSE", price, pnl=pnl, order_type="shadow", close_reason="signal_exit")
            else:
                ccxt_side = "sell" if pos.side == "LONG" else "buy"
                order = _place_limit_order(exchange, symbol, ccxt_side, abs(pos.qty), price)
                fill = _fill_price(order, price) if order else price
                oid = order["id"] if order else None
                pnl = _close(pos, fill)
                log_order(db, pos, "CLOSE", fill, pnl=pnl, order_type="live", exchange_order_id=oid,
                          close_reason="signal_exit")
                logger.info("live_trader: CLOSE @ %.4f  pnl=%.2f", fill, pnl)

    pos.equity = pos.mark_to_market(price)
    save_position(db, pos)

    dd_pct = (pos.peak_equity - pos.equity) / pos.peak_equity * 100 if pos.peak_equity > 0 else 0
    mode = "shadow" if shadow else "LIVE"
    logger.info(
        "live_trader [%s]: %s | %s | signal=%-4s | equity=%.2f | dd=%.2f%% | halt=%s",
        mode, strategy_name, symbol, signal, pos.equity, dd_pct, pos.daily_halt,
    )
    return True
