"""
Backtesting Engine Audit for RAFund ML4T

This audit reviews the existing backtest implementation in `backtesting/engine_eval.py`.
The current project does not contain `backtesting/engine.py`, so the engine behavior is based on the specialized evaluation engine present today.

1. How is the train/test split currently handled? (fixed date? fixed window? none?)
   - The engine uses a fixed training window of `lookback` days (default 60 days) at the start of the data.
   - After the training window, the remaining data is effectively treated as the test period.
   - The strategy performs signal generation once for the entire series, then only trades after the training cutoff.
   - Tag: FIX REQUIRED
     - This is not a genuine walk-forward split and there is no rolling out-of-sample evaluation.

2. Are transaction costs applied per-trade or as a bulk adjustment?
   - Transaction costs are applied per trade via a fixed `commission` percentage.
   - `open_spread` deducts cost from cash on entry, and `_close_spread` applies commission on exit proceeds.
   - Tag: OK

3. Is slippage modelled? If so, how?
   - No explicit slippage model is implemented.
   - Only commission is applied; price execution assumes market fills at bid/ask equivalents without slippage.
   - Tag: FIX REQUIRED

4. Are the prop-firm rules enforced during the backtest loop, or only calculated after the fact in the metrics report?
   - Prop-firm enforcement is performed in the loop via `EvaluationTracker`.
   - The loop checks `tracker.status` and `tracker.can_trade()` each bar, halting trading and closing positions on failures.
   - There is also stop-loss and max-holding logic inside the loop.
   - Tag: OK
     - However, rule state is tied to `EvaluationTracker` and does not expose a richer `PropFirmState` structure for downstream reporting.

5. What performance metrics are returned and how are they calculated?
   - Returned metrics include:
     - `initial_capital`
     - `final_value`
     - `total_return`, `total_return_pct`
     - `sharpe_ratio` computed as daily mean return / daily std * sqrt(252)
     - `max_drawdown` computed from daily equity series
     - `num_trades`, `num_closed_trades`, `win_rate`, `win_rate_pct`
     - `mean_daily_return`, `daily_volatility`
   - The engine also returns raw `trades`, `daily_equity`, `signals_df`, and evaluation summary.
   - Tag: OK

6. What are the inputs and outputs of the BacktestEngine.run() method (full signature)?
   - Current engine method is `EvaluationBacktestEngine.run(self, prices: pd.DataFrame) -> Dict`.
   - Input: a DataFrame of aligned prices for two symbols with `timestamp`, `close_a`, and `close_b`.
   - Output: a dictionary containing performance metrics, trade list, equity curve, signals, and evaluation summary.
   - Tag: FIX REQUIRED
     - This is not a generic engine interface; it is specific to pair trading and does not separate training from out-of-sample evaluation.

7. List any hardcoded values (dates, symbols, capital amounts) that should be parameters.
   - `symbol_a` and `symbol_b` default to `BTC/USDT` and `ETH/USDT`.
   - `lookback` (training window) is defaulted to 60 days.
   - `leg_allocation_pct` default 0.18.
   - `commission` default 0.001.
   - `stop_loss_spread_pct` default 0.03.
   - `max_holding_days` default 30.
   - `rules` default account size and prop-firm values are embedded in `PropFirmRules`.
   - Tag: FIX REQUIRED
     - Many strategy and evaluation parameters should be explicit and configurable for walk-forward validation.

"""
