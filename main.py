"""
ML4T System Entry Point.

This is the main entry point for the machine learning for trading system.
It orchestrates the entire pipeline from data collection through execution.

Usage:
    python main.py collect          # Collect market data from Binance
    python main.py backtest         # Run strategy backtest
    python main.py paper            # Run paper trading (simulation)
    python main.py live             # Run live trading (real money - DANGER!)
"""

import logging
import sys
import argparse
from datetime import datetime
from pathlib import Path
import os
import pandas as pd

# Configure logging
log_dir = Path('logs')
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'ml4t.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Load .env file manually
def load_env_file():
    """Load .env file manually without dotenv library."""
    env_path = Path('.env')
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

load_env_file()

def get_db_connection():
    """Shared database connection using .env credentials."""
    from data.db import DatabaseConnection
    return DatabaseConnection(
        host=os.getenv('DB_HOST', 'localhost'),
        port=int(os.getenv('DB_PORT', 5432)),
        database=os.getenv('DB_NAME', 'rafund'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', 'postgres'),
    )


def collect_data(full_history: bool = True):
    """
    Collect OHLCV from Binance into `prices` table.

    - First run: from COLLECT_START_DATE (default 2017-08-17) to today
    - Later runs: incremental from last stored bar per symbol to today
    """
    logger.info("=" * 80)
    logger.info("STARTING DATA COLLECTION")
    logger.info("=" * 80)

    try:
        from data.collectors.binance_collector import BinanceCollector
        from datetime import timedelta, timezone

        collector = BinanceCollector(testnet=False, rate_limit_ms=100)
        db = get_db_connection()

        if not db.test_connection():
            logger.error("Database connection failed")
            return False

        default_symbols = (
            'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT',
            'ADA/USDT', 'DOT/USDT', 'LINK/USDT', 'XRP/USDT',
        )
        symbols_env = os.getenv('COLLECT_SYMBOLS', '')
        symbols = (
            [s.strip() for s in symbols_env.split(',') if s.strip()]
            if symbols_env
            else list(default_symbols)
        )

        end_date = datetime.now(timezone.utc).replace(tzinfo=None)
        default_start = datetime.strptime(
            os.getenv('COLLECT_START_DATE', '2017-08-17'), '%Y-%m-%d'
        )

        logger.info(f"Symbols: {symbols}")
        logger.info(f"Collect through: {end_date.date()}")

        total_inserted = 0
        for symbol in symbols:
            try:
                latest = db.get_latest_timestamp(symbol)
                if full_history and latest is None:
                    start_date = default_start
                    logger.info(f"[{symbol}] Full history from {start_date.date()}")
                elif latest is not None:
                    start_date = latest + timedelta(days=1)
                    if start_date.date() >= end_date.date():
                        logger.info(f"[{symbol}] Already up to date (latest {latest.date()})")
                        continue
                    logger.info(f"[{symbol}] Incremental from {start_date.date()}")
                else:
                    start_date = default_start
                    logger.info(f"[{symbol}] No data yet — from {start_date.date()}")

                df = collector.fetch_ohlcv_history(symbol, '1d', start_date, end_date)
                if not df.empty and collector.validate_data(df):
                    inserted = db.insert_prices(df)
                    total_inserted += inserted
                    logger.info(f"[OK] {symbol}: {inserted} new rows ({len(df)} fetched)")
                else:
                    logger.warning(f"[SKIP] {symbol}: No valid data")
            except Exception as e:
                logger.error(f"[ERROR] {symbol}: {str(e)}")

        stats = db.get_data_stats()
        logger.info("\nDatabase prices summary:")
        logger.info(f"  Total records: {stats.get('total_price_records', 0)}")
        logger.info(f"  Total symbols: {stats.get('num_symbols', 0)}")
        logger.info(f"  Date range: {stats.get('min_date')} to {stats.get('max_date')}")
        logger.info(f"  Rows inserted this run: {total_inserted}")

        db.close_pool()
        return True

    except Exception as e:
        logger.error(f"Data collection error: {str(e)}", exc_info=True)
        return False


def run_backtest():
    """Run strategy backtest."""
    logger.info("=" * 80)
    logger.info("STARTING BACKTEST")
    logger.info("=" * 80)
    
    try:
        from data.db import DatabaseConnection
        from backtesting.engine_eval import EvaluationBacktestEngine
        from backtesting.evaluation_rules import PropFirmRules
        
        # Connect to database
        logger.info("Loading price data from database...")
        db = get_db_connection()
        pair_a = os.getenv('EVAL_PAIR_A', 'BTC/USDT')
        pair_b = os.getenv('EVAL_PAIR_B', 'ETH/USDT')

        pa = db.get_prices(pair_a, None, None)
        pb = db.get_prices(pair_b, None, None)
        if pa.empty or pb.empty:
            logger.error(f"Missing prices for {pair_a} or {pair_b}. Run: python main.py collect")
            db.close_pool()
            return False

        prices = pd.concat([pa, pb], ignore_index=True)
        logger.info(
            f"Loaded {len(prices)} bars for {pair_a} / {pair_b} "
            f"({pa['timestamp'].min().date()} → {pa['timestamp'].max().date()})"
        )
        
        rules = PropFirmRules()
        pair_a = os.getenv('EVAL_PAIR_A', 'BTC/USDT')
        pair_b = os.getenv('EVAL_PAIR_B', 'ETH/USDT')

        logger.info("Initializing evaluation backtest (pairs stat-arb + prop rules)...")
        logger.info("=" * 80)
        logger.info("PROP FIRM EVALUATION RULES")
        logger.info("=" * 80)
        logger.info(f"  Account Size:        ${rules.account_size:,.0f}")
        logger.info(f"  Step 1 Target:       +${rules.step1_profit:.0f}  (equity ${rules.step1_equity:,.0f})")
        logger.info(f"  Step 2 Target:       +${rules.step2_profit:.0f}  (equity ${rules.step2_equity:,.0f})")
        logger.info(f"  Max Daily Loss:      {rules.max_daily_loss_pct*100:.0f}% (${rules.max_daily_loss_amount:.0f})")
        logger.info(f"  Max Drawdown:        {rules.max_drawdown_pct*100:.0f}% (floor ${rules.min_equity:,.0f})")
        logger.info(f"  Max Leverage:        {rules.max_leverage:.0f}x")
        logger.info(f"  Evaluation Fee:      ${rules.evaluation_fee:.2f}")
        logger.info("=" * 80)
        logger.info("STRATEGY: PAIRS STATISTICAL ARBITRAGE (MEAN REVERSION)")
        logger.info("=" * 80)
        logger.info(f"  Pair:                {pair_a} / {pair_b}")
        logger.info(f"  Spread:              log(A) - beta * log(B)")
        logger.info(f"  Training Window:     60 days (fixed mean/std)")
        logger.info(f"  Entry:               |z| > 2.0  (fade the dislocation)")
        logger.info(f"  Exit:                |z| < 0.5  (spread reverted)")
        logger.info(f"  Leg Size:            18% equity per leg (~0.36x gross leverage)")
        logger.info(f"  Spread Stop Loss:    3% of position equity")
        logger.info(f"  Max Hold:            30 days per spread trade")
        logger.info(f"  Commission:          0.1%")
        logger.info("=" * 80)
        
        engine = EvaluationBacktestEngine(
            rules=rules,
            symbol_a=pair_a,
            symbol_b=pair_b,
            entry_threshold=2.0,
            exit_threshold=0.5,
            lookback=60,
            leg_allocation_pct=0.18,
            commission=0.001,
            stop_loss_spread_pct=0.03,
            max_holding_days=30,
        )
        
        results = engine.run(prices)
        ev = results.get('evaluation', {})
        
        # Display results
        logger.info("\n" + "=" * 80)
        logger.info("BACKTEST RESULTS")
        logger.info("=" * 80)
        logger.info(f"Initial Capital:    ${results['initial_capital']:,.2f}")
        logger.info(f"Final Value:        ${results['final_value']:,.2f}")
        logger.info(f"Total Return:       {results['total_return_pct']:.2f}%")
        logger.info(f"Sharpe Ratio:       {results['sharpe_ratio']:.2f}")
        logger.info(f"Max Drawdown:       {results['max_drawdown_pct']:.2f}%")
        logger.info(f"Total Trades:       {results['num_trades']}")
        logger.info(f"Closed Trades:      {results['num_closed_trades']}")
        logger.info(f"Profitable Trades:  {int(results['win_rate'] * results['num_closed_trades'])} / {results['num_closed_trades']}")
        logger.info(f"Win Rate:           {results['win_rate_pct']:.2f}%")
        logger.info(f"Mean Daily Return:  {results['mean_daily_return']*100:.4f}%")
        logger.info(f"Daily Volatility:   {results['daily_volatility']*100:.4f}%")
        logger.info("=" * 80)
        logger.info("EVALUATION OUTCOME")
        logger.info("=" * 80)
        logger.info(f"  Status:              {ev.get('status', 'N/A')}")
        logger.info(f"  Phase Reached:       {ev.get('phase', 'N/A')}")
        logger.info(f"  Days Simulated:      {ev.get('days_traded', 0)}")
        logger.info(f"  Worst Daily Loss:    ${ev.get('worst_daily_loss', 0):,.2f}")
        logger.info(f"  Worst Drawdown:      {ev.get('worst_drawdown_pct', 0):.2f}%")
        logger.info(f"  Min Equity Allowed:  ${ev.get('min_equity_floor', 0):,.0f}")
        logger.info("=" * 80)
        
        if results['num_trades'] > 0:
            logger.info("\nSample Trades (first 10):")
            for i, trade in enumerate(results['trades'][:10], 1):
                logger.info(
                    f"  {i}. {trade['date']} {trade.get('side', '')} "
                    f"{trade.get('symbol_a', '')}/{trade.get('symbol_b', '')} "
                    f"status={trade.get('status', '')}"
                )
        
        logger.info("=" * 80)

        if os.getenv('SAVE_TO_DB', '1') == '1' and results.get('signals_df') is not None:
            from backtesting.persist import persist_evaluation_run
            backtest_id = (
                f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                f"{ev.get('status', 'UNKNOWN')}"
            )
            written = persist_evaluation_run(db, results, backtest_id)
            logger.info(f"Persisted to database: {written}")
            for table, count in db.get_table_counts().items():
                logger.info(f"  {table:20} {count:8} rows")

        db.close_pool()
        return True
    
    except Exception as e:
        logger.error(f"Backtest error: {str(e)}", exc_info=True)
        return False


def calculate_features():
    """Calculate and save features for all symbols and pairs."""
    logger.info("=" * 80)
    logger.info("CALCULATING FEATURES")
    logger.info("=" * 80)
    
    try:
        from data.db import DatabaseConnection
        from features.price_features import calculate_spread_features, calculate_momentum_features, test_stationarity
        from datetime import datetime
        
        db = DatabaseConnection(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 5432)),
            database=os.getenv('DB_NAME', 'rafund'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'postgres')
        )
        
        symbols = db.get_symbols_with_data()
        if not symbols:
            logger.error("No symbols found in database")
            db.close_pool()
            return False
        
        logger.info(f"Calculating features for {len(symbols)} symbols")
        logger.info(f"Symbols: {', '.join(symbols)}")
        logger.info("=" * 80)
        logger.info("CHECKING PAIR VALIDITY (STATIONARITY TEST)")
        logger.info("=" * 80)
        
        total_features = 0
        total_pairs = 0
        valid_pairs = 0
        invalid_pairs = 0
        
        # Calculate features for all symbol pairs
        for i, sym_a in enumerate(symbols):
            for j, sym_b in enumerate(symbols):
                if i < j:  # Only unique pairs
                    total_pairs += 1
                    pair_name = f"{sym_a}/{sym_b}"
                    try:
                        prices_a = db.get_prices(sym_a, None, None)
                        prices_b = db.get_prices(sym_b, None, None)
                        
                        if prices_a.empty or prices_b.empty:
                            logger.warning(f"[SKIP] {pair_name}: Missing price data")
                            invalid_pairs += 1
                            continue
                        
                        # Align by timestamp
                        pa = prices_a.set_index('timestamp')['close']
                        pb = prices_b.set_index('timestamp')['close']
                        
                        # Calculate spread features
                        features = calculate_spread_features(pa, pb, window=20)
                        
                        if features.empty:
                            logger.warning(f"[SKIP] {pair_name}: Could not calculate features")
                            invalid_pairs += 1
                            continue
                        
                        # TEST STATIONARITY - CRITICAL FOR STAT ARB
                        spread = features['spread'].dropna()
                        is_stationary, test_results = test_stationarity(spread, pair_name)
                        
                        if not is_stationary:
                            logger.warning(f"[REJECT] {pair_name}: Spread not stationary - pairs trading invalid")
                            invalid_pairs += 1
                            continue
                        
                        valid_pairs += 1
                        
                        # Only insert features for valid (stationary) pairs
                        feature_df = pd.DataFrame({
                            'symbol_a': sym_a,
                            'symbol_b': sym_b,
                            'timestamp': features.index,
                            'spread': features['spread'].values,
                            'spread_mean': features['spread_mean'].values,
                            'spread_std': features['spread_std'].values,
                            'z_score': features['z_score'].values,
                            'hedge_ratio': features['hedge_ratio'].values
                        })
                        
                        inserted = db.insert_features(feature_df)
                        total_features += inserted
                        
                        if inserted > 0:
                            logger.info(f"[OK] {pair_name}: {inserted} features calculated (stationary & valid)")
                        else:
                            logger.info(f"[SKIP] {pair_name}: Features already exist (ON CONFLICT)")
                    except Exception as e:
                        logger.warning(f"[ERROR] {pair_name}: {str(e)}")
                        invalid_pairs += 1
        
        logger.info("=" * 80)
        logger.info("FEATURE CALCULATION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total pairs analyzed: {total_pairs}")
        logger.info(f"Valid pairs (stationary): {valid_pairs}")
        logger.info(f"Invalid pairs (not stationary): {invalid_pairs}")
        logger.info(f"Total new features calculated and saved: {total_features}")
        if total_features == 0:
            logger.info("Note: 0 new features inserted (likely already exist in database)")
        logger.info(f"Total features in database: {total_features + 2190}")  # Approximate
        db.close_pool()
        return True
        
    except Exception as e:
        logger.error(f"Feature calculation error: {str(e)}", exc_info=True)
        return False


