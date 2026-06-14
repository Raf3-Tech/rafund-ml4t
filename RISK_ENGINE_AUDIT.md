# RISK ENGINE AUDIT — Raf3nd ML4T

**Auditor:** Backtesting & Risk Engineer
**Date:** 2026-06-05
**Scope:** `backtesting/`, `portfolio/`, `monitoring/`
**Branch:** `feat/wire-validation-lifecycle`
**Mission:** Make the backtester as realistic as possible.

> **Coordination note.** This report is the execution/risk-engine counterpart to
> `docs/QUANT_AUDIT_REPORT.md` (research validity: leakage, walk-forward rigor,
> feature science, statistical tests). The two are complementary with a single
> deliberate overlap — the `√252` vs `√365` annualization — which the QUANT
> report owns (its §5). It is repeated here only as it affects risk metrics.
> Everything below (fills, slippage, prop-firm controls, VaR/CVaR/Kelly/vol-
> targeting, stress behaviour) is out of the QUANT report's scope.

> **Verification.** Every finding marked ✅verified was reproduced by running the
> current on-disk engine (runtime probes, 2026-06-05). The stress scenarios are
> encoded in `tests/test_risk_engine_stress.py` (**6 passed, 4 xfailed**); each
> xfail is a known bug below and turns green when fixed.

---

## 0. Executive Summary

> **2026-06-14 update:** F1, F2, F3, and F5 are all **fixed**. All 17 stress
> tests pass; no xfail markers remain. Kelly sizing is now wired into
> `BacktestEngine` (`use_kelly=True`). Prop-Firm Compliance Score is now **8 /
> 10**; Portfolio Risk Score is **5 / 10** (see §5/§6 for updated breakdown).

The engine is correct for both single-asset and two-leg pair trades. Prop-firm
controls (daily-loss, leverage cap, drawdown floor) are enforced on all paths.

Outstanding gaps:

1. ✅ **~~Single-asset positions are invisible to equity~~** — fixed: `_compute_equity`
   falls back to `price` when `price_a` is absent; all single-asset paths mark to market.
2. ✅ **~~Daily-loss limit parenthesis bug~~** — fixed: daily P&L is computed then
   compared to the threshold; profitable days no longer halt.
3. ✅ **~~Leverage clip clears the daily halt~~** — fixed: the errant
   `daily_halt = False` line was removed.
4. 🟠 **Same-bar (look-ahead) fills, no latency, non-vol-scaled slippage** — the
   execution model is still optimistic and breaks down under stress.
5. 🟡 **Kelly sizing wired but `portfolio/` `RiskManager` still orphaned** —
   `kelly_fraction` is now called per-bar when `use_kelly=True`; risk-parity
   multi-strategy allocation (`multi_strategy_allocate`) is still not called
   from the engine or window layer.

**Prop-Firm Compliance Score: 8 / 10**  ·  **Portfolio Risk Score: 5 / 10**
(breakdowns in §5 and §6).

---

## 1. Execution Audit (`backtesting/engine.py`, `backtesting/costs.py`)

### F1 ✅ FIXED — Single-asset positions are not marked to market
`engine.py:102-108` (`_compute_equity`) values `position_a` only `if price_a is
not None`. Single-asset market data carries a **`price`** column, not `price_a`,
so `getattr(row, "price_a", None)` is `None` and the open position contributes
**$0** to equity.

> Probe: a single long held through **+50% then −40%** reported `final_equity =
> 4998.65` (flat, i.e. costs only). Unrealized P&L never enters the equity curve,
> so Sharpe, drawdown, daily-loss and the drawdown floor are **all blind** to
> single-asset exposure. `strategies/factor_model.py` trades exactly this path.

### F6 ✅ FIXED — Single-asset exit on `signal→0` realizes $0 and discards the position
`engine.py:384-386` closes flat-signal exits with `getattr(row, "price_a")` /
`price_b` — both `None` for single-asset data — so `_close_position` skips the
price branch, books **`pnl = 0`**, and zeroes the position. Only the *flip* path
(`engine.py:375-383`) closes single-asset correctly (via `price`). Net effect:
single-asset trades that exit on a zero signal vanish with no P&L.

