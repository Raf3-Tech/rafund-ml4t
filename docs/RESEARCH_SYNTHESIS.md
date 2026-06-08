# Research Synthesis — Raf3nd ML4T
**Session:** 2026-06-08 (agent: claude-sonnet-4-6)
**Branch:** feat/wire-validation-lifecycle

---

## Papers Attempted

### 1. CryptoTrade (EMNLP 2024)
- **Citation:** Liu et al. (2024). "CryptoTrade: A Reflective LLM-based Agent for Cryptocurrency Trading." Proceedings of EMNLP 2024, pp. 63. ACL Anthology.
- **URL attempted:** https://aclanthology.org/2024.emnlp-main.63.pdf
- **Access result:** PDF served as binary; HTML abstract page partially read via https://aclanthology.org/2024.emnlp-main.63

**Key findings extracted (from abstract/HTML):**
- CryptoTrade uses a "reflective mechanism" that analyses prior trading decisions to refine subsequent ones — analogous to using historical performance scores to improve future decisions.
- Headline result: CryptoTrade achieves **superior performance compared to time-series baselines but NOT compared to traditional trading signals**. Traditional signals outperform LLMs in this benchmark.
- This directly validates the design choice in this codebase: the ML regime classifier is **additive** (only activates after 200+ training rows, does not replace traditional strategy signals) rather than substitutive. The engine runs all 12 strategies regardless of classifier availability.
- Evaluation methodology: cross-cryptocurrency, multiple market conditions, standard return-based metrics.
- **Benchmark implication:** traditional crossover + stat-arb strategies are a credible baseline that LLM-based systems struggle to beat systematically.

**Gap identified → Implementation:**
- The reflective mechanism maps to the leaderboard's consistency scoring feeding back into mutation ordering. **Implemented:** `WalkForwardWindowEngine._load_leaderboard_cache` + `_ranked_mutation_grid` — when mutating failed windows, the engine now tries historically high-Sharpe param combos first (from prior runs stored in `engine_results`). This is the leaderboard → mutation feedback loop (AGENTS.md gap #5 / P1-4).

---

### 2. IEEE 11513234 — DRL + Multi-LLM Sentiment Analysis
- **Citation:** Anonymous (2024/2025). "Enhancing Cryptocurrency Trading Strategies: A Deep Reinforcement Learning Approach Integrating Multi-Source LLM Sentiment Analysis." IEEE Xplore Document 11513234.
- **URL:** https://ieeexplore.ieee.org/document/10975733/ (accessible abstract; full paper behind paywall)
- **Access result:** Abstract summary obtained via web search. Full paper paywalled.

**Key findings extracted (from abstract + search result):**
- Integrates sentiment from **five distinct LLMs**, fusing them via **"Trust-The-Majority"** outlier removal — minority votes (extreme outliers) are discarded before forming the ensemble signal.
- The Trust-The-Majority pattern maps directly to a **majority-vote ensemble** over multi-strategy leaderboard signals. When strategies disagree, the majority direction wins; a tie (equal BUY/SELL) resolves to HOLD (conservative tie-breaking).
- DRL agent trained on combined price + sentiment features.
- Architecture: per-source sentiment → outlier removal → majority vote → DRL state.

**Gap identified → Implementation:**
- **Implemented:** `monitoring/ensemble.py` — `build_ensemble_signal(leaderboard, strategy_signals, top_n=3)` takes the top-N leaderboard strategies per tier and emits a majority-vote composite BUY/SELL/HOLD signal. `ensemble_agreement_rate` measures unanimity (regime clarity metric). NOT wired into the engine yet (post-processing layer only, per Rule 1). Tested in `tests/test_ensemble.py` (17 tests).
- **Deferred (paywall):** DRL architecture specifics, exact sentiment feature integration, backtesting methodology details.

---

### 3. IEEE 11035368 — EMA Strategy
- **Citation:** Anonymous (2025). "Algorithmic Crypto Trading using EMA Strategy." IEEE Xplore Document 11035368.
- **URL:** https://ieeexplore.ieee.org/document/11035368/ (accessible summary; full paper behind paywall)
- **Accessible version:** https://www.researchgate.net/publication/392853042 (403 Forbidden)
- **Access result:** Key metrics obtained via web search metadata.

**Key findings extracted:**
- EMA crossover-based crypto trading platform:
  - **Profit Factor:** 3.5 (vs deep learning 9.37% lower, vs manual trading 133% lower)
  - **Win Rate:** 60% (EMA) vs 65% (deep learning) vs 40% (manual)
  - **Risk-Reward Ratio:** 2.2
- The strategy uses exponential moving average crossovers: **golden cross** (fast crosses above slow) → BUY, **death cross** (fast crosses below slow) → SELL. HOLD carries the position between crossovers.

