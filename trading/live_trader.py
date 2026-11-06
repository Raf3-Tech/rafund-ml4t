"""Live order execution via CCXT with prop-firm risk enforcement.

Safety gates (all must pass before any order is placed):
  1. LIVE_TRADING_ENABLED=1  — explicit opt-in required
  2. <EXCHANGE>_API_KEY / <EXCHANGE>_API_SECRET set and non-empty
     (BINANCE_*, KRAKEN_*, or HTX_*, matching the selected exchange)
  3. account_failed == False
  4. daily_halt == False  (for opens; force-close still runs on breach)
  5. Notional capped at LIVE_MAX_NOTIONAL_USDT (default 200)
  6. Qty/price rounded to the market's precision and checked against its
     minimum amount/cost (trading/execution.py) — below-minimum orders are
     never sent
  7. LIVE_ACCOUNT_TYPE=spot never opens a SHORT — SELL only exits a long

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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional

from config.logging_config import get_logger
from trading.alerts import send_alert
from trading.execution import build_execution_engine
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


def _build_execution_engine(exchange_name: str, shadow: bool):
    return build_execution_engine(exchange_name, shadow=shadow, max_notional=_max_notional())


@dataclass
class _Fill:
    """What the exchange actually executed for one limit order.

    qty is the *filled* quantity (0 when nothing filled, < requested on a
    partial fill). uncertain=True means the final order state could not be
    read back from the exchange, so local state cannot be trusted to match.
    """
    qty: float
    price: float
    order_id: Optional[str]
    uncertain: bool = False
    requested: float = 0.0  # qty actually sent, after exchange precision rounding


def _fill_from_status(status: dict, requested: float, fallback_price: float,
                      order_id: Optional[str]) -> _Fill:
    if status.get("status") == "error":
        return _Fill(0.0, fallback_price, order_id, uncertain=True)
    filled = status.get("filled")
    if filled is None:
        # Some venues omit `filled`; only a fully closed order implies a full fill.
        filled = requested if status.get("status") == "closed" else 0.0
    filled = max(0.0, min(float(filled), requested))
    price = float(status.get("average") or status.get("price") or fallback_price)
    return _Fill(filled, price, order_id)


def _execute_limit(engine, symbol: str, side: str, qty: float, price: float) -> _Fill:
    """Place a limit order, wait up to _LIMIT_FILL_TIMEOUT_S for it to fill,
    cancel whatever is left, and return what actually filled.

    Callers must update local position state from the returned fill only —
    never from the requested qty — so the DB can't drift from the exchange.
    """
    order = engine.place_limit_order(symbol, side, qty, price)
    if order is None or order.status == "error":
        err = (order.raw or {}).get("error") if order is not None else "no order returned"
        logger.error("live_trader: limit %s %s qty=%.8f rejected — %s", side, symbol, qty, err)
        return _Fill(0.0, price, None)
    if order.status == "shadow":
        return _Fill(qty, price, None, requested=qty)

    # The adapter may have truncated qty to the market's precision; judge the
    # fill against what was actually sent, not what was asked for.
    qty = order.qty
    oid = order.order_id
    status = order.raw or {}
    waited = 0.0
    while status.get("status") != "closed" and waited < _LIMIT_FILL_TIMEOUT_S:
        time.sleep(_LIMIT_POLL_INTERVAL_S)
        waited += _LIMIT_POLL_INTERVAL_S
        status = engine.adapter.fetch_order(oid, symbol)

    if status.get("status") != "closed":
        engine.adapter.cancel_order(oid, symbol)
        # Re-read after cancelling: picks up fills that raced the cancel and
        # the final partially-filled quantity.
        status = engine.adapter.fetch_order(oid, symbol)

    fill = _fill_from_status(status, qty, price, oid)
    fill.requested = qty
    if fill.qty < qty:
        logger.warning("live_trader: limit %s %s filled %.8f of %.8f — remainder cancelled  id=%s",
                       side, symbol, fill.qty, qty, oid)
    return fill


def _halt_unreconciled(db, pos, what: str) -> None:
    """Exchange state unknown: stop all trading until an operator reconciles."""
    pos.manual_halt = True
    logger.error("live_trader[%s]: %s — order state unknown, manual_halt set", pos.exchange, what)
    send_alert(
        f"RECONCILE NEEDED — LIVE/{pos.exchange}/{pos.symbol}",
        f"Could not confirm the result of a {what} order. Trading halted; check the "
        f"exchange position and resume manually once it matches the DB.",
    )


def _live_close(db, engine, pos, price: float, reason: str) -> Optional[float]:
    """Close `pos` on the exchange and record only what filled.

    Returns realised P&L if the position is now fully flat, else None (nothing
    or only part filled — the unfilled remainder stays open in the DB, matching
    the exchange, and is retried next cycle).
    """
    side = "sell" if pos.side == "LONG" else "buy"
    requested = abs(pos.qty)
    fill = _execute_limit(engine, pos.symbol, side, requested, price)
    if fill.uncertain:
        _halt_unreconciled(db, pos, f"{reason} close")
        return None

    if fill.qty <= 0:
        logger.warning("live_trader: %s close of %s did not fill — position still open", reason, pos.symbol)
        send_alert(f"CLOSE NOT FILLED — LIVE/{pos.exchange}/{pos.symbol}",
                   f"{reason} close @ {price:.4f} did not fill. Position still open; retrying next cycle.")
        return None

    # Full close = everything that was sendable filled. Any sub-precision dust
    # the adapter truncated off can't be traded, so the position is done.
    if fill.qty >= fill.requested * (1 - 1e-9):
        if fill.requested < requested:
            logger.info("live_trader: %s close left %.10f %s dust below exchange precision",
                        reason, requested - fill.requested, pos.symbol)
        pnl = _close(pos, fill.price, as_of=datetime.now(timezone.utc))
        log_order(db, pos, "CLOSE", fill.price, pnl=pnl, order_type="live",
                  exchange_order_id=fill.order_id, close_reason=reason)
        return pnl

    # Partial: realise P&L on the filled slice through the same _close path.
    part = replace(pos, qty=fill.qty, equity=0.0)
    pnl = _close(part, fill.price, as_of=datetime.now(timezone.utc))
    pos.qty = requested - fill.qty
    pos.equity += pnl
    log_order(db, pos, "CLOSE", fill.price, pnl=pnl, order_type="live",
              exchange_order_id=fill.order_id, close_reason=f"{reason}_partial")
    send_alert(f"PARTIAL CLOSE — LIVE/{pos.exchange}/{pos.symbol}",
               f"{reason} close filled {fill.qty:.8f} of {requested:.8f}; "
               f"{pos.qty:.8f} still open, retrying next cycle.")
    return None


def _live_open(db, engine, pos, target: str, qty: float, price: float, setup_tag) -> None:
    """Open `target` on the exchange and record only the filled quantity."""
    side = "buy" if target == "LONG" else "sell"
    fill = _execute_limit(engine, pos.symbol, side, qty, price)
    if fill.uncertain:
        _halt_unreconciled(db, pos, "open")
        return
    if fill.qty <= 0:
        logger.info("live_trader: OPEN %s %s did not fill — staying flat", target, pos.symbol)
        return
    notional = fill.qty * fill.price
    _open(pos, target, fill.price, notional)
    log_order(db, pos, "OPEN", fill.price, commission=notional * _COMMISSION_PCT,
              order_type="live", exchange_order_id=fill.order_id, setup_tag=setup_tag)
    logger.info("live_trader: OPEN %s %s qty=%.8f @ %.4f", target, pos.symbol, fill.qty, fill.price)


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
        engine = _build_execution_engine(exchange_name, shadow)
        exchange = _build_exchange(exchange_name)
        _configure_leverage(exchange, pos.symbol)
        pnl = _live_close(db, engine, pos, price, "kill_switch")
        if pnl is None:
            # Halt anyway so nothing new opens, but report the truth: the
            # position (or its unfilled remainder) is still open on the exchange.
            pos.manual_halt = True
            save_position(db, pos)
            send_alert(f"KILL SWITCH — LIVE/{exchange_name}",
                       f"Trading halted but close did NOT fully fill — {abs(pos.qty):.8f} "
                       f"{pos.symbol} still open. Flatten on the exchange manually.")
            return {"exchange": exchange_name, "closed": False, "manual_halt": True,
                    "remaining_qty": abs(pos.qty), "error": "close not filled"}

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

    engine = _build_execution_engine(exchange_name, shadow)
    exchange = _build_exchange(exchange_name) if not shadow else None
    if exchange is not None:
        _configure_leverage(exchange, symbol)

    # Close stale position when top strategy rotated
    if not pos.is_flat and (pos.strategy_name != strategy_name or pos.symbol != symbol):
        logger.info("live_trader: top strategy changed — closing stale %s %s", pos.side, pos.symbol)
        if not shadow:
            if _live_close(db, engine, pos, price, "rotation") is None:
                # Old position still (partly) open — don't start trading the new
                # strategy/symbol on top of it. Retry the close next cycle.
                save_position(db, pos)
                return True
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
            # Unfilled remainder stays open; breach flags persist, so the
            # next cycle retries the force-close.
            _live_close(db, engine, pos, price, reason)
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

    # A spot account can't hold a short: SELL is exit-only there (closes a
    # long, never opens a short). Applied in shadow mode too so shadow runs
    # show exactly what live would do.
    if target == "SHORT" and _live_account_type() == "spot":
        logger.info("live_trader: SELL on spot account — exit only, no short opened (%s)", symbol)
        target = None

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
                    _live_open(db, engine, pos, target, qty, price, setup_tag)
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
                pnl = _live_close(db, engine, pos, price, "position_flip")
                # Only open the new side once the old one is fully closed.
                if pnl is not None:
                    logger.info("live_trader: CLOSE (flip) @ %.4f  pnl=%.2f", price, pnl)
                    if not pos.daily_halt and not pos.manual_halt:
                        notional = _target_notional(pos, cfg, extra_cap=_max_notional())
                        _live_open(db, engine, pos, target, notional / price, price, setup_tag)
    else:
        if not pos.is_flat:
            if shadow:
                logger.info("live_trader: [shadow] would CLOSE %s %s @ %.4f", pos.side, symbol, price)
                pnl = _close(pos, price)
                log_order(db, pos, "CLOSE", price, pnl=pnl, order_type="shadow", close_reason="signal_exit")
            else:
                pnl = _live_close(db, engine, pos, price, "signal_exit")
                if pnl is not None:
                    logger.info("live_trader: CLOSE @ %.4f  pnl=%.2f", price, pnl)

    pos.equity = pos.mark_to_market(price)
    save_position(db, pos)

    dd_pct = (pos.peak_equity - pos.equity) / pos.peak_equity * 100 if pos.peak_equity > 0 else 0
    mode = "shadow" if shadow else "LIVE"
    logger.info(
        "live_trader [%s]: %s | %s | signal=%-4s | equity=%.2f | dd=%.2f%% | halt=%s",
        mode, strategy_name, symbol, signal, pos.equity, dd_pct, pos.daily_halt,
    )
    return True