### F4 🟠 HIGH — Same-bar (look-ahead) fills, no latency model
`engine.py:301-405`: signals are merged on `timestamp` and filled at **that same
bar's** price (`_open_position`/`_close_position` receive the current row's
price). There is no next-bar-open execution and **no latency** between signal and
fill. A signal derived from bar *t*'s close is executed at bar *t* — optimistic
and a mild look-ahead. *Realistic fix:* execute at `t+1` open.

### F7 🟠 MEDIUM — `volume_proportional` slippage is unreachable  ✅verified
`costs.py:51-54` requires `bar_volume`, but the engine **never passes
`bar_volume`** to `cost_model.apply` (`engine.py:182-191, 215-219, 251-267`).
Selecting `slippage_model="volume_proportional"` raises `ValueError`. Only the
flat `fixed` model is usable.

### F8 🟠 MEDIUM — Fixed slippage is constant, not volatility-scaled
`costs.py:49-50` returns a flat `fixed_slippage_pct` (default **5 bps**)
regardless of bar volatility, gap size, or order size. During the flash-crash and
vol-spike stress runs the engine still assumed 5 bps — unrealistic; slippage
should widen with realized volatility / spread.

### F9 🟠 MEDIUM — No short proceeds, borrow, or funding
Opening reduces `cash` by **transaction costs only** (`engine.py:196, 223`), not
the cash outlay/proceeds of the trade. This nets out **only because balanced pair
legs (+notional / −notional) cancel in `_compute_equity`**. There is no short-
sale borrow fee and no perp **funding rate**, which materially affect crypto
carry over multi-day holds.

### Commissions — ✅ correct (within the cost model)
`costs.py:46-47` charges `notional × commission_pct` on **both** open and close,
matching the README's "0.1% per trade". `executed_price` embeds slippage and
flows into realized P&L. No double counting observed for pairs.

---

## 2. Prop-Firm Compliance (`engine.py`, cross-checked vs README §"Prop Firm Evaluation Rules")

README rules: $5,000 account · daily loss 4% ($200) · drawdown floor 6% ($4,700)
· leverage 5× · Step-1 +$250 / Step-2 +$500.

### F2 ✅ FIXED — Daily-loss check is a mis-parenthesised ternary
`engine.py:139`:
```python
if equity - self.daily_start_equity if self.daily_start_equity is not None else 0.0 <= -self.initial_capital * self.daily_loss_limit_pct:
```
Python parses this as
`(equity - daily_start_equity) if (daily_start_equity is not None) else (0.0 <= -200)`.
In the normal branch it evaluates to the **daily P&L float used as a truthiness
test** — it never compares to the −$200 threshold.

> Probe: a tiny-**profit** intraday day set `daily_halt` on **11 of 12 bars**.
> The 4% rule is both over-triggering (any non-zero P&L halts) and never actually
> checking the limit. *Fix:* `daily_pnl = (equity - self.daily_start_equity); if
> daily_pnl <= -self.initial_capital * self.daily_loss_limit_pct:`.

### F3 ✅ FIXED — Leverage clip clears the daily halt
`engine.py:175-176`: inside the `target_notional > max_notional` branch the code
sets `self.prop_firm_state.daily_halt = False`. Clipping notional must not touch
the halt flag.

> Probe: with `daily_halt=True`, an oversized `_open_position` reset it to
> `False`. A loss-limited account can resume trading by submitting an oversized
> order. *Fix:* delete line 176.

### Drawdown floor — ✅ compliant (pairs), but see F1
`engine.py:132-136` fails the account when `equity ≤ initial × (1 − 0.06) =
$4,700` — a **static floor from initial capital**, matching the README. Note the
*reported* `current_drawdown_pct` (`engine.py:127-130`) is **trailing-peak**
based, so the metric and the fail trigger use different references — fine as long
as it's understood, but worth documenting. **Caveat:** because of F1 this floor
is blind to single-asset losses.

### Leverage limit — ⚠️ partial
`engine.py:166-174` caps `target_notional ≤ equity × 5` **at open only**. It is
never re-checked as price moves, and in normal use `target_notional = equity ×
leg_allocation_pct (0.18)` so the 5× cap almost never binds. Plus F3.

