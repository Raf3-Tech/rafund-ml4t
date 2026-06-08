# QUANT RESEARCH AUDIT REPORT — Raf3nd ML4T

**Auditor:** Quant Research Auditor
**Date:** 2026-06-04
**Scope:** `models/`, `features/`, `strategies/`, `tests/`, `docs/`
**Branch:** `feat/wire-validation-lifecycle`
**Mission:** Ensure every strategy, model, and feature is scientifically valid.

---

## 0. Executive Summary

The codebase has a **genuinely sound walk-forward skeleton** (chronological splitter, frozen-baseline rules strategy, sign-flip permutation test) but is undermined by **three confirmed data-leakage paths** and a **economically-inverted ML signal** that together make the headline "OOS Sharpe ≥ 1.0" gate scientifically unreliable.

The most important findings:

1. 🔴 **Full-sample look-ahead in the `hedge_ratio` feature** — computed once over the entire price history and broadcast to every row (`features/price_features.py:128-139`). It is then written to the DB and consumed as a model feature.
2. 🔴 **Train/validation window overlap** — the ML model is fit on the first 80% of *all* history (`models/train.py:176-180`) but the validator never refits per fold (`models/validator.py:42-43`, a no-op `prepare`), and pulls a *fixed 24-month* validation window (`models/validator.py:204`). With ~7 years of data the model's training set overlaps the validation window → "OOS" Sharpe is partly in-sample.
3. 🔴 **Look-ahead pair selection** — pairs are accepted/rejected by an ADF test run over the **full** spread series (`features/price_features.py:236`), i.e. selection uses the test period.
4. 🟠 **Economically inverted ML signal** — the model predicts the *level* of next-bar z-score and trades `sign(prediction)` (`models/validator.py:51-60`), which is backwards for mean reversion.
5. 🟠 **Significance testing is not in the promotion path** — `validate_for_promotion` checks only a point-estimate Sharpe/drawdown gate; the permutation/t-test (`backtesting/significance.py`) is wired only into the separate CLI `validate` flow.
6. 🟠 **Crypto annualized with `√252`** everywhere (24/7 markets need `√365`).

**Research Score: 48 / 100** (breakdown in §6).

---

## 1. Data Leakage Findings

### 1.1 🔴 CRITICAL — Full-sample `hedge_ratio` (look-ahead / improper scaling)
`features/price_features.py:128-139`
```python
covariance = np.cov(log_returns_a, log_returns_b)[0, 1]   # whole series
variance_b = np.var(log_returns_b)                         # whole series
hedge_ratio = covariance / variance_b ...
df['hedge_ratio'] = hedge_ratio                            # constant for ALL rows
```
The hedge ratio at row *t* embeds returns from *t+1 … T*. This column is persisted to the `features` table (`main.py:603,632,635`) and used:
- as a **model input feature** (`models/train.py:173`, `models/validator.py:58`),
- to size the model's hedge leg (`CandidateModelStrategy`, `models/validator.py:58-60`).

**Impact:** Every model that includes `hedge_ratio` has future information baked into both its features and its position sizing. Because the value is identical for all rows it also has **zero variance** → its reported feature importance is meaningless.

**Secondary defect:** this "hedge ratio" is `cov/var` of **log returns** (a returns-beta), whereas the trading strategy uses an OLS fit of **log price levels** (`strategies/stat_arb.py:279`). The feature and the strategy do not even use the same spread definition.

### 1.2 🔴 CRITICAL — Train/validation overlap (no enforced separation)
- `models/train.py:148-151` loads **all** feature history; `:176-180` fits on the first 80% chronologically.
- `models/validator.py:42-43` — `CandidateModelStrategy.prepare()` is `return None`, so inside the walk-forward loop (`backtesting/walk_forward.py:82-85`) the model is **never refit per fold**. The per-fold `train_df` is discarded; the *same statically-fit model* scores every test fold.
- `models/validator.py:192-206` loads a **fixed `NOW() - INTERVAL '24 months'`** validation window.

