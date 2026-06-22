# Candlestick pattern reference

Source: "The Candlestick Trading Bible" (KohanFx.com). Captured here for
future strategy work — these are the foundational, objective patterns; the
book covers many more, but most require subjective judgment to identify.
Only patterns with clear, measurable rules are listed.

## Engulfing bar (most objective — used in `strategies/smc_breakout.py`)

- **Bullish**: current candle's body fully engulfs the prior candle's body —
  `close > prior_open` and `open < prior_close`. Stronger when it forms after
  a clear downtrend or at a support/discount level.
- **Bearish**: mirror — current body engulfs prior body in the down
  direction. Stronger after an uptrend or at resistance/premium.
- Why this one: clear numeric definition (compare 4 prices), no wick/shape
  judgment calls, trivially codable as a vectorized check on OHLC columns.

## Pin bar / hammer (inside-bar variant)

- **Bullish (hammer)**: small body, long lower wick (typically ≥2x body),
  small/no upper wick — signals rejection of lower prices. Most reliable at a
  support zone.
- **Bearish (shooting star)**: mirror — long upper wick, rejection of higher
  prices, most reliable at resistance.
- More codable than it looks (wick-to-body ratio is measurable) but the
  "most reliable at support/resistance" qualifier reintroduces the same
  zone-identification problem as order blocks/FVGs — not used standalone
  in this codebase yet.

## Doji

- Open and close nearly equal, extended wicks either side. Signals
  indecision, not direction — needs context (prior trend, location) to mean
  anything. Not used as a standalone signal anywhere in this codebase;
  noted here only because it's foundational, not because it's recommended
  for automated entry logic on its own.

## Why only engulfing made it into code

Pin bar and doji both require a "where it forms" qualifier to be meaningful,
which means another subjective zone-detection step. Engulfing bar's rule is
self-contained (just the current and prior candle), which is why it was
picked as the single confirmation filter for `SMCBreakout.generate_signals()`
rather than building a multi-pattern detector. If a future strategy needs
pin-bar logic, the wick/body ratio check is straightforward to add the same
way — but pair it with an explicit, coded zone definition (like the
discount/premium logic already in `smc_breakout.py`), not a vague "at
support" condition.