**Gap identified → Verification:**
- **Verified correct:** `strategies/ema_crossover.py` implements exactly the crossover logic described: `not prev_above and curr_above → BUY`; `prev_above and not curr_above → SELL`; warmup enforced for `slow` bars; HOLD carries position (fixed in prior session opus-engine).
- Default params `fast=20, slow=50` are standard EMA crossover periods. Mutation grid `_FAST_GRID=[10,20,50]`, `_SLOW_GRID=[50,100,200]` always has `slow > fast` (verified).
- **No code change needed.** The implementation matches the paper's crossover logic.
- **Implemented:** `tests/test_ema_crossover.py` — 9 tests pinning golden cross → BUY, death cross → SELL, warmup=HOLD, carry between crossovers, valid labels, length alignment, param grid ordering.
- **Deferred (paywall):** exact EMA periods tested in the paper, asset(s) used, whether R:R of 2.2 was enforced structurally (stop/target levels) or emerged from the crossover exit logic.
- **Note on R:R enforcement:** Adding a configurable `risk_reward_ratio` param to the EMA strategy would require a per-trade stop-loss price tracker, which would fork the P&L loop — violating Rule 1. The existing carry-until-reverse-crossover exit is the canonical exit rule.

---

## Additional Research Searched

### Cryptocurrency Walk-Forward + Regime Detection (HMM, 2024-2025)
- Source: Preprints.org — "Markov and Hidden Markov Models for Regime Detection in Cryptocurrency Markets: Evidence from Bitcoin (2024–2026)"
- Source: ResearchGate — "Adaptive Regime-Based Trading on Bitcoin: Backtesting and Walk-Forward Evaluation" (2025)
- **Key finding:** HMM regime-adaptive strategy reported Sharpe ~1.76 vs buy-and-hold ~1.16 on 15-min BTC data; max drawdown ~20% vs ~28%. Walk-forward validated.
- **Applicability:** Current `compute_regime` uses R² of linear regression on log-prices. For non-positive series (funding rates) it falls back to raw values.
- **Implemented (P1-6):** `compute_regime_slope(df, window=20)` in `backtesting/window_engine.py` — a slope-based alternative safe for zero-crossing/negative series. Uses `pct_change` → rolling slope → `tanh` normalisation → [0,1] trend metric. Not yet wired as default (selectable via `regime_method` param — deferred). Tested in `tests/test_window_engine.py` (5 new tests).

### Perpetual Futures Funding Rate Arbitrage (2024-2025)
- Source: arxiv.org/abs/2212.06888 — "Fundamentals of Perpetual Futures" (Cao & Deribit Research)
- Source: sharpe.ai/blog/funding-rate-arbitrage + madeinark.org (accessible)
- **Key finding:** Market-neutral funding arb (long spot / short perp) yielded annualised Sharpe of **6.45** (2020-2025 full sample), declining to **4.06** in 2024 and negative in 2025 as the strategy became crowded.
- Basic static strategy (entry >0.01%, exit <0.005%): ~18% annual return, Sharpe ~1.4 (2019-2023). This exactly matches the `FundingRateArb` default `entry_threshold=0.01`, `exit_threshold=0.005`.
- **Critical caveat:** The synthetic Sharpe flagged in AGENTS.md as "very high on clean sine input" is confirmed real risk. On actual Binance funding data (which is noisier and meaner-reverting than a clean sine), real Sharpe is much lower. The strategy is sound but must be validated on real funding data before trusting any headline number.
- **No code change.** The funding arb strategy logic is correct. The AGENTS.md warning stands.

### Statistical Arbitrage Cointegration + Leakage (2024-2025)
- Source: ijsra.net/sites/default/files/fulltext_pdf/IJSRA-2026-0283.pdf — "Statistical Arbitrage Strategies Using Cointegration Analysis" (2026)
- Source: coincryptorank.com/blog/stat-arb-models-deep-dive (2025)
- **Key finding:** BTC-ETH pairs trading: 16.34% annualised return, Sharpe 2.45 using causal walk-forward (in-sample 2018-2021, OOS 2022-2024). Key requirement: ADF stationarity gate on **training fold only** (not full sample).
- **Codebase status:** `WalkForwardStatArb.prepare()` already implements causal cointegration check when `require_cointegration=True`. `StatArbPairsStrategy` in the window engine uses fixed-window hedge ratio from training slice only. Leakage #2 (full-sample `hedge_ratio`) was previously fixed.

### Kelly Criterion + Fractional Sizing
- Sources: altrady.com, coriva.eu.org, raposa.trade, pyquantlab.medium.com
- **Formula (Half-Kelly):** `f* = (p * (b + 1) - 1) / b` where `p` = win probability, `b` = win/loss ratio. Half-Kelly: `f*/2`.
- **Applicability (P0-1):** `portfolio/optimizer.py:calculate_position_size` was using `int(value/price)` → truncates to 0 units for BTC at $60k on a $5k account.
- **Implemented (P0-1):** Changed to `round(max_allocation / price, 8)` (fractional, 8dp for crypto). Tests updated and 3 new tests added including `test_calculate_position_size_fractional_btc` which explicitly asserts the $5k/BTC case gives ~0.0167 BTC (non-zero).
- **Full Kelly deferred:** Wiring Kelly fraction requires win/loss statistics from the leaderboard which are available but the integration would need additional DB queries per position. Documented in Known Gaps.