There is **no mechanism** guaranteeing the validation window post-dates the model's training data. With ~7 years of history, the model's training cut-off (~80% ≈ ~5.6 yr) lands *inside* the 24-month validation window, so roughly the oldest ~6–7 months of "OOS" folds were in the training set. The promoted `oos_sharpe` is therefore contaminated.

**This is the single most damaging issue**: the walk-forward harness *looks* rigorous, but for the ML path it degenerates into scoring a pre-fit model on partially-seen data.

### 1.3 🔴 HIGH — Look-ahead survivorship / selection bias in pair acceptance
`features/price_features.py:206-258` (`test_stationarity`) is run on the entire spread series and used (`main.py:554+`) to accept or reject a pair before trading. Selecting pairs that are stationary *over the whole sample* uses information from the test period — a classic selection look-ahead. Pairs that only became cointegrated late, or broke down late, are filtered using the future.

### 1.4 🟠 MEDIUM — Universe survivorship bias
The traded universe is hardcoded to current large-cap survivors (`config/loader.py:67-70`: BTC, ETH, SOL, BNB, ADA, DOT, LINK, XRP). Coins that delisted or collapsed (e.g. LUNA, FTT) are absent. Backtest returns are biased upward because only assets that survived to 2026 are considered.

### 1.5 🟠 MEDIUM — Target/feature coupling (not leakage, but mis-specification)
`models/train.py:168` sets `target = z_score.shift(-1)` while `z_score` is itself a feature (`:173`). The model is effectively an autoregression on a bounded mean-reverting series. This is *not* future leakage (target is strictly *t+1*), but combined with §2.1 it produces a signal that is statistically "accurate" yet economically inverted.

### 1.6 ✅ What is **clean**
- **No random train/test split anywhere.** All splits are strictly chronological (`models/train.py:176-180`, `backtesting/splitter.py`). The mission requirement *"reject random train/test split"* is satisfied.
- Rolling `spread_mean`/`spread_std` (`features/price_features.py:114-115`) are backward-looking (pandas `.rolling`), no look-ahead.
- `WalkForwardStatArb.prepare()` (`strategies/stat_arb.py:274-285`) correctly **refreezes** the hedge ratio and baseline on each fold's train set and applies them OOS — verified by `tests/test_stat_arb_wf.py::test_prepare_freezes_baseline_and_is_not_recomputed_on_test`.

| # | Finding | Severity | Location |
|---|---------|----------|----------|
|1.1| Full-sample hedge_ratio feature | 🔴 Critical | `features/price_features.py:128-139` |
|1.2| Train/validation overlap (no refit, fixed 24-mo window) | 🔴 Critical | `models/validator.py:42-43,204` + `models/train.py:148-180` |
|1.3| Full-sample ADF pair selection | 🔴 High | `features/price_features.py:236`, `main.py` |
|1.4| Universe survivorship bias | 🟠 Medium | `config/loader.py:67-70` |
|1.5| Autoregressive target on own feature | 🟠 Medium | `models/train.py:168,173` |

---

## 2. Strategy Weaknesses

### 2.1 🟠 Economically inverted ML signal
`models/validator.py:45-60`:
```python
predictions = self.model.predict(X)          # predicted next-bar z-score LEVEL
if pred > 0: side = 1                          # -> long A / short B
elif pred < 0: side = -1
```
For a mean-reverting spread, a predicted **high positive** z (spread rich) implies the spread will **fall** → you should **short** the spread (short A / long B). The code goes **long** A. The model trades the *level* of the forecast as if it were a momentum signal on a series it explicitly assumes mean-reverts. Either the position sign is wrong, or the target should be the next-bar **spread return / z-change**, not the z-level.

### 2.2 🟠 ML model and traded strategy use inconsistent spreads
Three different spread definitions coexist:
- Feature table: `spread = norm_a − norm_b` on prices normalized to their **first** value (`features/price_features.py:107-111`).
- `StatArbStrategy`: `log(A) − β·log(B)` (`strategies/stat_arb.py:58-60`).
- `WalkForwardStatArb`: OLS `β` on log levels per fold (`strategies/stat_arb.py:274-282`).

The model is trained on (1) but the rules backtest uses (3). Results are not comparable and the model's features don't describe the series it would trade.

