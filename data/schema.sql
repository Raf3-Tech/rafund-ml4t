-- ML4T Database Schema  —  ⚠️ REFERENCE ONLY, NOT THE SOURCE OF TRUTH ⚠️
-- Date: 2026-06-07 (updated with engine_results + funding_rates)
--
-- Alembic is the canonical schema. Provision and migrate every database with:
--     alembic upgrade head
--
-- Do NOT bootstrap a database from this file: it has drifted from the
-- migrations (different index definitions; missing the model_registry /
-- drift_reports / model_blocks tables added in migration 0002). A DB created
-- from here will also leave Alembic's version table empty, so migrations think
-- nothing is applied. This file is kept only as a human-readable overview.
-- For a pre-existing DB created from this file, run: alembic stamp 0001

-- Create ENUM for trade signals
CREATE TYPE signal_type AS ENUM ('BUY', 'SELL', 'HOLD', 'CLOSE');

-- Price data table
CREATE TABLE IF NOT EXISTS prices (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, timestamp)
);

-- Create index for fast lookups
CREATE INDEX IF NOT EXISTS idx_symbol_time ON prices(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_timestamp ON prices(timestamp DESC);

-- Features table (spread, z-score, etc.)
CREATE TABLE IF NOT EXISTS features (
    id SERIAL PRIMARY KEY,
    symbol_a VARCHAR(20) NOT NULL,
    symbol_b VARCHAR(20) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    spread DOUBLE PRECISION,
    spread_mean DOUBLE PRECISION,
    spread_std DOUBLE PRECISION,
    z_score DOUBLE PRECISION,
    hedge_ratio DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol_a, symbol_b, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_pair_time ON features(symbol_a, symbol_b, timestamp DESC);

-- Signals table
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    symbol_a VARCHAR(20) NOT NULL,
    symbol_b VARCHAR(20) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    signal signal_type NOT NULL,
    z_score DOUBLE PRECISION,
    position_a INTEGER,  -- position size for asset A
    position_b INTEGER,  -- position size for asset B
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_signal_time ON signals(symbol_a, symbol_b, timestamp DESC);

-- Trades table
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    trade_date TIMESTAMP NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION,
    quantity INTEGER NOT NULL,
    direction VARCHAR(10) NOT NULL,  -- LONG or SHORT
    pnl DOUBLE PRECISION,
    return_pct DOUBLE PRECISION,
    status VARCHAR(20) NOT NULL,  -- OPEN, CLOSED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);

-- Portfolio table
CREATE TABLE IF NOT EXISTS portfolio (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    position_size INTEGER NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    current_price DOUBLE PRECISION,
    unrealized_pnl DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(timestamp, symbol)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_time ON portfolio(timestamp DESC);

-- Backtest results
CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    backtest_id VARCHAR(50) NOT NULL UNIQUE,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    initial_capital DOUBLE PRECISION NOT NULL,
    final_value DOUBLE PRECISION NOT NULL,
    total_return DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    win_rate DOUBLE PRECISION,
    num_trades INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_backtest_id ON backtest_results(backtest_id);

-- Walk-forward engine results (added migration 0003)
-- See alembic/versions/0003_add_engine_results_funding_rates.py
CREATE TABLE IF NOT EXISTS engine_results (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    strategy_name VARCHAR(100) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    window_type VARCHAR(20) NOT NULL,
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    window_years FLOAT NOT NULL,
    params JSONB NOT NULL,
    total_return_pct FLOAT,
    sharpe_ratio FLOAT,
    max_drawdown_pct FLOAT,
    win_rate_pct FLOAT,
    num_trades INTEGER,
    conservative_pass BOOLEAN,
    standard_pass BOOLEAN,
    permissive_pass BOOLEAN,
    failure_reason TEXT,
    mutation_generation INTEGER DEFAULT 0,
    parent_run_id UUID,
    bars_used INTEGER,
    regime_trend FLOAT,
    regime_volatility FLOAT,
    regime_direction VARCHAR(10),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_engine_results_strategy ON engine_results(strategy_name);
CREATE INDEX IF NOT EXISTS idx_engine_results_symbol ON engine_results(symbol);
CREATE INDEX IF NOT EXISTS idx_engine_results_run_id ON engine_results(run_id);

-- 8-hour perpetual futures funding rates (added migration 0003)
CREATE TABLE IF NOT EXISTS funding_rates (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(50) NOT NULL,
    funding_time TIMESTAMP NOT NULL,
    funding_rate FLOAT NOT NULL,
    mark_price FLOAT,
    UNIQUE(symbol, funding_time)
);

CREATE INDEX IF NOT EXISTS idx_funding_rates_symbol ON funding_rates(symbol);
CREATE INDEX IF NOT EXISTS idx_funding_rates_time ON funding_rates(funding_time);
