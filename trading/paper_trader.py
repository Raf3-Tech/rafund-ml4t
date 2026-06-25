"""Paper trading engine.

Runs one cycle per train_loop.sh iteration, per exchange, for every currently
qualifying strategy-symbol candidate (see _paper_candidates — best
single-symbol strategy per symbol by avg_sharpe, independent of the stricter
live-promotion gate in research_decisions). Each candidate trades as its own
independent slot:
  1. Fetch latest bars from DB
  2. Generate signal on those bars
  3. Apply prop-firm rules (daily halt, drawdown floor)
  4. Open / close / hold virtual position
  5. Persist state to paper_positions; append to paper_orders
"""

from __future__ import annotations

import re
from datetime import timezone
from typing import List, Optional, Tuple

import pandas as pd

from config.logging_config import get_logger
from trading.alerts import send_alert
from trading.position import (
    PositionState,
    list_positions,
    load_position,
    log_order,
    max_safe_notional,
    save_position,
)

logger = get_logger(__name__)

_COMMISSION_PCT = 0.001
SUPPORTED_EXCHANGES = ("binance", "kraken", "htx")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _paper_run_id(exchange: str, strategy_name: str, symbol: str) -> str:
    return f"paper_{exchange}_{_slug(strategy_name)}_{_slug(symbol)}"


# ── helpers ──────────────────────────────────────────────────────────────────


def _top_accepted_strategy(db) -> Optional[Tuple[str, str, dict]]:
    """Return (strategy_name, symbol, params) for the highest-ranked *live-promoted*
    decision — i.e. one that already cleared research/pipeline.py's statistical
    bar (100+ trades, Sharpe floor). Live trading only; paper trading uses
    _paper_candidates() instead since it carries no capital risk and would
    otherwise never accumulate the trade history needed to clear this bar."""
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


def _paper_candidates(db) -> List[Tuple[str, str, dict]]:
    """Best single-symbol strategy per symbol, by avg_sharpe, for paper trading.

    Deliberately ignores research_decisions.accepted and the leaderboard's
    qualifies/pass_ratio gate — both are calibrated for live-capital
    promotion (100+ trades, consistency across windows) and currently reject
    everything single-symbol even when avg_sharpe is strongly positive.
    Paper trading has no capital risk, so it runs on the best signal
    available today and lets the journal accumulate real trade history.
    Pairs strategies (e.g. Statistical Arbitrage) are excluded — paper
    trading's signal path only supports single-symbol generate_signals().
    """
    from monitoring.leaderboard import build_leaderboard
    from strategies.base import BasePairsStrategy
    from strategies.registry import StrategyRegistry

    lb = build_leaderboard(db)
    if lb.empty:
        return []

    lb = lb[lb["avg_sharpe"] > 0]
    if lb.empty:
        return []

    candidates: List[Tuple[str, str, dict]] = []
    for symbol, group in lb.groupby("symbol"):
        best = group.sort_values("avg_sharpe", ascending=False).iloc[0]
        strategy_name = str(best["strategy_name"])
        try:
            strategy = StrategyRegistry.instantiate(strategy_name)
        except KeyError:
            continue
        if isinstance(strategy, BasePairsStrategy):
            continue
        params = best["params"] if isinstance(best["params"], dict) else {}
        candidates.append((strategy_name, str(symbol), params))
    return candidates


def _capital_weighted_equity(
    db, candidates: List[Tuple[str, str, dict]], total_capital: float,
) -> dict:
    """Risk-parity-weighted starting equity per (strategy_name, symbol) slot.

    Reuses the same `_fetch_returns_history` + `PortfolioOptimizer.multi_strategy_allocate`
    machinery monitoring/leaderboard.py already uses to compute its display-only
    risk_parity_alloc_pct column — here it actually sizes paper capital instead
    of just showing a percentage. Falls back to equal-weight automatically
    (handled inside multi_strategy_allocate) when there isn't enough per-window
    return history yet for a candidate. Only affects a slot's *starting* equity
    the first time it's created (see trading.position.load_position) — an
    existing slot's compounded equity is never reset by a weight recompute.
    """
    import json as _json
    from monitoring.leaderboard import _fetch_returns_history
    from portfolio.optimizer import PortfolioOptimizer

    if not candidates:
        return {}

    keys = [
        f"{name}|{symbol}|{_json.dumps(params, sort_keys=True)}"
        for name, symbol, params in candidates
    ]
    returns_pivot = _fetch_returns_history(db)
    optimizer = PortfolioOptimizer(initial_capital=1.0, max_position_size=1.0)
    allocations = optimizer.multi_strategy_allocate(
        strategy_names=keys, returns_history=returns_pivot, capital=total_capital,
    )
    return {
        (name, symbol): allocations[key]
        for (name, symbol, _params), key in zip(candidates, keys)
    }


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