### 2.3 🟠 `StatArbStrategy.generate_signals` never exits
`strategies/stat_arb.py:111-172`: entries are set on `|z| > entry_threshold` and then `ffill()`-ed forever (`:165`). There is **no exit rule** — `exit_threshold` is accepted but never used to set `signal = 0`. Positions are held to the end of the sample. (The walk-forward adapter `WalkForwardStatArb` *does* implement exits at `:303-304`, so this only affects the legacy single-pass path — but that path is still importable and tested.)

### 2.4 🟠 `FactorStrategy` is an untested stub
`strategies/factor_model.py` thresholds at `0.7 / 0.3` (`:56`) assume the composite score is bounded in [0,1], but `compute_composite_score` (`:32-54`) performs a weight-normalized sum with no such bounding. It has no `fit`/`prepare`, no walk-forward integration, and no significance testing. It is scientifically incomplete and must not be promoted.

### 2.5 🟡 Hard-coded thresholds, never optimized OOS
Entry/exit `2.0 / 0.5`, fixed window `60`, rolling window `20` are fixed constants. There is no nested/OOS hyper-parameter selection, so any in-sample tuning that produced these numbers is undocumented and untestable.

---

## 3. Walk-Forward Validation — Verification

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Chronological (no random split) | ✅ Pass | `backtesting/splitter.py:51-127`, `models/train.py:176-180` |
| Rolling window | ✅ Implemented | `TimeSeriesSplitter(train_months, test_months, step_months)`, non-overlapping test guard `splitter.py:81-82` |
| Expanding window | ❌ Not implemented | Splitter only supports **fixed-length rolling** train windows (`fold_start` advances; no expanding mode). The mission's "expanding window" option does not exist. |
| Out-of-sample testing | ⚠️ Partial | Genuine OOS for the **rules** strategy (per-fold refit). **Compromised** for the **ML** strategy (no per-fold refit + train/validation overlap, §1.2). |
| Reject random split | ✅ Pass | none present |

**Additional concerns:**
- **Single static fit across folds (ML path).** `walk_forward.py:82-85` calls `fit`/`prepare`, but `CandidateModelStrategy` implements neither meaningfully → the "walk-forward" is a multi-segment replay of one model.
- **Anchored vs rolling not configurable.** Only rolling is available; expanding/anchored walk-forward (often preferred for regime-spanning crypto) is absent.
- **Gate ignores fold dispersion.** `_evaluate_gate` (`walk_forward.py:156-165`) uses `mean_oos_sharpe` and `worst_oos_drawdown` only; `median_oos_sharpe`, `consistency_score`, and `pct_folds_profitable` are computed but **not gated on**.

---

## 4. Feature Audit (`features/`)

The model feature set is `['spread', 'spread_mean', 'spread_std', 'z_score', 'hedge_ratio']` (`models/train.py:173`).

| Feature | Economic rationale | Predictive rationale | Importance (qualitative) | Redundancy |
|---------|--------------------|--------------------|--------------------------|------------|
| `spread` | Distance from the cointegration equilibrium of a pair | Larger \|spread\| → larger expected reversion | Moderate (but defined on normalized prices, not the traded log-spread, §2.2) | Parent of all others |
| `spread_mean` | Rolling equilibrium estimate | Reference level for reversion | Low–moderate | Deterministic rolling transform of `spread` |
| `spread_std` | Local volatility of the spread | Scales reversion magnitude / risk | Low–moderate | Deterministic rolling transform of `spread` |
| `z_score` | Standardized deviation `(spread−mean)/std` | The canonical mean-reversion signal | High | **Exact function of the three above → perfect multicollinearity** |
| `hedge_ratio` | Cointegration leg ratio β | Should define the spread itself | **~Zero** (constant per pair) | Constant column; **leaks** (§1.1) |

