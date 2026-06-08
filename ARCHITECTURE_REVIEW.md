# Raf3nd ML4T — Architecture & Production-Readiness Review

**Date:** 2026-06-05
**Scope:** entire repository (~10,061 LOC across 12 packages, 17→25 test files)
**Method:** static review + measured test coverage + live DB/schema inspection. Findings are cited as `file.py:line`.

> **Working-tree caveat (read first).** This review was produced against a working tree with **many uncommitted changes** (`git status` shows ~16 modified source files + several untracked modules, audit docs, and tests). Some findings below may already be partially addressed in those uncommitted edits — **verify against the current diff before acting**. Nothing here is committed. A `.coverage` artifact and `tmp/` were generated during measurement and should be gitignored/removed.

---

## Production Readiness Score: **4.5 / 10** — *Early-stage; not production-ready*

| Dimension | Score | One-line rationale |
|---|---:|---|
| Architecture / modularity | 5/10 | Clean package boundaries undermined by a 1,157-LOC `main.py` god-entrypoint and 5-site SQL duplication. |
| Database layer | 4/10 | Thread-**unsafe** pool used under a threaded scheduler; connection leaks on error paths; errors swallowed as empty results. |
| Test coverage | 5/10 | Strong offline unit suite (171 pass) but **~46%** line coverage and **zero true end-to-end** tests. |
| CI/CD | 3/10 → improving | None existed; a working pipeline is now scaffolded (`.github/workflows/ci.yml`). |
| Observability / logging | 6/10 | Centralized structlog config, but split with stdlib `logging` and a logger-override bug silently de-structures all DB logs. |
| Error handling / resilience | 4/10 | Excellent retry in the Binance collector; absent everywhere else; DB layer hides failures from callers. |
| Security / secrets | 4/10 | Good `.env` hygiene, but hardcoded `'postgres'` password defaults, an unimplemented `${DB_PASSWORD}` placeholder, and unquoted f-string SQL. |
| Config management | 4/10 | Three competing config paths (`Settings`, raw YAML, scattered `os.getenv`) with diverging defaults. |

**Gate to "production-ready" (≈7/10):** fix the thread-unsafe pool + connection leaks, stop swallowing DB errors, remove credential fallbacks, raise coverage past 80% with at least one real e2e test, and make the CI lint/coverage gates blocking.

---

## 1. Technical Debt

### Critical
| # | Finding | Evidence | Fix |
|---|---|---|---|
| C1 | **`SimpleConnectionPool` is not thread-safe**, but it is shared across `BackgroundScheduler` worker threads (retrain / drift / health jobs run concurrently). Concurrent `getconn`/`putconn` can corrupt the free-list and hand one connection to two threads. | `data/db.py:108`; `models/retraining_scheduler.py:43,177` | Switch to `psycopg2.pool.ThreadedConnectionPool` (drop-in API); raise `max_conn` above the count of concurrent jobs. |
| C2 | **Connection leaks on error paths.** No `DatabaseConnection` method returns its connection in a `finally:`. The 4 `delete_*_for_*` helpers and `get_table_counts` return on error **without** `return_connection`; insert paths gate the return behind a `rollback()` that can itself raise. With a 5-slot pool, ~5 errors exhaust it → deadlock. | `data/db.py:673-743`, `:745-762`, `:203-208`, `:387-392`, `:445-451` | Wrap every checkout in `try/finally: return_connection(conn)`. |