def _open(
    pos: PositionState, side: str, price: float, notional: float, as_of=None,
    stop_price: Optional[float] = None,
) -> None:
    commission = notional * _COMMISSION_PCT
    pos.qty = notional / price
    pos.side = side
    pos.entry_price = price
    from datetime import datetime, timezone
    pos.entry_time = as_of if as_of is not None else datetime.now(timezone.utc)
    pos.equity -= commission
    pos.stop_price = stop_price


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
    pos.stop_price = None
    return pnl


def _stop_breached(pos: PositionState, price: float) -> bool:
    """True if `price` has moved through pos.stop_price against the open side."""
    if pos.is_flat or pos.stop_price is None:
        return False
    if pos.side == "LONG":
        return price <= pos.stop_price
    return price >= pos.stop_price


def _target_notional(pos: PositionState, cfg, extra_cap: Optional[float] = None) -> float:
    """leg_allocation_pct sizing, clipped by the gap-risk safety cap and any
    caller-supplied cap (e.g. live trading's LIVE_MAX_NOTIONAL_USDT)."""
    notional = pos.equity * cfg.leg_allocation_pct
    notional = min(notional, max_safe_notional(pos, cfg))
    if extra_cap is not None:
        notional = min(notional, extra_cap)
    return max(notional, 0.0)


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
    """One paper-trading cycle for one exchange — runs every qualifying
    strategy-symbol candidate (see _paper_candidates) as its own independent
    paper position. Safe to call even before any candidates qualify, or
    before that exchange has price history yet."""
    from config.loader import get_settings
    cfg = get_settings()

    candidates = _paper_candidates(db)
    if not candidates:
        logger.info("paper_trader[%s]: no qualifying strategies yet — skipping cycle", exchange)
        return True

    weighted_equity = _capital_weighted_equity(
        db, candidates, total_capital=cfg.account_size * len(candidates),
    )

    ok = True
    for strategy_name, symbol, params in candidates:
        run_id = _paper_run_id(exchange, strategy_name, symbol)
        initial_capital = weighted_equity.get((strategy_name, symbol), cfg.account_size)
        ok = _run_paper_slot(
            db, cfg, exchange, run_id, strategy_name, symbol, params,
            initial_capital=initial_capital,
        ) and ok
    return ok


