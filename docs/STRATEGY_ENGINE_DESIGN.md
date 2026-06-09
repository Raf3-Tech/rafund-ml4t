# Per-Asset Strategy-Generation Engine — Evidence Base & Design

**Author:** Backtesting & Risk Engineer
**Date:** 2026-06-05
**Goal:** Let the system *generate its own strategy per asset* by price-predicting,
gated behind *robust* backtesting, with prop-firm restrictions and all known
trading-research biases controlled for.

---

## 0. Sources & honest scope of reading

Both requested papers are paywalled to full text; here is exactly what I read:

1. **Springer ch. 4** — *Fang, Ventre, Basios, Kanthan, Martinez-Rego, Wu, Li,
   "Cryptocurrency Trading: A Comprehensive Survey"* (in *Blockchain, Crypto
   Assets, and Financial Innovation*, Springer 2025; also *Financial Innovation*
   2022). **I read the full open-access text** via the arXiv mirror
   (arXiv:2003.11352). It surveys 146 papers.
2. **IEEE 10568131** — *Otabek & Choi, "From Prediction to Profit: A
   Comprehensive Review of Cryptocurrency Trading Strategies and Price
   Forecasting Techniques," IEEE Access, 2024.* IEEE Xplore and ResearchGate
   blocked automated full-text retrieval (HTTP 418/403); **I worked from the
   abstract, indexed metadata, and secondary summaries**, not the full PDF.

**I did not read the 146+ individually-referenced papers** — that is infeasible
in this setting. What follows synthesizes the *methodological consensus* of these
two reviews, mapped onto our codebase. Treat per-paper claims as the reviews'
reported findings, not independently verified.

---

## 1. What the two reviews actually teach

### 1.1 Fang et al. survey — taxonomy & findings
- **Trading-system taxonomy:** infrastructure (real-time, arbitrage), systematic
  (technical analysis, **pairs/statistical arbitrage**, informed trading),
  emergent tech (**econometrics, ML, reinforcement learning**), portfolio &
  co-movement construction, and bubble/extreme-condition analysis.