### High
| # | Finding | Evidence | Fix |
|---|---|---|---|
| H1 | **DB errors are swallowed and returned as empty/None/0/False** — a broken DB is indistinguishable from "no data," so the pipeline silently trains/backtests on empty inputs. | `data/db.py:255,286,311,332,493,762,794,807,824` (reads); insert paths `:209,393,452,559,620,933` | Re-raise a typed `DBError` after logging; reserve empty returns for genuinely empty results. |
| H2 | **`data/db.py` logger override** — line 22 binds structlog's `get_logger`; line 38 overwrites it with stdlib `logging.getLogger`, so ~50 DB log calls bypass structured/JSON logging entirely. | `data/db.py:22` then `:38` | Delete line 38. |
| H3 | **`main.py` is a 1,157-LOC god-entrypoint** holding all business logic (collect/backfill/backtest/validate/retrain/drift/features/signals/pipeline/paper/live). ~10 command functions repeat an 8-line open-DB / test / close-pool preamble. | `main.py:60-1040` | Extract commands into their owning packages behind a `with db_session() as db:` context manager; `main.py` → argparse dispatch (~150 LOC). |
| H4 | **`${DB_PASSWORD}` interpolation is not implemented.** `settings.yaml` declares `password: ${DB_PASSWORD}` but the loader does a plain `yaml.safe_load` with no `${}` expansion — an unset env var yields the literal string `"${DB_PASSWORD}"`. | `config/settings.yaml:8` vs `config/loader.py:21` | Implement `os.path.expandvars` in the loader, or drop the placeholder and rely on the `_env(...)` override path. |
| H5 | **Risk-engine guard bugs** (reported by review; `backtesting/engine.py` is currently modified in the tree — verify): operator-precedence error makes the daily-loss halt dead (`:139`), and `daily_halt = False` is reset on a leverage clip, silently re-enabling trading after a halt (`:176`). | `backtesting/engine.py:139,176` | Parenthesize the daily-PnL comparison; remove the halt reset. **Confirm against current diff.** |

### Medium (selected)
- **Feature-loading SQL duplicated at 5 sites** with identical `get_connection / pd.read_sql / finally` boilerplate: `models/train.py:150`, `models/validator.py:193`, `monitoring/drift_detector.py:82` & `:108`, `main.py:685`. (See Refactor R1.)
- **Two signal-generation truths** that disagree on threshold: `main.py:704` (z>1.5, BUY/SELL/HOLD) vs `backtesting/engine_eval.py` (z>2.0, adds CLOSE).
- **Split logging frameworks** — 6 modules use stdlib `logging` (`db.py`, `engine_eval.py`, `persist.py`, `collectors`, `clear_database.py`, `price_features.py`), the rest use structlog; structured-kwarg log calls are a latent crash on the stdlib loggers.
- **`ModelValidator.engine_config: Dict[str,Any]` is forwarded blindly** into `BacktestEngine(**cfg)` (`models/validator.py:74`, `backtesting/walk_forward.py:70`) — the earlier `BacktestEngine(**whole_retraining_config)` crash was a direct symptom. Replace with a typed `EngineConfig`.

### Dead code
`portfolio/` (216 LOC — only referenced by its test), `features/factor_models.py` (93 LOC, no non-test caller), `strategies/factor_model.py::FactorStrategy` (imported in `main.py:672` but never used), `data/clear_database.py` (not wired to the CLI), `dev/` (empty scaffold), legacy `StatArbStrategy.get_trades` / `use_fixed_window=False` "DEPRECATED" branch. The `factor_model` retrain path trains a `LinearRegression` on stat-arb features — it never sees factor data.

---

## 2. Refactor Opportunities (ranked by ROI)

1. **Extract `DatabaseConnection.load_pair_features(symbol, columns=None, since=None, until=None)`.** Kills the 5-site feature-SQL duplication and centralizes the checkout pattern (~40 LOC + 4 duplicate `try/finally` removed). Highest ROI, lowest risk.
2. **Decompose `main.py`** into per-package command modules behind one `db_session()` context manager — removes the 10× copy-pasted DB preamble and the largest single source of coupling.
3. **Single-source engine/strategy parameters.** The literal block `entry_threshold=2.0, exit_threshold=0.5, lookback=60, leg_allocation_pct=0.18, commission=0.001, …` is repeated verbatim in `run_backtest`, `run_full_pipeline`, and `run_paper_trading` though it already lives on `Settings`. Build the engine from `get_settings()` in one factory.
4. **Unify configuration** through `Settings`: delete the ~36 scattered `os.getenv` reads (26 in `main.py`), collapse the two `DatabaseConnection` constructor signatures into one factory, and replace `validator.engine_config: Dict` with a typed dataclass.
5. **Standardize logging on `config.logging_config.get_logger`** across the 6 stdlib modules; remove the `db.py:38` override.

---

## 3. Security Findings