def _apply_paper_step(
    db, cfg, pos: PositionState, strategy_name: str, symbol: str, signal: str,
    price: float, bar_time, *, send_alerts: bool = True,
    regime_direction: Optional[str] = None, stop_price: Optional[float] = None,
) -> int:
    """Apply one signal/price/time observation to `pos` in-place: rotation
    close, day rollover, prop-firm checks, stop check, open/close/hold.
    Shared by the live per-cycle path (_run_paper_slot) and the historical
    backfill replay (backfill_paper_slot) — the only differences are where
    signal/price/bar_time come from and whether halt transitions page out
    via send_alert (suppressed during replay — halts from months ago
    shouldn't notify anyone). Returns the number of orders logged.

    regime_direction ("bull"/"bear"/"neutral"/None), when given, blocks
    *opening new* exposure that fights the recent-history trend (top-down
    analysis's "golden rule": only trade with the higher-timeframe bias) —
    it never blocks closing/reducing an existing position. None disables
    the filter entirely (the default — only _run_paper_slot passes a real
    value, gated behind cfg.paper_regime_filter_enabled; backfill replay
    deliberately doesn't use this, to keep historical replay comparable to
    runs made before the filter existed).

    stop_price, when given, is recorded on `pos` if a new position is
    opened this step (see strategies.base.BaseStrategy.get_stop_level —
    only SMCBreakout currently returns one). An existing open position's
    stop is checked *before* the new signal is evaluated; if breached, the
    position is force-closed with close_reason="stop" and no new signal is
    processed this step.
    """
    exchange = pos.exchange
    n_orders = 0

    # Close stale position if the strategy/symbol this run_id tracks rotated
    if not pos.is_flat and (pos.strategy_name != strategy_name or pos.symbol != symbol):
        pnl = _close(pos, price)
        log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="rotation", created_at=bar_time)
        n_orders += 1
        pos.strategy_name = strategy_name
        pos.symbol = symbol

    # Day rollover: reset daily state
    if pos.day_rolled(as_of=bar_time):
        pos.daily_start_equity = pos.mark_to_market(price)
        pos.daily_halt = False

    current_equity = pos.mark_to_market(price)
    was_account_failed, was_daily_halt = pos.account_failed, pos.daily_halt
    _update_propfirm(pos, current_equity, cfg)

    if pos.account_failed and not was_account_failed:
        if send_alerts:
            send_alert(
                f"ACCOUNT FAILED — paper/{exchange}/{symbol}",
                f"Equity {current_equity:.2f} breached the drawdown floor "
                f"({cfg.max_drawdown_pct:.0%} of {cfg.account_size:.2f}). Trading halted.",
            )
    elif pos.daily_halt and not was_daily_halt:
        if send_alerts:
            send_alert(
                f"DAILY LOSS HALT — paper/{exchange}/{symbol}",
                f"Equity {current_equity:.2f} hit the daily loss limit "
                f"({cfg.max_daily_loss_pct:.0%} of {cfg.account_size:.2f}). Halted until tomorrow.",
            )

    # Force-close and stop if account is blown
    if pos.account_failed:
        if not pos.is_flat:
            pnl = _close(pos, price)
            log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="account_failed", created_at=bar_time)
            n_orders += 1
        return n_orders

    # Manual kill switch — no new opens until explicitly resumed via the dashboard
    if pos.manual_halt:
        return n_orders

    # Structural stop: force-close before evaluating any new signal this step.
    if _stop_breached(pos, price):
        pnl = _close(pos, price)
        log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="stop", created_at=bar_time)
        n_orders += 1
        logger.info("paper_trader: STOP %s @ %.4f  pnl=%.2f", symbol, price, pnl)
        pos.equity = pos.mark_to_market(price)
        return n_orders

    # Determine target side
    if signal == "BUY":
        target = "LONG"
    elif signal == "SELL":
        target = "SHORT"
    else:
        target = None

    setup_tag = f"{strategy_name}_{signal.lower()}" if signal in ("BUY", "SELL") else None

    regime_blocks_open = (
        (target == "LONG" and regime_direction == "bear")
        or (target == "SHORT" and regime_direction == "bull")
    )
    if regime_blocks_open:
        logger.info(
            "paper_trader: regime filter skipped %s open for %s %s (regime=%s)",
            target, strategy_name, symbol, regime_direction,
        )

    if target is not None:
        if pos.is_flat:
            if not pos.daily_halt and not regime_blocks_open:
                notional = _target_notional(pos, cfg)
                _open(pos, target, price, notional, as_of=bar_time, stop_price=stop_price)
                log_order(db, pos, "OPEN", price, setup_tag=setup_tag, created_at=bar_time)
                n_orders += 1
                logger.info("paper_trader: OPEN %s %s @ %.4f  equity=%.2f",
                            target, symbol, price, pos.equity)
        elif pos.side != target:
            pnl = _close(pos, price)
            log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="position_flip", created_at=bar_time)
            n_orders += 1
            logger.info("paper_trader: CLOSE (flip) @ %.4f  pnl=%.2f", price, pnl)
            if not pos.daily_halt and not regime_blocks_open:
                notional = _target_notional(pos, cfg)
                _open(pos, target, price, notional, as_of=bar_time, stop_price=stop_price)
                log_order(db, pos, "OPEN", price, setup_tag=setup_tag, created_at=bar_time)
                n_orders += 1
                logger.info("paper_trader: OPEN %s %s @ %.4f  equity=%.2f",
                            target, symbol, price, pos.equity)
    else:
        if not pos.is_flat:
            pnl = _close(pos, price)
            log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="signal_exit", created_at=bar_time)
            n_orders += 1
            logger.info("paper_trader: CLOSE %s @ %.4f  pnl=%.2f", symbol, price, pnl)

    pos.equity = pos.mark_to_market(price)
    return n_orders