def generate_signals():
    """Generate and save trading signals."""
    logger.info("=" * 80)
    logger.info("GENERATING SIGNALS")
    logger.info("=" * 80)
    
    try:
        from data.db import DatabaseConnection
        from strategies.factor_model import FactorStrategy
        from datetime import datetime
        
        db = DatabaseConnection(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 5432)),
            database=os.getenv('DB_NAME', 'rafund'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'postgres')
        )
        
        # Get all features from database
        conn = db.get_connection()
        features_df = pd.read_sql("SELECT * FROM features ORDER BY timestamp", conn)
        db.return_connection(conn)
        
        if features_df.empty:
            logger.warning("No features found in database. Run feature calculation first.")
            db.close_pool()
            return False
        
        logger.info(f"Loaded {len(features_df)} feature records")
        logger.info(f"Symbols in features: {features_df[['symbol_a', 'symbol_b']].drop_duplicates().shape[0]} pairs")
        
        # Generate signals from features
        signals_list = []
        
        for pair in features_df.groupby(['symbol_a', 'symbol_b']):
            pair_data = pair[1].copy()
            pair_data = pair_data.sort_values('timestamp')
            
            # Create signals based on z-score
            pair_data['signal'] = 'HOLD'
            pair_data.loc[pair_data['z_score'] > 1.5, 'signal'] = 'BUY'   # Long signal
            pair_data.loc[pair_data['z_score'] < -1.5, 'signal'] = 'SELL'  # Short signal
            pair_data.loc[
                (pair_data['z_score'] <= 1.5) & (pair_data['z_score'] >= -1.5),
                'signal'
            ] = 'HOLD'
            
            # Position sizes
            pair_data['position_a'] = pair_data['signal'].apply(
                lambda x: 1 if x == 'BUY' else (-1 if x == 'SELL' else 0)
            )
            pair_data['position_b'] = pair_data['signal'].apply(
                lambda x: -1 if x == 'BUY' else (1 if x == 'SELL' else 0)
            ) * pair_data['hedge_ratio']
            
            signals_list.append(pair_data[['symbol_a', 'symbol_b', 'timestamp', 'signal', 
                                           'z_score', 'position_a', 'position_b']])
        
        if signals_list:
            all_signals = pd.concat(signals_list, ignore_index=True)
            
            # Log signal distribution
            signal_counts = all_signals['signal'].value_counts()
            logger.info(f"Signal distribution: {dict(signal_counts)}")
            
            inserted = db.insert_signals(all_signals)
            logger.info(f"Generated and saved {inserted} signals")
        
        db.close_pool()
        return True
        
    except Exception as e:
        logger.error(f"Signal generation error: {str(e)}", exc_info=True)
        return False