**Key feature findings:**
- 🔴 **Severe redundancy / multicollinearity.** `z_score ≡ (spread − spread_mean)/spread_std`. Four of the five features are deterministic functions of a single underlying series. Effective dimensionality ≈ 1–2. For the `LinearRegression` default model (`train.py:185`) this is ill-conditioned; for RandomForest it inflates apparent importance spread arbitrarily.
- 🔴 **`hedge_ratio` is a constant per pair** → contributes no within-pair predictive signal and corrupts importance plots (`train.py:202-231`). It should be removed from the feature set or made time-varying *and* causal.
- 🟠 **No standardization for the linear path.** Features live on very different scales (raw spread ≈ O(0.01), z-score ≈ O(1), hedge ratio ≈ O(1)). RandomForest is scale-invariant, but the `LinearRegression` fallback (`train.py:185`) is not, and no scaler is fit (so at least there is *no scaler-leakage*).
- 🟠 **Two different `window` defaults.** Feature `z_score` uses `window=20` (`price_features.py:84`, `main.py:603`) while the strategy baseline uses `60` (`stat_arb.py:34`). The model's z-score and the strategy's z-score are not the same statistic.

**Feature-importance mechanics:** importance is logged per run as `tmp/<model>_feature_importance.png` and `tmp/<model>_features.json` (`train.py:202-231`) using `feature_importances_`/`|coef_|`. This is **in-sample** importance on a leaky, collinear feature set, so the numbers are not trustworthy until §1.1, §4 redundancy, and §1.2 are fixed.

---

## 5. Statistical Validity

| Requirement | Status | Evidence / Gap |
|-------------|--------|----------------|
| Sharpe significance | ⚠️ Partial | Sharpe computed (`significance.py:41`) but **no standard error / confidence interval** (no Lo 2002 SE, no Probabilistic/Deflated Sharpe). The promotion gate is a hard `≥ 1.0` point estimate (`validator.py:113`). |
| Bootstrap testing | ❌ Missing | No bootstrap of the Sharpe or returns. The permutation test is sign-flip, not a resampling bootstrap, and assumes IID. |
| Monte Carlo validation | ❌ Missing | No synthetic-path / randomized-entry Monte Carlo anywhere. |
| p-values | ⚠️ Partial | `ttest_1samp` p-value + sign-flip permutation p-value (`significance.py:39,60`), but only in the **CLI `validate`** flow, **not** in model promotion. |

**Strengths:**
- ✅ The sign-flip permutation test is **correctly implemented** (`significance.py:48-62`) and the previous degenerate order-shuffle was fixed — confirmed by `tests/test_validation.py::test_permutation_pvalue_is_not_degenerate`.
- ✅ Significance requires `p < α` **and** `perm_p < α` **and** `mean > 0` (`significance.py:64`) — appropriately conservative AND-gate.

**Weaknesses:**
- 🔴 **IID assumption violated.** Returns come from carried multi-day positions (`stat_arb.py:297-305`) → strong autocorrelation/overlap. Both the t-test and the sign-flip permutation assume independence, so both **overstate** significance. Need block bootstrap / Newey–West / stationary bootstrap.
- 🔴 **No multiple-testing correction.** The system sweeps many pairs × 2 model types against the same `Sharpe ≥ 1.0` gate (`retraining_scheduler.run_cycle`). With ~6–10 candidates, the probability of a false "significant" winner is high. No Bonferroni / FDR / Deflated Sharpe.
- 🟠 **Significance disconnected from promotion.** `ModelValidator.validate_for_promotion` never calls `test_strategy_significance`. A model can reach Production with a lucky point-estimate Sharpe and zero significance evidence.
- 🟠 **`√252` annualization on 24/7 crypto** (`significance.py:41,59`; `engine.py:415`; `engine_eval.py:335`; `train.py:193`; `risk.py:74-75,105`; `monitoring/metrics.py`). Daily crypto bars have ~365 periods/yr; using 252 mis-annualizes Sharpe by ≈ `√(252/365) ≈ 0.83×` and makes the `≥1.0` gate inconsistent with the data frequency.

---

## 6. Research Score

| Dimension | Weight | Score | Notes |
|-----------|-------:|------:|-------|
| Leakage control | 30% | 30/100 | Three confirmed leakage paths (§1.1–1.3); clean rules path partly offsets |
| Walk-forward rigor | 20% | 55/100 | Solid splitter; broken for ML path; no expanding window |
| Feature science | 15% | 35/100 | Redundant/collinear set, constant leaky feature, window mismatch |
| Statistical validity | 20% | 50/100 | Correct permutation test, but no bootstrap/MC, IID-violating, no MTC, off the promotion path |
| Strategy soundness | 15% | 45/100 | Inverted ML signal, inconsistent spreads, legacy no-exit path |
| **Weighted total** | 100% | **≈ 48/100** | |

