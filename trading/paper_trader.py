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
    """All single-symbol strategy-symbol combos with engine_results, for paper trading.

    Paper mode runs ALL strategies simultaneously — no strategy is excluded.
    Every strategy logs entries, exits, PnL, drawdown, and timeframe chain to the
    journal so the leaderboard can compute ready_for_live scores from real paper data.

    Exclusions:
      - Pairs strategies (require generate_signals_pair, not generate_signals).
      - DCA / HODL when eval_mode=True — these are incompatible with a 3% MDD envelope.

    Leaderboard qualifies/pass_ratio gates are NOT applied here — they are calibrated
    for live-capital promotion. Paper has no capital risk and needs to accumulate
    trade history across ALL strategies to populate ready_for_live scores.
    """
    from config.loader import get_settings
    from monitoring.leaderboard import build_leaderboard
    from strategies.base import BasePairsStrategy
    from strategies.registry import StrategyRegistry

    cfg = get_settings()

    # Eval mode: run ONLY the single best ready_for_live strategy (auto-selected)
    if cfg.eval_mode:
        from monitoring.leaderboard import select_eval_strategy
        result = select_eval_strategy(db)
        if result is None:
            logger.warning(
                "paper_trader: eval_mode=True but no ready_for_live strategy — "
                "continue paper trading to accumulate history"
            )
            return []
        return [result]

    lb = build_leaderboard(db)
    if lb.empty:
        return []

    # DCA and HODL accumulate slowly and don't pass prop-firm constraints;
    # exclude them in eval mode (handled above) and let them paper-trade freely otherwise.
    candidates: List[Tuple[str, str, dict]] = []
    seen: set = set()
    for _, row in lb.iterrows():
        strategy_name = str(row["strategy_name"])
        symbol = str(row["symbol"])
        key = (strategy_name, symbol)
        if key in seen:
            continue
        seen.add(key)

        try:
            strategy = StrategyRegistry.instantiate(strategy_name)
        except KeyError:
            continue
        if isinstance(strategy, BasePairsStrategy):
            continue

        params = row["params"] if isinstance(row["params"], dict) else {}
        candidates.append((strategy_name, symbol, params))

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