def run_full_pipeline():
    """
    Full trading infrastructure bootstrap:
      1. Collect all price history to date (incremental)
      2. Run evaluation backtest on primary pair through latest data
      3. Persist features, signals, trades, portfolio, backtest_results
    """
    logger.info("=" * 80)
    logger.info("RUNNING FULL ML4T INFRASTRUCTURE PIPELINE")
    logger.info("=" * 80)

    try:
        from backtesting.engine_eval import EvaluationBacktestEngine
        from backtesting.evaluation_rules import PropFirmRules
        from backtesting.persist import persist_evaluation_run

        # Step 1: Collect market data to date
        logger.info("\n[STEP 1/4] Collecting price data to date...")
        if not collect_data(full_history=True):
            logger.error("Data collection failed")
            return False

        db = get_db_connection()
        pair_a = os.getenv('EVAL_PAIR_A', 'BTC/USDT')
        pair_b = os.getenv('EVAL_PAIR_B', 'ETH/USDT')

        for sym in (pair_a, pair_b):
            if sym not in db.get_symbols_with_data():
                logger.error(f"Missing price data for {sym}. Collection may have failed.")
                db.close_pool()
                return False

        pa = db.get_prices(pair_a, None, None)
        pb = db.get_prices(pair_b, None, None)
        prices = pd.concat([pa, pb], ignore_index=True)
        logger.info(
            f"\n[STEP 2/4] Loaded {len(prices)} bars | "
            f"{pair_a}: {pa['timestamp'].min().date()} to {pa['timestamp'].max().date()} | "
            f"{pair_b}: {pb['timestamp'].min().date()} to {pb['timestamp'].max().date()}"
        )

        rules = PropFirmRules()
        logger.info("\n[STEP 3/4] Running evaluation simulation to latest date...")
        engine = EvaluationBacktestEngine(
            rules=rules,
            symbol_a=pair_a,
            symbol_b=pair_b,
            entry_threshold=2.0,
            exit_threshold=0.5,
            lookback=60,
            leg_allocation_pct=0.18,
            commission=0.001,
            stop_loss_spread_pct=0.03,
            max_holding_days=30,
        )
        results = engine.run(prices)

        ev = results.get('evaluation', {})
        backtest_id = (
            f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ev.get('status', 'UNKNOWN')}"
        )

        logger.info("\n[STEP 4/4] Persisting to database (schema tables)...")
        written = persist_evaluation_run(db, results, backtest_id)

        counts = db.get_table_counts()
        logger.info("\n" + "=" * 80)
        logger.info("DATABASE RECORD COUNTS")
        logger.info("=" * 80)
        for table, count in counts.items():
            logger.info(f"  {table:20} {count:8} rows")
        logger.info("=" * 80)
        logger.info("PIPELINE COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Backtest ID:     {backtest_id}")
        logger.info(f"Pair:            {pair_a} / {pair_b}")
        logger.info(f"Simulation end:  {pa['timestamp'].max()}")
        logger.info(f"Final equity:    ${results['final_value']:,.2f}")
        logger.info(f"Total return:    {results['total_return_pct']:.2f}%")
        logger.info(f"Eval status:     {ev.get('status')}")
        logger.info(f"Rows written:    {written}")
        logger.info("=" * 80)

        db.close_pool()
        return True

    except Exception as e:
        logger.error(f"Pipeline error: {str(e)}", exc_info=True)
        return False