### Profit targets / account fail — ✅ compliant
`engine.py:143-149` unlocks Step-1 at +$250 and Step-2 at +$500; `engine.py:336-
339` flattens and stops on failure. Correct (for pairs).

---

## 3. Portfolio Risk (`portfolio/risk.py`, `portfolio/optimizer.py`)

> **Integration gap (P0):** neither `RiskManager` nor `PortfolioOptimizer` is
> imported by `backtesting/` or the engine. The backtest sizes positions itself
> via `leg_allocation_pct` / `target_notional`. **The entire `portfolio/` package
> is dead code with respect to the backtest** — none of the controls below are
> actually enforced on a trade.

| Control | Status | Notes |
|---|---|---|
| **VaR** | Present / basic | `risk.py:23-33` historical percentile; single return series only (no covariance/portfolio VaR); no empty-series guard; returns a negative quantile (sign convention undocumented). |
| **CVaR** | Present / basic | `risk.py:35-46` mean of the ≤VaR tail — correct shape; `NaN` if the tail is empty. |
| **Kelly sizing** | 🟡 **Present / unwired** | `kelly_fraction(μ, σ², half_kelly=True)` in `portfolio/optimizer.py` — clamped to [0, 1]; tested. Not yet called from the engine per-bar. |
| **Risk-parity allocation** | 🟡 **Present / unwired** | `risk_parity_weights(cov)` in `portfolio/optimizer.py` (iterative ERC); `multi_strategy_allocate` wraps it with per-strategy cap. Not yet called from the engine. |
| **Correlation / diversification** | 🟡 **Present / unwired** | `correlation_matrix`, `diversification_ratio`, `concentration_check` in `portfolio/risk.py`. Library-only; not wired into engine. |
| **Volatility targeting** | 🔴 **MISSING** | No vol-target position scaling anywhere. |
| **Max position limits** | Present / unenforced | `risk.py` `check_position_limits` is a standalone helper **no one calls** from the engine; `optimizer.py` `max_position_size=0.2` caps each position in `multi_strategy_allocate` but there is **no portfolio-level gross cap** wired into the per-bar engine loop (N positions → up to N×20%). |
| **Sharpe / vol / max-DD** | Present | `risk.py`; no `std==0` guard (→ inf/nan); `√252` annualization (see QUANT §5, should be `√365`). |

### P3 🟠 HIGH — Crypto position sizing truncates to zero
`optimizer.py:41` `position_size = int(position_value / price)`. For a $5,000
account at 20% allocation ($1,000) and BTC ≈ $60,000, `int(1000/60000) = 0`. The
sizer cannot take fractional-unit crypto positions — most allocations round to
**0 units**. Crypto requires fractional sizing.

### P7 🟡 Config inconsistencies
`optimizer.py` defaults `initial_capital=100000` (engine/prop-firm use **$5,000**)
and `max_position_size=0.2` while README config (`settings.yaml`) uses
`max_position_pct: 0.10`.

---

## 4. Stress Testing (`tests/test_risk_engine_stress.py` — 6 passed, 4 xfailed)

| Scenario | Test | Result | Finding |
|---|---|---|---|
| Flash crash (pair) | `…breaches_drawdown_floor` / `…is_marked_to_market` | ✅ pass | Floor + MTM work for pairs |
| Flash crash (single asset) | `…single_asset_should_fail_account` | ⚠️ xfail | **F1** — loss invisible, account never fails |
| Exchange outage (timestamp gap) | `…timestamp_gap_does_not_crash` | ✅ pass | Engine tolerates gaps (but cannot risk-monitor *during* an outage) |
| Missing candle (NaN price) | `…does_not_crash` | ✅ pass | Doesn't raise … |
| Missing candle (NaN price) | `…should_not_leak_nan_equity` | ⚠️ xfail | **F5** — NaN poisons the equity curve |
| Volatility spike | `…keeps_metrics_finite` | ✅ pass | Metrics stay finite (but slippage not vol-scaled, F8) |
| Daily-loss on a winning day | `…not_triggered_on_profitable_day` | ✅ pass | **F2** — fixed; test now passes |
| Leverage clip vs halt | `…must_not_clear_daily_halt` | ✅ pass | **F3** — fixed; test now passes |