def _latest_bars(
    db, symbol: str, n: int, exchange: str = "binance", timeframe: str = "1d"
) -> Optional[pd.DataFrame]:
    df = db.read_sql(
        """
        SELECT timestamp, open, high, low, close, volume
        FROM prices
        WHERE symbol = %s AND exchange = %s AND timeframe = %s
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        [symbol, exchange, timeframe, n],
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


def _target_notional(
    pos: PositionState,
    cfg,
    extra_cap: Optional[float] = None,
    current_price: Optional[float] = None,
    stop_price: Optional[float] = None,
) -> float:
    """leg_allocation_pct sizing, clipped by the dual safety cap (gap-risk headroom
    AND per-trade risk budget) plus any caller-supplied hard cap.

    Soft-floor halving: when cumulative drawdown from initial capital is between
    soft_floor_amount and hard_floor_amount, position size is halved as a
    first-response measure before the hard floor fires.
    """
    stop_dist = None
    if stop_price is not None and current_price is not None and current_price > 0:
        stop_dist = abs(stop_price - current_price) / current_price

    notional = pos.equity * cfg.leg_allocation_pct
    notional = min(notional, max_safe_notional(pos, cfg, stop_distance_pct=stop_dist))

    # Soft-floor halving: DD from initial capital ≥ soft_floor_amount → 50% sizing.
    # Sizing returns to normal only when DD recovers below soft_floor_recovery_amount.
    dd_from_initial = cfg.account_size - pos.equity
    soft_recovery = getattr(cfg, 'soft_floor_recovery_amount', getattr(cfg, 'soft_floor_amount', 130.0))
    soft_active = (
        hasattr(cfg, 'soft_floor_amount')
        and dd_from_initial >= cfg.soft_floor_amount
        and dd_from_initial >= soft_recovery
    )
    if soft_active:
        notional *= 0.5

    if extra_cap is not None:
        notional = min(notional, extra_cap)
    return max(notional, 0.0)


def _update_propfirm(pos: PositionState, current_equity: float, cfg) -> str:
    """Update peak, daily-halt, account_failed in-place.

    Returns a string event tag:
      ""            — nothing changed
      "soft_floor"  — soft floor newly crossed (size halved next trade, no halt)
      "hard_floor"  — hard floor newly crossed (halt + close needed by caller)
      "daily_halt"  — daily loss limit hit
      "account_failed" — already failed (no-op, reported for logging)

    Two-tier floor system (paper/live):
      Soft floor (cfg.soft_floor_amount below initial capital, e.g. $130 DD):
        → halves position sizing via _target_notional; no halt; logs WARNING.
        → sizing restores once DD drops below soft_floor_recovery_amount.
      Hard floor (cfg.hard_floor_amount below initial capital, e.g. $145 DD):
        → sets account_failed = True and daily_halt = True.
        → caller must force-close open positions and log the event to paper_orders.
        → $5 buffer before the prop firm's actual $150 floor absorbs close slippage.

    Manual kill-switch (force_close_now) is an emergency-only operator escape hatch.
    The two-tier floor system is the primary automated protection — no manual
    intervention is needed for normal drawdown scenarios.
    """
    if pos.account_failed:
        return "account_failed"

    if current_equity > pos.peak_equity:
        pos.peak_equity = current_equity

    event = ""

    # ── Soft floor (warning tier, no halt) ───────────────────────────────────
    dd_from_initial = cfg.account_size - current_equity
    soft_floor_amount = getattr(cfg, 'soft_floor_amount', 130.0)
    if dd_from_initial >= soft_floor_amount:
        event = "soft_floor"
        logger.warning(
            "SOFT FLOOR TRIGGERED — equity=%.2f dd=%.2f — position size halved",
            current_equity, dd_from_initial,
        )

    # ── Daily loss limit ──────────────────────────────────────────────────────
    daily_pnl = current_equity - pos.daily_start_equity
    if daily_pnl <= -cfg.account_size * cfg.max_daily_loss_pct:
        if not pos.daily_halt:
            pos.daily_halt = True
            event = "daily_halt"
            logger.warning(
                "paper_trader: daily halt — daily_pnl=%.2f limit=%.2f",
                daily_pnl, -cfg.account_size * cfg.max_daily_loss_pct,
            )

    # ── Hard floor (internal buffer, $5 before prop firm's actual floor) ──────
    hard_floor_equity = getattr(cfg, 'hard_floor_equity', cfg.account_size * (1.0 - cfg.max_drawdown_pct) + 5.0)
    if current_equity <= hard_floor_equity:
        pos.account_failed = True
        pos.daily_halt = True
        event = "hard_floor"
        logger.error(
            "HARD FLOOR TRIGGERED — equity=%.2f hard_floor=%.2f — all trading halted",
            current_equity, hard_floor_equity,
        )

    return event


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
    source_timeframe: Optional[str] = None,
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
        log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="rotation",
                  source_timeframe=source_timeframe, created_at=bar_time)
        n_orders += 1
        pos.strategy_name = strategy_name
        pos.symbol = symbol

    # Day rollover: reset daily state and trade count
    if pos.day_rolled(as_of=bar_time):
        pos.daily_start_equity = pos.mark_to_market(price)
        pos.daily_halt = False
        pos.daily_trade_count = 0

    current_equity = pos.mark_to_market(price)
    was_account_failed, was_daily_halt = pos.account_failed, pos.daily_halt
    floor_event = _update_propfirm(pos, current_equity, cfg)

    if floor_event == "hard_floor" and not was_account_failed:
        hard_floor_eq = getattr(cfg, 'hard_floor_equity', cfg.account_size * 0.97 + 5.0)
        if send_alerts:
            send_alert(
                f"HARD FLOOR — paper/{exchange}/{symbol}",
                f"Equity {current_equity:.2f} hit internal hard floor ({hard_floor_eq:.2f}). "
                f"All trading halted. Review journal before resuming.",
            )
        # Persist the hard-floor halt event to paper_orders for auditability.
        log_order(db, pos, "HARD_FLOOR", price, close_reason="hard_floor",
                  source_timeframe=source_timeframe, created_at=bar_time)
        n_orders += 1
    elif floor_event == "soft_floor":
        soft_floor_eq = getattr(cfg, 'soft_floor_equity', cfg.account_size - 130.0)
        if send_alerts:
            send_alert(
                f"SOFT FLOOR — paper/{exchange}/{symbol}",
                f"Equity {current_equity:.2f} crossed soft floor ({soft_floor_eq:.2f}). "
                f"Position size halved until DD recovers below "
                f"${getattr(cfg, 'soft_floor_recovery_amount', 100):.0f}.",
            )
    elif floor_event == "daily_halt" and not was_daily_halt:
        if send_alerts:
            send_alert(
                f"DAILY LOSS HALT — paper/{exchange}/{symbol}",
                f"Equity {current_equity:.2f} hit the daily loss limit "
                f"({cfg.max_daily_loss_pct:.0%} of {cfg.account_size:.2f}). Halted until tomorrow.",
            )

    # Force-close and stop if account is blown (hard floor or other failure)
    if pos.account_failed:
        if not pos.is_flat:
            pnl = _close(pos, price)
            log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="account_failed",
                      source_timeframe=source_timeframe, created_at=bar_time)
            n_orders += 1
        return n_orders

    # Emergency manual halt (operator escape hatch — auto two-tier floor is primary).
    # This only fires if an operator explicitly called force_close_now() externally.
    if pos.manual_halt:
        return n_orders

    # Structural stop: force-close before evaluating any new signal this step.
    if _stop_breached(pos, price):
        pnl = _close(pos, price)
        log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="stop",
                  source_timeframe=source_timeframe, created_at=bar_time)
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

    # Max trades per day cap: in eval_mode default=3, else default=10
    max_daily = getattr(cfg, 'max_trades_per_day', 10)
    daily_cap_hit = pos.daily_trade_count >= max_daily
    if daily_cap_hit and target is not None and pos.is_flat:
        logger.info(
            "paper_trader: daily trade cap (%d) hit for %s %s — skipping %s",
            max_daily, strategy_name, symbol, target,
        )
        target = None  # treat remaining signal as HOLD

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
                notional = _target_notional(pos, cfg, current_price=price, stop_price=stop_price)
                _open(pos, target, price, notional, as_of=bar_time, stop_price=stop_price)
                log_order(db, pos, "OPEN", price, setup_tag=setup_tag,
                          source_timeframe=source_timeframe, created_at=bar_time)
                pos.daily_trade_count += 1
                n_orders += 1
                logger.info("paper_trader: OPEN %s %s @ %.4f  equity=%.2f",
                            target, symbol, price, pos.equity)
        elif pos.side != target:
            pnl = _close(pos, price)
            log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="position_flip",
                      source_timeframe=source_timeframe, created_at=bar_time)
            n_orders += 1
            logger.info("paper_trader: CLOSE (flip) @ %.4f  pnl=%.2f", price, pnl)
            if not pos.daily_halt and not regime_blocks_open:
                notional = _target_notional(pos, cfg, current_price=price, stop_price=stop_price)
                _open(pos, target, price, notional, as_of=bar_time, stop_price=stop_price)
                log_order(db, pos, "OPEN", price, setup_tag=setup_tag,
                          source_timeframe=source_timeframe, created_at=bar_time)
                pos.daily_trade_count += 1
                n_orders += 1
                logger.info("paper_trader: OPEN %s %s @ %.4f  equity=%.2f",
                            target, symbol, price, pos.equity)
    else:
        if not pos.is_flat:
            pnl = _close(pos, price)
            log_order(db, pos, "CLOSE", price, pnl=pnl, close_reason="signal_exit",
                      source_timeframe=source_timeframe, created_at=bar_time)
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

    Uses TopDownSignalResolver when cfg.paper_regime_filter_enabled is True:
    1w/1d bias + 4h confirmation + 1h entry → (signal, source_timeframe).
    Falls back to a plain 1d signal when the resolver is disabled.
    """
    from strategies.registry import StrategyRegistry
    try:
        strategy = StrategyRegistry.instantiate(strategy_name)
    except KeyError:
        logger.error("paper_trader[%s]: unknown strategy %s", exchange, strategy_name)
        return False

    source_timeframe: Optional[str] = None
    regime_direction: Optional[str] = None

    if cfg.paper_regime_filter_enabled:
        from strategies.timeframe_resolver import TopDownSignalResolver
        resolver = TopDownSignalResolver()
        signal, source_timeframe = resolver.resolve(db, strategy, params, symbol, exchange)
        # Derive regime_direction from the resolver's own bias for the regime filter
        from backtesting.window_engine import compute_regime
        # Use 1d bars for regime_direction (matches existing filter semantics)
        df_1d = _latest_bars(db, symbol, strategy.get_min_bars(params) * 2 + 10,
                             exchange=exchange, timeframe="1d")
        if df_1d is not None and len(df_1d) >= 5:
            _, _, regime_direction = compute_regime(df_1d)
        # Use the 1d bars as the price/bar_time reference for close execution
        df = df_1d
    else:
        df = _latest_bars(db, symbol, strategy.get_min_bars(params) * 2 + 10,
                          exchange=exchange, timeframe="1d")
        if df is None or len(df) < strategy.get_min_bars(params):
            logger.warning("paper_trader[%s]: not enough bars for %s (%d)",
                           exchange, symbol, 0 if df is None else len(df))
            return True
        signal = _last_signal(df, strategy_name, params)
        source_timeframe = "1d"

    if df is None or len(df) < strategy.get_min_bars(params):
        logger.warning("paper_trader[%s]: not enough bars for %s (%d)",
                       exchange, symbol, 0 if df is None else len(df))
        return True

    price = float(df["close"].iloc[-1])
    bar_time = df["timestamp"].iloc[-1]
    stop_level = strategy.get_stop_level(df, params)

    pos = load_position(db, run_id, initial_capital=initial_capital or cfg.account_size,
                        strategy_name=strategy_name, symbol=symbol, exchange=exchange)

    _apply_paper_step(
        db, cfg, pos, strategy_name, symbol, signal, price, bar_time,
        send_alerts=True, regime_direction=regime_direction,
        stop_price=stop_level, source_timeframe=source_timeframe,
    )
    save_position(db, pos)

    dd_pct = (pos.peak_equity - pos.equity) / pos.peak_equity * 100 if pos.peak_equity > 0 else 0
    logger.info(
        "paper_trader[%s]: %s | %s | signal=%-4s | tf=%-3s | equity=%.2f | dd=%.2f%% | halt=%s",
        exchange, strategy_name, symbol, signal, source_timeframe or "?",
        pos.equity, dd_pct, pos.daily_halt,
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
