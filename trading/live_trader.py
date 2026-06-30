"""Live order execution via CCXT with prop-firm risk enforcement.

Safety gates (all must pass before any order is placed):
  1. LIVE_TRADING_ENABLED=1  — explicit opt-in required
  2. <EXCHANGE>_API_KEY / <EXCHANGE>_API_SECRET set and non-empty
     (BINANCE_*, KRAKEN_*, or HTX_*, matching the selected exchange)
  3. account_failed == False
  4. daily_halt == False  (for opens; force-close still runs on breach)
  5. Notional capped at LIVE_MAX_NOTIONAL_USDT (default 200)

Order execution: limit orders only, never market. Every order is placed at
the signal bar's close price and given LIVE_LIMIT_FILL_TIMEOUT_S (default
30s) to fill; if it hasn't filled by then it's cancelled rather than chased
at a worse price. This system trades on daily-bar structure, not speed —
there's no reason to cross the spread and pay for urgency that isn't needed.

Leverage / account type (env vars):
  LIVE_ACCOUNT_TYPE  — 'spot' (default), 'future' (USDT-M perps), 'margin'
  LIVE_LEVERAGE      — integer multiplier, default 1 (no leverage).
                       Set to 5 for 5× on a futures/margin account.
  LIVE_MARGIN_MODE   — 'isolated' (default) or 'cross'.
                       Isolated is strongly preferred for prop accounts:
                       a hard-floor breach only wipes that position's margin,
                       not the whole account balance.

  When LIVE_ACCOUNT_TYPE=future, Binance uses USDT-M perpetual contracts.
  Kraken and HTX use their native margin/futures APIs respectively.
  Leverage is configured on the exchange once per cycle via set_leverage()
  before any order is placed. Most prop-firm accounts accept this call;
  if yours pre-configures leverage and rejects the API call it is logged
  as a warning and trading continues (best-effort, not a hard gate).

Run via:  python main.py live
Or add to train_loop.sh after paper trading step.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

from config.logging_config import get_logger
from trading.alerts import send_alert
from trading.paper_trader import (
    _close,
    _last_signal,
    _latest_bars,
    _open,
    _target_notional,
    _top_accepted_strategy,
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


def _live_leverage() -> int:
    return max(1, int(os.environ.get("LIVE_LEVERAGE", "1")))


def _live_margin_mode() -> str:
    mode = os.environ.get("LIVE_MARGIN_MODE", "isolated").strip().lower()
    return mode if mode in ("isolated", "cross") else "isolated"


def _live_account_type() -> str:
    t = os.environ.get("LIVE_ACCOUNT_TYPE", "spot").strip().lower()
    return t if t in ("spot", "future", "margin") else "spot"


def _check_api_keys(exchange_name: str) -> bool:
    prefix = exchange_name.upper()
    key = os.environ.get(f"{prefix}_API_KEY", "")
    secret = os.environ.get(f"{prefix}_API_SECRET", "")
    if not key or not secret:
        logger.error("live_trader: API keys not set for %s — cannot trade", exchange_name)
        return False
    return True


# ── exchange helpers ──────────────────────────────────────────────────────────


def _build_exchange(exchange_name: str):
    import ccxt
    prefix = exchange_name.upper()
    account_type = _live_account_type()
    creds = {
        "apiKey": os.environ.get(f"{prefix}_API_KEY", ""),
        "secret": os.environ.get(f"{prefix}_API_SECRET", ""),
        "enableRateLimit": True,
    }
    if exchange_name == "kraken":
        # Kraken uses 'margin' type for leveraged trading
        if account_type in ("future", "margin"):
            creds["options"] = {"defaultType": "margin"}
        return ccxt.kraken(creds)
    if exchange_name == "htx":
        # HTX (Huobi): swap for USDT-M perps, spot otherwise
        if account_type == "future":
            creds["options"] = {"defaultType": "swap"}
        return ccxt.htx(creds)
    # Binance: future → USDT-M perps, margin → cross/isolated margin, spot → default
    creds["options"] = {"defaultType": account_type if account_type in ("future", "margin") else "spot"}
    return ccxt.binance(creds)


def _configure_leverage(exchange, symbol: str) -> bool:
    """Set margin mode and leverage on the exchange for `symbol` before any order.

    Best-effort: some prop-firm accounts pre-configure leverage and reject API
    calls to change it — those failures are logged as warnings, not errors, and
    trading continues. Returns True only if both calls succeeded.

    Call once per cycle, before the first order. Exchanges ignore no-op calls
    (e.g. set_leverage(5) when leverage is already 5) so repeated calls are safe.
    """
    leverage = _live_leverage()
    margin_mode = _live_margin_mode()

    if leverage == 1 and _live_account_type() == "spot":
        return True  # spot with no leverage — nothing to configure

    ok = True
    try:
        exchange.set_margin_mode(margin_mode, symbol)
        logger.info("live_trader: margin mode set to %s for %s", margin_mode, symbol)
    except Exception as exc:
        # Many exchanges raise if mode is already set — treat as non-fatal.
        logger.warning("live_trader: set_margin_mode(%s, %s) — %s (continuing)", margin_mode, symbol, exc)
        ok = False

    try:
        exchange.set_leverage(leverage, symbol)
        logger.info("live_trader: leverage set to %dx for %s", leverage, symbol)
    except Exception as exc:
        logger.warning("live_trader: set_leverage(%d, %s) — %s (continuing)", leverage, symbol, exc)
        ok = False

    return ok


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


# ── kill switch ────────────────────────────────────────────────────────────────


def force_close_live(db, pos) -> dict:
    """Flatten a live position (real limit order, or in-memory close if
    LIVE_TRADING_ENABLED=0 / API keys absent) and set manual_halt."""
    exchange_name = pos.exchange or "binance"

    if pos.is_flat:
        pos.manual_halt = True
        save_position(db, pos)
        send_alert(f"KILL SWITCH — LIVE/{exchange_name}", "Triggered with no open position. Trading halted.")
        return {"exchange": exchange_name, "closed": False, "manual_halt": True}

    shadow = not _live_enabled() or not _check_api_keys(exchange_name)

    df = _latest_bars(db, pos.symbol, 5, exchange=exchange_name)
    if df is None or df.empty:
        return {"exchange": exchange_name, "closed": False, "error": "no price data"}
    price = float(df["close"].iloc[-1])

    if shadow:
        pnl = _close(pos, price)
        log_order(db, pos, "CLOSE", price, pnl=pnl, order_type="shadow", close_reason="kill_switch")
    else:
        exchange = _build_exchange(exchange_name)
        _configure_leverage(exchange, pos.symbol)
        ccxt_side = "sell" if pos.side == "LONG" else "buy"
        order = _place_limit_order(exchange, pos.symbol, ccxt_side, abs(pos.qty), price)
        fill = _fill_price(order, price) if order else price
        oid = order["id"] if order else None
        pnl = _close(pos, fill)
        log_order(db, pos, "CLOSE", fill, pnl=pnl, order_type="live", exchange_order_id=oid,
                  close_reason="kill_switch")

    pos.manual_halt = True
    save_position(db, pos)
    logger.warning("live_trader[%s]: KILL SWITCH — closed  pnl=%.2f", exchange_name, pnl)
    send_alert(f"KILL SWITCH — LIVE/{exchange_name}", f"Closed @ {price:.4f}  pnl={pnl:.2f}. Trading halted.")
    return {"exchange": exchange_name, "closed": True, "pnl": pnl, "manual_halt": True}


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

    leverage = _live_leverage()
    account_type = _live_account_type()
    margin_mode = _live_margin_mode()
    logger.info(
        "live_trader[%s]: account_type=%s  leverage=%dx  margin_mode=%s  shadow=%s",
        exchange_name, account_type, leverage, margin_mode, shadow,
    )

    if not shadow and not _check_api_keys(exchange_name):
        return False

    result = _top_accepted_strategy(db)
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
    if exchange is not None:
        _configure_leverage(exchange, symbol)

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
    was_account_failed, was_daily_halt = pos.account_failed, pos.daily_halt
    _update_propfirm(pos, current_equity, cfg)

    mode_tag = "shadow" if shadow else "LIVE"
    if pos.account_failed and not was_account_failed:
        send_alert(
            f"ACCOUNT FAILED — {mode_tag}/{exchange_name}/{symbol}",
            f"Equity {current_equity:.2f} breached the drawdown floor "
            f"({cfg.max_drawdown_pct:.0%} of {cfg.account_size:.2f}). Trading halted.",
        )
    elif pos.daily_halt and not was_daily_halt:
        send_alert(
            f"DAILY LOSS HALT — {mode_tag}/{exchange_name}/{symbol}",
            f"Equity {current_equity:.2f} hit the daily loss limit "
            f"({cfg.max_daily_loss_pct:.0%} of {cfg.account_size:.2f}). Halted until tomorrow.",
        )

    if pos.manual_halt:
        save_position(db, pos)
        return True

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
                notional = _target_notional(pos, cfg, extra_cap=_max_notional())
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
                    notional = _target_notional(pos, cfg, extra_cap=_max_notional())
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
                    notional = _target_notional(pos, cfg, extra_cap=_max_notional())
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
