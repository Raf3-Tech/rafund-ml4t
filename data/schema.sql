-- ML4T Database Schema
-- Date: 2026-04-16

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
