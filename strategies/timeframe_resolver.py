"""Top-down multi-timeframe signal resolver.

Hierarchy: 1w/1d set the directional bias; 4h confirms; 1h times the entry.
A BUY entry requires bull bias on both higher timeframes and a BUY signal on
4h and 1h. A SELL entry requires bear bias on both and SELL on 4h and 1h.
Any disagreement between timeframes produces HOLD.

If a timeframe has no bars yet (collector hasn't run for it), the resolver
degrades gracefully to the next coarser timeframe rather than blocking all
signals.
"""

from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd

from config.logging_config import get_logger

logger = get_logger(__name__)


class TopDownSignalResolver:
    """Resolve a trading signal using top-down multi-timeframe analysis."""

    BIAS_TFS = ("1w", "1d")
    CONFIRM_TF = "4h"
    ENTRY_TF = "1h"

    def resolve(
        self,
        db,
        strategy,
        params: dict,
        symbol: str,
        exchange: str,
    ) -> Tuple[str, str]:
        """Return (signal, source_timeframe).

        signal: "BUY" | "SELL" | "HOLD"
        source_timeframe: the timeframe that produced the entry ("1h", "4h", "1d").
        Falls back to coarser timeframes when finer bars are unavailable.
        """
        min_bars = strategy.get_min_bars(params)
        n_fetch = min_bars * 2 + 10

        # ── 1. Higher-timeframe bias (1w then 1d) ────────────────────────────
        bias = self._compute_bias(db, symbol, exchange)

        # ── 2. 4h confirmation ───────────────────────────────────────────────
        confirm_signal, confirm_tf = self._get_signal(
            db, strategy, params, symbol, exchange, self.CONFIRM_TF, n_fetch, min_bars
        )

        # ── 3. 1h entry ──────────────────────────────────────────────────────
        entry_signal, entry_tf = self._get_signal(
            db, strategy, params, symbol, exchange, self.ENTRY_TF, n_fetch, min_bars
        )

        # ── 4. Gate: all three must agree ────────────────────────────────────
        if bias == "bull" and confirm_signal == "BUY" and entry_signal == "BUY":
            logger.debug(
                "timeframe_resolver: BUY %s %s — bias=bull 4h=%s 1h=%s",
                strategy.__class__.__name__, symbol, confirm_signal, entry_signal,
            )
            return "BUY", entry_tf

        if bias == "bear" and confirm_signal == "SELL" and entry_signal == "SELL":
            logger.debug(
                "timeframe_resolver: SELL %s %s — bias=bear 4h=%s 1h=%s",
                strategy.__class__.__name__, symbol, confirm_signal, entry_signal,
            )
            return "SELL", entry_tf

        # If there are no intraday bars at all, fall back to 1d signal so paper
        # trading keeps accumulating history even before 1h/4h data arrives.
        if entry_tf == "1d":
            fallback_signal, fallback_tf = self._get_signal(
                db, strategy, params, symbol, exchange, "1d", n_fetch, min_bars
            )
            if fallback_signal in ("BUY", "SELL"):
                logger.debug(
                    "timeframe_resolver: fallback 1d signal=%s for %s %s",
                    fallback_signal, strategy.__class__.__name__, symbol,
                )
            return fallback_signal, fallback_tf

        return "HOLD", entry_tf

    # ── internals ─────────────────────────────────────────────────────────────

    def _fetch_bars(
        self,
        db,
        symbol: str,
        exchange: str,
        timeframe: str,
        n: int,
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

    def _compute_bias(self, db, symbol: str, exchange: str) -> str:
        """Return 'bull', 'bear', or 'neutral' from 1w and 1d regime direction.

        Requires both 1w and 1d to agree; falls back to 1d-only when 1w has
        no bars yet. Returns 'neutral' when either disagrees or data missing.
        """
        from backtesting.window_engine import compute_regime

        directions = []
        for tf in self.BIAS_TFS:
            df = self._fetch_bars(db, symbol, exchange, tf, 60)
            if df is not None and len(df) >= 5:
                _, _, direction = compute_regime(df)
                directions.append(direction)

        if not directions:
            return "neutral"
        if len(directions) == 1:
            return directions[0]
        # Both timeframes must agree
        return directions[0] if directions[0] == directions[1] else "neutral"

    def _get_signal(
        self,
        db,
        strategy,
        params: dict,
        symbol: str,
        exchange: str,
        timeframe: str,
        n: int,
        min_bars: int,
    ) -> Tuple[str, str]:
        """Return (signal, timeframe_used). Degrades to coarser timeframe when
        the requested bars aren't available."""
        df = self._fetch_bars(db, symbol, exchange, timeframe, n)
        if df is not None and len(df) >= min_bars:
            try:
                signals = strategy.generate_signals(df, params)
                signal = str(signals.iloc[-1]) if len(signals) > 0 else "HOLD"
                return signal, timeframe
            except Exception as exc:
                logger.warning(
                    "timeframe_resolver: signal error on %s %s %s: %s",
                    timeframe, symbol, strategy.__class__.__name__, exc,
                )

        # Degrade: 4h → 1d, 1h → 4h → 1d
        fallbacks = {"4h": ["1d"], "1h": ["4h", "1d"]}
        for coarser in fallbacks.get(timeframe, []):
            df2 = self._fetch_bars(db, symbol, exchange, coarser, n)
            if df2 is not None and len(df2) >= min_bars:
                try:
                    signals = strategy.generate_signals(df2, params)
                    signal = str(signals.iloc[-1]) if len(signals) > 0 else "HOLD"
                    logger.debug(
                        "timeframe_resolver: degraded %s→%s for %s %s",
                        timeframe, coarser, symbol, strategy.__class__.__name__,
                    )
                    return signal, coarser
                except Exception:
                    continue

        return "HOLD", timeframe