**Interpretation:** *Promising research scaffolding, not yet scientifically trustworthy.* The rules-based stat-arb path (`WalkForwardStatArb`) is close to valid; the **ML training/validation/promotion path is not** and should not be trusted for capital allocation until §1.1, §1.2, and §2.1 are fixed.

---

## 7. Recommended Improvements (prioritized)

**P0 — Must fix before any result is believed**
1. **Make `hedge_ratio` causal (or drop it).** Compute β per walk-forward train fold only (as `WalkForwardStatArb.prepare` already does) and never broadcast a full-sample value into the feature table. Remove the constant `hedge_ratio` column from model inputs. *(§1.1)*
2. **Enforce train/validation disjointness.** Either (a) make `CandidateModelStrategy.prepare()` actually refit the model on each fold's `train_df`, or (b) pass the model's `train_end` into `_load_validation_data` and require `validation_start > train_end`. Add a test asserting no fold's `test_start ≤ model.train_end`. *(§1.2)*
3. **Fix the ML signal direction / target.** Predict next-bar **spread change / z-change** (or trade `−sign(z)` for level forecasts) and add a unit test that a synthetic mean-reverting series yields reversion (not momentum) positions. *(§2.1)*
4. **Select pairs causally.** Run the ADF/cointegration gate only on each fold's training window; never accept/reject using the full sample. *(§1.3)*

**P1 — Statistical hardening**
5. **Wire significance into promotion.** Call `test_strategy_significance` on pooled OOS fold returns inside `validate_for_promotion` and require significance in addition to the Sharpe/drawdown gate.
6. **Replace IID significance with a block/stationary bootstrap** (e.g. `arch.bootstrap.StationaryBootstrap`) and report a **Deflated Sharpe Ratio** that accounts for the number of trials swept. *(§5)*
7. **Add Monte Carlo validation** (randomized-entry / shuffled-block synthetic paths) to bound the null Sharpe distribution. *(§5)*
8. **Annualize with 365** for daily crypto (or make the factor a config) across `significance.py`, `engine.py`, `engine_eval.py`, `train.py`, `risk.py`, `monitoring/metrics.py`. *(§5)*

**P2 — Feature & strategy quality**
9. **De-duplicate features.** Keep `z_score` (+ maybe `spread_std` as a risk scaler); drop the deterministic duplicates, or add genuinely independent features (volume, funding rate, half-life of mean reversion, spread momentum). *(§4)*
10. **Add expanding/anchored walk-forward** as a splitter option and report median + dispersion in the gate. *(§3)*
11. **Address survivorship bias** by including delisted assets or explicitly documenting the survivor-only universe as a known limitation. *(§1.4)*
12. **Fix or quarantine `StatArbStrategy.generate_signals`** (no-exit bug, §2.3) and either complete or remove `FactorStrategy` (§2.4).

**P3 — Documentation integrity**
13. `docs/ANALYSIS_ROLLING_WINDOW_PROBLEM.md` references **non-existent** files (`backtesting/engine_v2.py`, `BacktestEngineV2`, `BUGFIXES_SUMMARY.md`, `dev/diagnostics/...`). Update it to point at the real `strategies/stat_arb.py` fixed-window implementation so reviewers can reproduce the fix.

---

## 8. Test-Coverage Gaps (scientific)

Existing tests are good on *mechanics* (`test_stat_arb_wf.py`, `test_validation.py`, `test_price_features.py`) but **no test asserts the absence of leakage**. Add:
- a test that the `hedge_ratio` feature for a given row is invariant to appending future data;
- a test that `validate_for_promotion` rejects any configuration where the validation window overlaps the model's training window;
- a test that the ML signal is short-the-spread when forecasted z is high (reversion, not momentum);
- a significance test on autocorrelated returns showing the block bootstrap widens p-values vs the IID version.

---

*End of report.*
</content>
</invoke>