def _run_paper_slot(
    db, cfg, exchange: str, run_id: str, strategy_name: str, symbol: str, params: dict,
    initial_capital: Optional[float] = None,
) -> bool:
    """One paper-trading cycle for one (exchange, strategy, symbol) slot.

    initial_capital only matters the first time this run_id is created (see
    trading.position.load_position) — defaults to cfg.account_size (the
    pre-risk-parity flat allocation) when not given, e.g. for direct callers
    that don't go through run_paper_cycle's weighting step.
    """
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
    bar_time = df["timestamp"].iloc[-1]
    stop_level = strategy.get_stop_level(df, params)

    regime_direction = None
    if cfg.paper_regime_filter_enabled:
        from backtesting.window_engine import compute_regime
        _, _, regime_direction = compute_regime(df)

    pos = load_position(db, run_id, initial_capital=initial_capital or cfg.account_size,
                        strategy_name=strategy_name, symbol=symbol, exchange=exchange)

    _apply_paper_step(db, cfg, pos, strategy_name, symbol, signal, price, bar_time,
                       send_alerts=True, regime_direction=regime_direction, stop_price=stop_level)
    save_position(db, pos)

    dd_pct = (pos.peak_equity - pos.equity) / pos.peak_equity * 100 if pos.peak_equity > 0 else 0
    logger.info(
        "paper_trader[%s]: %s | %s | signal=%-4s | equity=%.2f | dd=%.2f%% | halt=%s",
        exchange, strategy_name, symbol, signal, pos.equity, dd_pct, pos.daily_halt,
    )
    return True


# ── backfill replay ───────────────────────────────────────────────────────────


def backfill_paper_slot(
    db, cfg, exchange: str, run_id: str, strategy_name: str, symbol: str, params: dict,
    lookback_days: int,
) -> int:
    """Replay the same mechanics as _run_paper_slot, day-by-day over
    already-collected price history, so paper_orders gets real historical
    OPEN/CLOSE rows today instead of waiting for live signals to trickle in
    one bar at a time. Returns the number of orders written.

    Always starts from a fresh PositionState — these slots were created
    today via the new multi-candidate paper trading, so there's no prior
    state to merge with. The final save_position uses the last historical
    bar's timestamp as last_update, so the next live cycle correctly sees
    day_rolled() == True and rolls cleanly into the present.
    """
    from strategies.registry import StrategyRegistry
    try:
        strategy = StrategyRegistry.instantiate(strategy_name)
    except KeyError:
        logger.error("paper_trader[%s]: unknown strategy %s", exchange, strategy_name)
        return 0

    full_df = db.read_sql(
        """
        SELECT timestamp, open, high, low, close, volume
        FROM prices
        WHERE symbol = %s AND exchange = %s
        ORDER BY timestamp ASC
        """,
        [symbol, exchange],
    )
    min_bars = strategy.get_min_bars(params)
    if full_df.empty or len(full_df) <= min_bars:
        logger.warning("paper_trader[%s]: not enough history to backfill %s", exchange, symbol)
        return 0
    full_df["timestamp"] = pd.to_datetime(full_df["timestamp"], utc=True)

    cutoff = full_df["timestamp"].iloc[-1] - pd.Timedelta(days=lookback_days)
    start_idx = max(int(full_df[full_df["timestamp"] >= cutoff].index.min()), min_bars)
    n_bars = min_bars * 2 + 10

    pos = PositionState(
        run_id=run_id, strategy_name=strategy_name, symbol=symbol, exchange=exchange,
        equity=cfg.account_size, peak_equity=cfg.account_size, daily_start_equity=cfg.account_size,
    )

    n_orders = 0
    for i in range(start_idx, len(full_df)):
        window = full_df.iloc[max(0, i + 1 - n_bars): i + 1]
        if len(window) < min_bars:
            continue
        signal = _last_signal(window, strategy_name, params)
        price = float(window["close"].iloc[-1])
        bar_time = window["timestamp"].iloc[-1]

        n_orders += _apply_paper_step(
            db, cfg, pos, strategy_name, symbol, signal, price, bar_time, send_alerts=False,
        )
        if pos.account_failed:
            break

    save_position(db, pos, last_update=full_df["timestamp"].iloc[-1])
    logger.info(
        "paper_trader[%s]: backfilled %s/%s — %d orders, equity=%.2f",
        exchange, strategy_name, symbol, n_orders, pos.equity,
    )
    return n_orders