| # | Finding | Evidence | Severity | Fix |
|---|---|---|---|---|
| S1 | **Hardcoded `'postgres'` password defaults** — silently authenticate with a guessable credential when `DB_PASSWORD` is unset, masking misconfiguration. | `main.py:66,562,680`; `monitoring/run_dashboard.py:95` | High | Fail-fast on missing `DB_PASSWORD` (the `data/db.py:89` pattern, which returns `None`, is already correct — apply it everywhere). |
| S2 | **Unquoted f-string SQL** — `SELECT timestamp, {columns} FROM features` interpolates column names read back from `model_registry.feature_names`. Currently sourced from a hardcoded whitelist (`models/train.py`), so low live exploitability, but unvalidated identifier interpolation. | `monitoring/drift_detector.py:81-87,107-112`; `models/retraining_scheduler.py` | Medium→High | Validate each name against the known feature set or use `psycopg2.sql.Identifier`. |
| S3 | **`${DB_PASSWORD}` placeholder gives false confidence** — interpolation isn't implemented (see H4). | `config/settings.yaml:8` | Medium | Implement expansion or remove the placeholder. |
| S4 | **`SELECT *`** in registry reads is fragile to schema drift and depends on downstream JSON decode of `feature_names`. | `data/db.py:769,787,800` | Low | Select explicit columns. |

**Good hygiene confirmed:** `.env` is gitignored, `.env.example` ships empty secrets, no DSN/API keys are committed, and no log line emits credentials (`_initialize_pool` logs only the database name). **Recommended strategy:** remove all password fallbacks → fail fast; adopt `pydantic-settings` `BaseSettings` for typed env loading; inject prod secrets via the platform store (the CI integration job already uses GitHub Actions secrets), not `.env` files.

---

## 4. Scalability Risks

1. **Thread-unsafe pool + tiny `max_conn=5`** (C1) — the single hardest blocker to running the scheduler and a web/API surface concurrently. Caps real concurrency at 5 and risks corruption under threads.
2. **`OR`-predicate on `features(symbol_a, symbol_b)` is effectively unindexed.** The two hottest paths — drift checks (hourly) and validation (every retrain) — filter `(symbol_a=%s OR symbol_b=%s)`, which the composite `idx_pair_time` cannot serve; Postgres seq-scans or BitmapOrs poorly, and `symbol_b` is not a usable prefix. **Add `features(symbol_a, timestamp)` and `features(symbol_b, timestamp)`** so the planner can BitmapOr two index scans. No standalone `features(timestamp)` index exists for the `NOW() - INTERVAL` range filter either.
3. **`pd.read_sql` on raw psycopg2 connections** (11+ sites) — emits the pandas "SQLAlchemy connectable" deprecation warning and rides an unsupported path that may break on a pandas upgrade. Introduce one module-level SQLAlchemy engine.
4. **Connection leaks (C2)** degrade to pool exhaustion under sustained error rates — a slow-motion outage, not a crash.
5. **Dual schema source of truth** — `data/schema.sql` *and* Alembic both define the baseline, and have already diverged (`schema.sql` uses `timestamp DESC` indexes and lacks `model_registry`/`drift_reports`/`model_blocks`, which exist only in migration `0002`). A DB bootstrapped from `schema.sql` holds data while Alembic's version table says nothing is applied — exactly the failure mode observed live (the `0002` tables were missing on a populated DB). **Pick Alembic as canonical; drop/mark `schema.sql` dev-only; document `alembic stamp 0001` for pre-existing DBs.** Also: `alembic/env.py` only reads the URL from `DATABASE_URL` (commented `sqlalchemy.url` fallback yields an empty URL), and `target_metadata=None` disables drift detection.

   > ✅ **RESOLVED** — Alembic is now canonical: `data/schema.sql` carries a "reference only" header (with the `alembic stamp 0001` note for pre-existing DBs) and the README/CHANGELOG point to `alembic upgrade head`. DB URL is now a single source of truth via `config.loader.build_database_url`, used by both `DatabaseConnection` and `alembic/env.py` (which now also loads `.env` and no longer depends on a bare `DATABASE_URL`). `target_metadata=None` (autogenerate drift detection) is unchanged — migrations remain hand-authored.
6. **MLflow file store (`file:./mlruns`)** is deprecated (Feb 2026) and not concurrency-safe; migrate to a `sqlite:///` or Postgres backend before multi-worker training.

---

## 5. Database Layer — detail