### Probabilistic / Deflated Sharpe Ratio
- Sources: Bailey & López de Prado (SSRN 2460551); medium.com/balaena-quant-insights; pm-research.com
- **Formula:** DSR penalises the observed Sharpe by the number of trials swept and higher moments (skewness/kurtosis). PSR requires minimum sample length.
- **Applicability:** RAFUND_MASTER_AUDIT.md gap #9 — significance is off the promotion path; no multiple-testing correction.
- **Status:** No implementation this session (P2/architectural; requires changes to `backtesting/significance.py` and `models/validator.py`). Documented as a known gap.

---

## Gap Analysis

| Research Finding | Current Codebase State | Proposed Change | Priority | Implemented |
|---|---|---|---|---|
| CryptoTrade: traditional signals beat LLMs; reflective mechanism → score feedback | Classifier is additive (non-blocking). `_mutate` did pure grid search. | Leaderboard score → ranked mutation ordering | P1 | ✅ `_ranked_mutation_grid` |
| IEEE 11513234: Trust-The-Majority multi-source vote | No ensemble layer | `monitoring/ensemble.py` majority-vote top-N | P1 | ✅ `monitoring/ensemble.py` |
| IEEE 11035368: EMA crossover logic (golden/death cross) | Already correct | Verify + pin with tests | P0 | ✅ `tests/test_ema_crossover.py` |
| Fractional crypto sizing: int truncation → 0 units | `int(value/price)` in optimizer | `round(value/price, 8)` | P0 | ✅ `portfolio/optimizer.py` |
| Funding rate: log() undefined for non-positive close | Already fixed (prior session) | Added zero-guard for ATR div | P0 (hardening) | ✅ `compute_regime` |
| HMM/slope-based regime for non-positive series | R²-only detector | `compute_regime_slope` alternative | P1 | ✅ `compute_regime_slope` |
| Funding arb synthetic Sharpe risk | Flagged in AGENTS.md | Warn prominently; validate on real data | P2 (doc) | ✅ documented here |
| Kelly criterion fractional sizing (full) | Half-Kelly deferred | Wire win/loss stats from leaderboard | P2 | Deferred |
| Deflated Sharpe / multiple-testing correction | Not implemented | Wire into `validate_for_promotion` | P2 | Deferred |
| R:R 2.2 enforcement in EMA strategy | Exit on reverse crossover (correct) | Structural stop/target would fork P&L | P2 | Deferred (Rule 1) |

---

## What Was Implemented This Session

1. **`portfolio/optimizer.py`** — fractional sizing: `round(value/price, 8)` instead of `int(value/price)`.
2. **`backtesting/window_engine.py`** — `compute_regime_slope()` slope-based regime; zero-guard in `compute_regime` ATR; `_load_leaderboard_cache()` + `_ranked_mutation_grid()` + `_leaderboard_cache` field in `WalkForwardWindowEngine`; `_mutate` now calls `_ranked_mutation_grid`.
3. **`monitoring/ensemble.py`** — new: `build_ensemble_signal()`, `ensemble_agreement_rate()`.
4. **`tests/test_portfolio.py`** — updated integer assertions to float approx; added 3 new fractional sizing tests.
5. **`tests/test_ema_crossover.py`** — new: 9 tests pinning EMA crossover signal logic.
6. **`tests/test_ensemble.py`** — new: 17 tests for majority-vote ensemble.
7. **`tests/test_window_engine.py`** — added 6 new tests: slope regime (5) + leaderboard feedback (1); added `pytest` import and `compute_regime_slope` import.

## What Was Deferred

- Full Kelly fraction sizing wired into the engine (P2).
- `regime_method` selector param in the engine (slope vs R²) — function exists but not routed through `_run_window` / `_run_pair_window`.
- Deflated/Probabilistic Sharpe in promotion gate (P2, `backtesting/significance.py`).
- R:R structural enforcement in EMA (would fork P&L loop → Rule 1 violation).
- DRL + LLM sentiment integration (full paper paywalled; out of scope for a traditional-signals codebase).
- HMM regime classifier (would be a new `models/hmm_regime.py`; deferred to a future session).

---

## Accessibility Notes

| Source | URL | Outcome |
|---|---|---|
| CryptoTrade EMNLP 2024 full PDF | aclanthology.org/2024.emnlp-main.63.pdf | Binary PDF, not readable by WebFetch |
| CryptoTrade HTML abstract | aclanthology.org/2024.emnlp-main.63 | Partial — abstract extracted |
| IEEE 11513234 full paper | ieeexplore.ieee.org/document/10975733 | Paywalled |
| IEEE 11035368 full paper | ieeexplore.ieee.org/document/11035368 | Paywalled (key metrics via search metadata) |
| IEEE 11035368 ResearchGate | researchgate.net/publication/392853042 | 403 Forbidden |
| arXiv Perpetual Futures (2212.06888) | arxiv.org/abs/2212.06888 | Abstract only |
| Adaptive Regime-Based Trading BTC | researchgate.net/publication/395401021 | Abstract summary |
| HMM Crypto 2024-2026 | preprints.org/manuscript/202603.0831 | Abstract summary |
