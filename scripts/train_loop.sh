#!/usr/bin/env bash
# Continuous training loop: collect → engine → research → retrain, then sleep.
# Set LOOP_INTERVAL (seconds) to control cadence; default is 1800 (30 min).
# Each step is non-fatal — a single failure logs a warning and the loop continues.
set -euo pipefail

LOOP_INTERVAL="${LOOP_INTERVAL:-1800}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

cycle=0
while true; do
    cycle=$((cycle + 1))
    log "========== Training cycle #${cycle} =========="

    log "Step 1/5 — Collecting OHLCV + funding data (Binance + Kraken)..."
    python main.py collect --exchange all --funding \
        || log "WARNING: collect step failed, continuing"

    log "Step 2/5 — Walk-forward engine (all strategies × symbols)..."
    python main.py engine \
        || log "WARNING: engine step failed, continuing"

    log "Step 3/5 — Research pipeline..."
    python main.py research \
        || log "WARNING: research step failed, continuing"

    log "Step 4/5 — Retraining ML models..."
    python main.py retrain \
        || log "WARNING: retrain step failed, continuing"

    log "Step 5/6 — Leaderboard snapshot:"
    python main.py leaderboard || true

    log "Step 6/6 — Paper trading cycle (virtual position, prop-firm rules)..."
    python main.py paper \
        || log "WARNING: paper trading step failed, continuing"

    log "========== Cycle #${cycle} complete — sleeping ${LOOP_INTERVAL}s =========="
    sleep "${LOOP_INTERVAL}"
done