def backfill_paper_cycle(db, exchange: str, lookback_days: int = 180) -> int:
    """Backfill every currently-qualifying candidate (see _paper_candidates)
    for one exchange. Returns total orders written across all slots."""
    from config.loader import get_settings
    cfg = get_settings()

    candidates = _paper_candidates(db)
    if not candidates:
        logger.info("paper_trader[%s]: no qualifying strategies yet — skipping backfill", exchange)
        return 0

    total = 0
    for strategy_name, symbol, params in candidates:
        run_id = _paper_run_id(exchange, strategy_name, symbol)
        total += backfill_paper_slot(db, cfg, exchange, run_id, strategy_name, symbol, params, lookback_days)
    return total


def backfill_paper_cycle_all(db, exchange: Optional[str] = None, lookback_days: int = 180) -> int:
    """Backfill every configured exchange (default: all of them). Returns
    total orders written."""
    exchanges = [exchange] if exchange else list(SUPPORTED_EXCHANGES)
    total = 0
    for ex in exchanges:
        total += backfill_paper_cycle(db, exchange=ex, lookback_days=lookback_days)
    return total


# ── kill switch ──────────────────────────────────────────────────────────────


def _force_close_one(db, exchange: str, pos: PositionState) -> dict:
    """Flatten a single already-loaded paper position and set manual_halt."""
    if pos.is_flat:
        pos.manual_halt = True
        save_position(db, pos)
        send_alert(f"KILL SWITCH — paper/{exchange}", "Triggered with no open position. Trading halted.")
        return {"exchange": exchange, "run_id": pos.run_id, "closed": False, "manual_halt": True}

    df = _latest_bars(db, pos.symbol, 5, exchange=exchange)
    if df is None or df.empty:
        return {"exchange": exchange, "run_id": pos.run_id, "closed": False, "error": "no price data"}
    price = float(df["close"].iloc[-1])
    pnl = _close(pos, price)
    log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="kill_switch")
    pos.manual_halt = True
    save_position(db, pos)
    logger.warning("paper_trader[%s]: KILL SWITCH — closed @ %.4f  pnl=%.2f", exchange, price, pnl)
    send_alert(f"KILL SWITCH — paper/{exchange}", f"Closed @ {price:.4f}  pnl={pnl:.2f}. Trading halted.")
    return {"exchange": exchange, "run_id": pos.run_id, "closed": True, "pnl": pnl, "manual_halt": True}


def force_close_now(db, exchange: str, mode: str = "paper", run_id: Optional[str] = None):
    """Flatten one paper/live position (or every open paper slot on an
    exchange when run_id is omitted) and set manual_halt.

    Paper positions are closed at the latest known price (no exchange order
    needed). Live positions are closed via a real limit order through
    ``trading.live_trader``'s force-close path. Returns a single status dict
    when run_id is given (or for live, which is always single-slot), or a
    list of status dicts when flattening every paper slot on an exchange.
    """
    if mode == "live":
        pos = load_position(db, run_id or f"live_{exchange}", exchange=exchange)
        from trading.live_trader import force_close_live
        return force_close_live(db, pos)

    if run_id is not None:
        pos = load_position(db, run_id, exchange=exchange)
        return _force_close_one(db, exchange, pos)

    positions = list_positions(db, exchange, run_id_prefix=f"paper_{exchange}")
    if not positions:
        return [{"exchange": exchange, "closed": False, "manual_halt": False, "error": "no open paper slots"}]
    return [_force_close_one(db, exchange, pos) for pos in positions]


def resume_trading(db, exchange: str, mode: str = "paper", run_id: Optional[str] = None):
    """Clear manual_halt for one position, or every open paper slot on an
    exchange when run_id is omitted."""
    if run_id is not None or mode == "live":
        pos = load_position(db, run_id or f"{mode}_{exchange}", exchange=exchange)
        pos.manual_halt = False
        save_position(db, pos)
        return {"exchange": exchange, "run_id": pos.run_id, "manual_halt": False}

    positions = list_positions(db, exchange, run_id_prefix=f"paper_{exchange}")
    resumed = []
    for pos in positions:
        pos.manual_halt = False
        save_position(db, pos)
        resumed.append({"exchange": exchange, "run_id": pos.run_id, "manual_halt": False})
    return resumed


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