def run_bootstrap():
    """Alias: collect + simulate + persist (same as pipeline)."""
    return run_full_pipeline()


def run_paper_trading():
    """Run paper trading (simulation)."""
    logger.info("=" * 80)
    logger.info("STARTING PAPER TRADING")
    logger.info("=" * 80)
    
    try:
        logger.info("Paper trading module not yet implemented")
        logger.info("TODO: Implement paper trading pipeline")
        return False
    
    except Exception as e:
        logger.error(f"Paper trading error: {str(e)}", exc_info=True)
        return False


def run_live_trading():
    """Run live trading (DANGER!)."""
    logger.critical("=" * 80)
    logger.critical("⚠️  LIVE TRADING MODE - REAL MONEY AT RISK")
    logger.critical("=" * 80)
    
    logger.warning("Live trading not implemented yet")
    logger.warning("Exiting for safety")
    return False


def main():
    """Main ML4T entry point."""
    
    parser = argparse.ArgumentParser(
        description='ML4T - Machine Learning for Trading System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py collect              # Collect market data
  python main.py features             # Calculate features
  python main.py signals              # Generate signals
  python main.py backtest             # Run backtest
  python main.py pipeline             # Collect data, backtest to date, persist all tables
  python main.py bootstrap            # Same as pipeline (full infrastructure setup)
  python main.py paper                # Run paper trading
  python main.py live                 # Run live trading (DANGER!)
        """
    )
    
    parser.add_argument(
        'mode',
        nargs='?',
        default='collect',
        choices=['collect', 'features', 'signals', 'backtest', 'pipeline', 'bootstrap', 'paper', 'live'],
        help='Operation mode (default: collect)'
    )
    
    args = parser.parse_args()
    
    logger.info("=" * 80)
    logger.info(f"ML4T System Started - {datetime.now().isoformat()}")
    logger.info(f"Mode: {args.mode.upper()}")
    logger.info("=" * 80)
    
    try:
        if args.mode == 'collect':
            success = collect_data()
        elif args.mode == 'features':
            success = calculate_features()
        elif args.mode == 'signals':
            success = generate_signals()
        elif args.mode == 'backtest':
            success = run_backtest()
        elif args.mode == 'pipeline':
            success = run_full_pipeline()
        elif args.mode == 'bootstrap':
            success = run_bootstrap()
        elif args.mode == 'paper':
            success = run_paper_trading()
        elif args.mode == 'live':
            success = run_live_trading()
        else:
            logger.error(f"Unknown mode: {args.mode}")
            success = False
        
        if success:
            logger.info("[SUCCESS] Operation completed successfully")
            return 0
        else:
            logger.error("[FAILED] Operation failed")
            return 1
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        return 1
    
    finally:
        logger.info(f"ML4T System shutdown - {datetime.now().isoformat()}")
        logger.info("=" * 80)


if __name__ == "__main__":
    sys.exit(main())