- **Connection-return audit:** 27 pooled-connection methods; **0** use `try/finally` for the return. ~14 return only on success + a fragile `except`; ~9 read methods are *mostly* safe via `return_connection(None)` no-op; **4 `delete_*` helpers + `get_table_counts` leak on error.** Pool *is* closed via `close_pool()/closeall()` in `main.py`/`predict.py`, but several entrypoints (`run_dashboard.py`, `verify_data.py`) never call it (Low — process exit covers it).
- **Indexing verdict:** `prices` good; `model_registry`/`drift_reports` good (partial unique + composite indexes from `0002`); **`features` OR-predicate is the top gap** (see Scalability #2).
- **Collectors are a bright spot:** `binance_collector.py` uses `tenacity` correctly — retries only transient ccxt errors, `wait_exponential(max=60)`, `stop_after_attempt(5)`, `reraise=True`; failures surface in `CollectionResult.errors` rather than silently dropping. One caveat: a mid-pagination failure returns a **partial** frame — callers must inspect `errors`.

---

## 6. Testing Coverage — measured

- **Overall: ~46%** line coverage on source packages (`pytest-cov`; `--cov=.` incl. test files reads ~60%). **171 passed, 4 xfailed.** **Fails the >80% target.**

| Package | Cover% | Biggest gaps |
|---|---:|---|
| portfolio | 100% | — |
| monitoring | 93% | drift-branch logic |
| config | 85% | `mlflow_config.py` (68%) |
| models | 63% | `train.py` (37%), `validator.py` (68%) |
| backtesting | 58% | `engine_eval.py` (11%), `persist.py` (14%) |
| data | 34% | `db.py` (19%, 446 missing lines) |
| strategies | 30% | `factor_model.py` (0%) |
| features | 28% | `factor_models.py` (0%) |
| **main.py** | **0%** | entire CLI orchestrator (709 lines) |

- **Test classes:** ~150 offline unit tests (DB always `MagicMock`); 3 component-integration files (`test_retraining_integration`, `test_validation`, `test_stat_arb_wf`) that are still fully offline/synthetic. **No true end-to-end test** exercises `collect→feature→backtest` or `train→validate→promote` against real Postgres/MLflow.
- **Highest-ROI additions:** (1) CLI smoke tests for `main.py` with mocked DB/MLflow (~+12-15% alone); (2) `data/db.py` round-trip tests against a temp Postgres/SQLite fixture (none exists today); (3) `engine_eval.py`+`persist.py` on the existing synthetic fixtures; (4) one genuine e2e on a temp DB + `file://` MLflow store.

---

## 7. CI/CD — delivered

**`.github/workflows/ci.yml`** (created, valid YAML, Python 3.12, `checkout@v4` + `setup-python@v5` w/ pip cache). Job DAG:

```
lint ─┐
      ├─► coverage ──► build
unit ─┤
      └─► integration ─┘
```

| Job | Action | Gate |
|---|---|---|
| lint | `ruff check` + `ruff format --check` | advisory (`continue-on-error`) until a `[tool.ruff]` config + cleanup land |
| unit | `pip install -r requirements.txt`; offline `pytest` (DB-mocked) | **blocking** |
| integration | Postgres 16 `services:` (health-checked), sets `DATABASE_URL`/`DB_*`, runs `alembic upgrade head`, then DB tests | allowed-partial (repo has ~no live-DB tests yet) |
| coverage | `pytest --cov=. --cov-fail-under=60 --cov-report=xml` | **blocking @ 60%, ratchet → 80%** |
| build | `compileall` + import-smoke of the 8 top packages | **blocking** |

**To make CI fully green/strict:** add `pyproject.toml` with `[tool.ruff]` + `[tool.pytest]` markers, tag real DB tests with an `integration` marker (then flip that job to blocking), clean lint debt, and ratchet the coverage gate upward (never down).

---

## Punch-list (do in this order)

1. `ThreadedConnectionPool` + `try/finally` connection returns (C1, C2) — correctness/availability.
2. Stop swallowing DB errors; raise typed `DBError` (H1).
3. Remove `'postgres'` password fallbacks; implement/remove `${DB_PASSWORD}` (S1, H4).
4. Delete the `db.py:38` logger override; standardize on structlog (H2).
5. Add `features(symbol_a,timestamp)` + `features(symbol_b,timestamp)` indexes (Scalability #2).
6. Collapse schema onto Alembic; document `alembic stamp` bootstrap (Scalability #5).
7. Extract `load_pair_features`; centralize config; decompose `main.py` (R1-R5).
8. Raise coverage >80% (CLI + db.py + one e2e); make lint/coverage gates blocking.
9. Move MLflow off the deprecated file store.

---
*Generated by a multi-agent architecture review (architecture, database, testing, CI/CD-and-prod-readiness streams). Findings are evidence-cited; verify against the current uncommitted diff before acting.*