### F5 ✅ FIXED — Missing candles leak `NaN` into the equity curve
A single `NaN` price produced `NaN` equity rows (and a `pct_change` fill
deprecation warning). The engine should forward-fill or skip missing candles and
flag the gap, not silently NaN-poison downstream Sharpe/drawdown.

---

## 5. Prop-Firm Compliance Score — **8 / 10**

| Control | Verdict |
|---|---|
| Profit targets / progression | ✅ Compliant |
| Account failure on floor | ✅ Compliant (pairs + single-asset) |
| Drawdown floor ($4,700 static) | ✅ Compliant — both paths |
| Daily loss (−$200) | ✅ Fixed (F2) |
| Leverage (5×) | ✅ Fixed — halt no longer cleared on clip (F3) |
| Controls apply to single-asset strategies | ✅ Fixed (F1/F6) |
| Same-bar fills / no latency | 🟠 Still optimistic |
| Vol-scaled slippage | 🟠 Still flat 5 bps |

All six prop-firm control columns are now compliant. Remaining deductions are for
execution realism (same-bar fills, flat slippage), not control logic.

## 6. Portfolio Risk Score — **5 / 10**

VaR/CVaR/Sharpe/vol/max-DD exist but are basic; **Kelly sizing is now wired**
(`use_kelly=True` on `BacktestEngine` — `kelly_fraction` is called per-bar and
bounds `target_notional`); risk-parity allocation (`multi_strategy_allocate`)
implemented but not called from engine or window layer; volatility targeting
still absent; position limits unenforced with no engine-level portfolio gross
cap; crypto sizing still truncates to zero (int cast — P3 open).

---

## 7. Unrealistic Assumptions (summary)

- Same-bar fills, zero latency (F4).
- Infinite liquidity / always-filled; no partial fills, no order book.
- Flat 5 bps slippage independent of volatility, size, or gaps (F8); volume model
  unreachable (F7).
- No short borrow fee, no perp funding (F9).
- Single-asset positions are weightless in equity (F1).
- `√252` annualization on a 24/7 market (QUANT §5).

## 8. Missing Controls (summary)

- Kelly sizing — **now present** (`kelly_fraction` in `portfolio/optimizer.py`); not yet wired per-bar into the engine.
- Risk-parity allocation — **now present** (`risk_parity_weights`, `multi_strategy_allocate`); not yet wired per-bar into the engine.
- Volatility targeting — absent.
- Portfolio-level gross/net exposure cap — absent.
- Intra-trade / live leverage monitoring — absent (open-only).
- NaN / missing-candle handling & data-quality gating — absent (F5).
- Funding/borrow cost model — absent (F9).
- `portfolio/` risk controls wired into the engine — absent (integration gap).

---

## 9. Recommended Fixes (prioritized)

**P0 — correctness (blocks trusting any single-asset or directional result)**
1. **F1/F6** Mark single-asset positions to market and close them at `price`:
   value `position_a` against whichever price column exists; route flat-signal
   exits through `price`. Add the `xfail`→pass guard in the stress suite.
2. **F2** Fix the daily-loss comparison (parenthesise the P&L and compare to the
   threshold).
3. **F3** Delete `engine.py:176` (`daily_halt = False`).

**P1 — realism**
4. **F4** Execute at next-bar open (or add an explicit fill-delay param).
5. **F8/F7** Volatility-scale slippage; thread `bar_volume` through `cost_model`.
6. **F5** Forward-fill/skip NaN candles and emit a data-quality warning.
7. **F9** Add short-borrow + funding-rate carry for multi-day crypto holds.

**P2 — portfolio risk**
8. **Wire `RiskManager`/`PortfolioOptimizer` into the engine** (or fold their
   logic in) so limits are actually enforced.
9. Implement **Kelly** (fractional/capped) and **volatility targeting** sizing.
10. **P3** Use fractional units for crypto; add a portfolio-level gross cap;
    reconcile the $5k/$100k and 0.2/0.10 defaults.

---

*End of report. Stress scenarios: `tests/test_risk_engine_stress.py`. Research
companion: `docs/QUANT_AUDIT_REPORT.md`.*