- **Models for prediction:** SVM (best ~62% binomial BTC accuracy but *"very
  prone to overfitting"*); Random Forest (works but *"performance proportional to
  amount of data"*, multicollinearity issues); **LSTM/GRU best for volatile
  series** (GRU often best MSE/R²; LSTM beat gradient boosting ~7% at 10-min
  horizon *with enough history*); CNN-LSTM stacks; seq2seq beats ARIMA but *"very
  poor in extreme cases"*. Crucially: **ANN sometimes beats LSTM in practice**
  despite theory — i.e. model choice is empirical, per-asset, per-horizon.
- **Features:** technical (EMA/MA, RSI/ROC/MACD, ATR, Ichimoku), volume, plus
  **economic, social (Google Trends / Twitter sentiment), and seasonal** groups;
  formulaic-alpha factors ("101 Alphas").
- **Evaluation:** return (annualized excess return, net profit), risk (**Sharpe,
  VaR, max drawdown**), forecast error (MAE/MSE/RMSE/R²), classification
  (precision/recall/F1); **rolling-window out-of-sample**, benchmarked vs
  buy-and-hold.
- **Risk/portfolio:** diversification & mean-variance optimization, MSGARCH VaR,
  **volatility-scaled position sizing** (turtle), market-neutral hedging,
  stop-losses, conservative exposure.

### 1.2 Otabek & Choi — "prediction → profit"
- Reviews econometric/statistical, ML, deep-learning, and **sentiment** forecasting
  for BTC/ETH/LTC; reports up to **~83% directional accuracy** for BTC/ETH, and
  that **DL + sentiment outperform conventional strategies *especially during
  volatility***.
- **The central, load-bearing lesson for us:** the paper exists *because there is
  a gap between forecast accuracy and trading profit.* A high-accuracy predictor
  is **necessary but not sufficient**; profit depends on how the forecast is
  turned into positions, **net of costs, under realistic execution and risk
  limits.** Optimizing RMSE/accuracy is *not* the objective — net risk-adjusted
  P&L is.

### 1.3 The one-sentence synthesis driving this design
> **Predict per-asset, but select and promote strategies on cost-aware,
> leakage-free, multiple-testing-corrected out-of-sample P&L under prop-firm
> limits — never on forecast accuracy or a single backtest Sharpe.**

---

## 2. Bias & pitfall control checklist (the core requirement)

Auto-generating a strategy *per asset* means searching over many models, features,
horizons and thresholds → **massive multiple testing**. This *amplifies* every
bias below. Each must be a hard, tested control, not a guideline.

| # | Bias / pitfall | Why it bites here | Control to implement | Where |
|---|---|---|---|---|
| B1 | **Look-ahead / data leakage** | Features or labels using future info inflate OOS | Causal features only; **next-bar execution**; fit transforms per fold | `features/`, `engine.py`, WF loop |
| B2 | **Train/test contamination** | Overlapping windows; serial correlation bleeds across split | **Purged + embargoed walk-forward** (López de Prado): drop train samples whose label horizon overlaps test; embargo a gap after each test fold | `backtesting/splitter.py` |
| B3 | **Data-snooping / multiple testing** | Searching N assets × M configs guarantees a lucky winner | **Deflated Sharpe Ratio** & **White's Reality Check / SPA**; track *number of trials*; Bonferroni/BH-FDR on promotion p-values | new `backtesting/selection.py` |
| B4 | **Backtest overfitting** | Best in-sample config ≠ best OOS | **PBO (Probability of Backtest Overfitting)** via CSCV; nested CV; complexity penalty | new selection module |
| B5 | **Survivorship bias** | Universe is current survivors (BTC/ETH/SOL/…); dead coins excluded | Include delisted assets *or* document the survivor-only universe as a known limitation; never select pairs/assets using the full sample | `config/loader.py`, data layer |
| B6 | **Non-stationarity / regime change** | Crypto has jumps & structural breaks | Rolling refit per fold; regime tagging (vol state); shorter, more frequent retrains; report fold dispersion not just mean | WF runner, scheduler |
| B7 | **Transaction-cost / slippage / impact omission** | "Profitable" strategies die on costs | Realistic, **volatility-scaled** slippage + commission on every fill; charge funding/borrow on multi-day holds | `backtesting/costs.py` (see RISK audit F7/F8/F9) |
| B8 | **Label/target mis-specification** | Predicting a level and trading its sign can invert the edge | Predict **next-bar return / direction / vol-scaled return**, not a bounded level; unit-test sign economics | `models/` |
| B9 | **Selection look-ahead in pair/asset choice** | ADF/cointegration on full sample chooses with the future | Run all selection gates on the **train fold only** | `features/price_features.py` |
| B10 | **Metric gaming (accuracy≠profit)** | High R²/accuracy, negative net P&L | **Objective = OOS net risk-adjusted P&L under prop-firm limits**, accuracy is diagnostic only | promotion gate |
| B11 | **Sharpe annualization error** | 24/7 crypto annualized with √252 | Use **√365** consistently (see `docs/QUANT_AUDIT_REPORT.md` §5) | global constant |

---

## 3. Target & model design ("price predicting")

- **Per asset, per horizon:** target = next-bar **log-return** (regression) or its
  **sign** (classification) or a **volatility-normalized return**. *Avoid*
  predicting a bounded mean-reverting *level* and trading its sign (the current ML
  path does this and the QUANT audit flags it as economically inverted).
- **Model family is an empirical, per-asset choice** (survey: ANN sometimes beats
  LSTM): candidate set = {regularized linear/logit, gradient boosting, random
  forest, LSTM/GRU} selected by purged-OOS score, **not** a fixed model.
- **Features (causal only):** returns & momentum (RSI, MACD, ROC), volatility
  (ATR, realized vol), volume features, and optional exogenous (sentiment/Google
  Trends, funding rate) — each computed strictly from past data. De-duplicate
  collinear features (QUANT audit §4).

---

## 4. Auto-strategy-generation architecture (per asset)

```
for each asset:
  ┌─────────────────────────────────────────────────────────────┐
  │ 1. Causal feature build (no full-sample stats)               │  features/
  │ 2. Candidate space: {model} × {horizon} × {feature set}      │  new: strategy_search
  │       × {signal threshold} × {sizing rule}                   │
  │ 3. Purged+embargoed walk-forward over each candidate         │  backtesting/ (B2)
  │ 4. Score = OOS net risk-adjusted P&L under prop-firm sim     │  engine.py (B7,B10)
  │ 5. Multiple-testing correction across all candidates:        │  new: selection.py
  │       Deflated Sharpe + PBO; record #trials                  │  (B3,B4)
  │ 6. Promote ONLY if: DSR>0 sig., PBO<0.5, never fails account,│  models/validator.py
  │       beats buy-and-hold net of cost, dispersion acceptable  │
  └─────────────────────────────────────────────────────────────┘
  → registered per-asset strategy (or "no tradeable edge found")
```

"Generates its own strategies" = this search loop choosing, per asset, the
configuration that survives the leakage-free, cost-aware, overfitting-penalized
gate — and explicitly returning **"no edge"** when nothing clears it (a correct
and frequent outcome the reviews imply).

---

## 5. Prop-firm integration ($5k / 4% daily / 6% DD / 5×)

- **Sizing:** volatility targeting (survey-endorsed) with a **Kelly-capped**
  fraction, then clamped so the backtested worst-case daily loss and drawdown stay
  inside 4% / 6%. `kelly_fraction` is now implemented in `portfolio/optimizer.py`
  (half-Kelly, clamped to [0,1]); it is not yet wired per-bar into the engine (Phase E
  in AGENTS.md). Volatility targeting is still missing (RISK audit P-vol).
- **Hard reject** any generated strategy whose walk-forward backtest ever trips
  `account_failed`, or whose worst-fold drawdown breaches 6%.
- **Promotion gate** extends the existing `ModelValidator.validate_for_promotion`
  with DSR/PBO + significance + cost-aware net P&L, not just `Sharpe ≥ 1.0`.

---

## 6. Robust backtesting protocol (the gate that makes the rest trustworthy)

1. **Purged + embargoed** walk-forward (B2).
2. **Realistic execution**: next-bar fills, vol-scaled slippage, commission, and
   funding (B1/B7).
3. **Cost-aware objective**: net Sharpe/Sortino + max drawdown + prop-firm pass.
4. **Overfitting controls**: Deflated Sharpe Ratio, PBO via CSCV, fold dispersion.
5. **Benchmark**: must beat buy-and-hold net of costs.
6. **Significance**: block/stationary **bootstrap** (returns are autocorrelated;
   IID t-test/sign-flip overstate significance — QUANT audit §5).

---

## 7. ⚠️ Hard prerequisites — why this is *not yet buildable on the current code*

"Robust backtesting" is currently **impossible**; two existing audits document why,
and per-asset price prediction hits the *worst* of the bugs:

- **`RISK_ENGINE_AUDIT.md` F1 — single-asset positions are invisible to equity.**
  Per-asset predictive strategies are *single-asset* strategies → their P&L,
  Sharpe, drawdown and prop-firm checks are all meaningless today. **This alone
  blocks the entire feature.**
- **F2/F3** — daily-loss control broken; leverage clip clears the halt → prop-firm
  gating cannot be trusted.
- **F4/F7/F8/F9** — look-ahead fills, non-vol-scaled / unreachable slippage, no
  funding → costs understated (violates B1/B7/B10).
- **`docs/QUANT_AUDIT_REPORT.md` §1** — full-sample `hedge_ratio`, train/validation
  overlap, full-sample ADF pair selection, inverted ML signal → the ML harness
  leaks (violates B1/B8/B9), and the splitter has **no purge/embargo** (B2).

**Building auto-strategy generation now would mass-produce confident, overfit,
leaked, cost-blind strategies** — the exact failure mode both reviews warn about.

### Required ordering
- **P0 (correctness):** RISK F1/F2/F3 (engine), QUANT §1.1/1.2 (leakage), add
  purge+embargo to the splitter.
- **P1 (realism):** RISK F4/F7/F8/F9 (execution & costs), √365, vol-scaled slippage.
- **P2 (selection):** Deflated Sharpe + PBO + bootstrap significance module.
- **P3 (engine):** per-asset return-prediction models + the search loop + Kelly /
  vol-target sizing + extended promotion gate.

---

## 8. Phased build plan

| Phase | Deliverable | Depends on |
|---|---|---|
| 0 | Fix engine + leakage (P0 above); make single-asset backtests valid | RISK/QUANT audits |
| 1 | Purged/embargoed WF splitter + realistic costs (P1) | Phase 0 |
| 2 | `backtesting/selection.py`: DSR, PBO/CSCV, block bootstrap | Phase 1 |
| 3 | Per-asset return-prediction models + causal feature sets | Phase 1 |
| 4 | Strategy-search loop + Kelly/vol-target sizing + extended gate | Phases 2–3 |
| 5 | Wire into retraining scheduler & dashboard; paper-trade | Phase 4 |

---

*Companion docs: `RISK_ENGINE_AUDIT.md` (execution/risk), `docs/QUANT_AUDIT_REPORT.md`
(research validity). Sources: Fang et al. (arXiv:2003.11352); Otabek & Choi, IEEE
Access 2024 (doi:10.1109/ACCESS.2024.<10568131>).*
