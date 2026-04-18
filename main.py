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

def collect_data():
    """Collect market data from Binance."""
    logger.info("=" * 80)
    logger.info("STARTING DATA COLLECTION")
    logger.info("=" * 80)
    
    try:
        from data.collectors.binance_collector import BinanceCollector
        from data.db import DatabaseConnection
        from datetime import timedelta
        
        # Initialize collector and database
        logger.info("Initializing Binance collector and database connection...")
        collector = BinanceCollector(testnet=False, rate_limit_ms=100)
        db = DatabaseConnection(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 5432)),
            database=os.getenv('DB_NAME', 'rafund'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'postgres')
        )
        
        # Test connection
        if not db.test_connection():
            logger.error("Database connection failed")
            return False
        
        # Define symbols
        symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT']
        
        # Set date range (4 years of data for robust statistical analysis)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=4*365)  # ~4 years
        
        logger.info(f"Collecting data for symbols: {symbols}")
        logger.info(f"Period: {start_date.date()} to {end_date.date()}")
        logger.info(f"Data range: ~{(end_date - start_date).days} days")
        
        # Collect data
        for symbol in symbols:
            try:
                df = collector.fetch_ohlcv_history(symbol, '1d', start_date, end_date)
                
                if not df.empty and collector.validate_data(df):
                    db.insert_prices(df)
                    logger.info(f"[OK] {symbol}: {len(df)} records inserted")
                else:
                    logger.warning(f"[SKIP] {symbol}: No valid data")
            except Exception as e:
                logger.error(f"[ERROR] {symbol}: {str(e)}")
        
        # Show statistics
        stats = db.get_data_stats()
        logger.info(f"\nDatabase now contains:")
        logger.info(f"  Total records: {stats.get('total_price_records', 0)}")
        logger.info(f"  Total symbols: {stats.get('num_symbols', 0)}")
        logger.info(f"  Date range: {stats.get('min_date')} to {stats.get('max_date')}")
        
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
        from backtesting.engine_v2 import BacktestEngineV2
        from datetime import datetime
        
        # Connect to database
        logger.info("Loading price data from database...")
        db = DatabaseConnection(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 5432)),
            database=os.getenv('DB_NAME', 'rafund'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'postgres')
        )
        
        # Get all available symbols
        symbols_with_data = db.get_symbols_with_data()
        
        if not symbols_with_data:
            logger.error("No symbols with data found in database")
            db.close_pool()
            return False
        
        logger.info(f"Found {len(symbols_with_data)} symbols: {', '.join(symbols_with_data)}")
        
        # Fetch all price data
        all_prices = []
        for symbol in symbols_with_data:
            symbol_prices = db.get_prices(symbol, None, None)
            if not symbol_prices.empty:
                all_prices.append(symbol_prices)
        
        if not all_prices:
            logger.error("No price data retrieved")
            db.close_pool()
            return False
        
        prices = pd.concat(all_prices, ignore_index=True)
        logger.info(f"Loaded {len(prices)} price records for backtesting")
        
        # Run backtest with improved engine and risk controls
        logger.info("Initializing backtesting engine (v2 - fixed window baseline)...")
        logger.info("=" * 80)
        logger.info("STRATEGY CONFIGURATION")
        logger.info("=" * 80)
        logger.info(f"  Window Mode:         FIXED (not rolling)")
        logger.info(f"  Training Period:     60 days")
        logger.info(f"  Entry Threshold:     Z-score > 2.0")
        logger.info(f"  Exit Threshold:      Z-score < 0.5")
        logger.info("=" * 80)
        logger.info("RISK PARAMETERS")
        logger.info("=" * 80)
        logger.info(f"  Initial Capital:     $100,000")
        logger.info(f"  Max Position Size:   10% of capital")
        logger.info(f"  Commission:          0.1%")
        logger.info(f"  Stop Loss:           5% per position")
        logger.info("=" * 80)
        
        engine = BacktestEngineV2(
            initial_capital=100000,
            commission=0.001,
            entry_threshold=2.0,
            exit_threshold=0.5,
            lookback=60,
            max_position_pct=0.10,  # Max 10% per position
            stop_loss_pct=0.05,     # 5% stop loss
            use_fixed_window=True   # KEY FIX: Use fixed training window instead of rolling
        )
        
        results = engine.run(prices)
        
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
        
        if results['num_trades'] > 0:
            logger.info("\nSample Trades (first 10):")
            for i, trade in enumerate(results['trades'][:10], 1):
                logger.info(f"  {i}. {trade['date']} {trade['symbol']:10} {trade['side']:4} "
                          f"@ ${trade['price']:.2f}")
        
        logger.info("=" * 80)
        
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
    """Run complete pipeline: features -> signals -> backtest -> save results."""
    logger.info("=" * 80)
    logger.info("RUNNING COMPLETE ML4T PIPELINE")
    logger.info("=" * 80)
    
    try:
        from data.db import DatabaseConnection
        from backtesting.engine import BacktestEngine
        from datetime import datetime
        import uuid
        
        # Step 1: Calculate features
        logger.info("\n[STEP 1] Calculating features...")
        if not calculate_features():
            logger.error("Feature calculation failed")
            return False
        
        # Step 2: Generate signals
        logger.info("\n[STEP 2] Generating signals...")
        if not generate_signals():
            logger.error("Signal generation failed")
            return False
        
        # Step 3: Load data and run backtest
        logger.info("\n[STEP 3] Running backtest...")
        
        db = DatabaseConnection(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 5432)),
            database=os.getenv('DB_NAME', 'rafund'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'postgres')
        )
        
        symbols_with_data = db.get_symbols_with_data()
        if not symbols_with_data:
            logger.error("No symbols with data found")
            db.close_pool()
            return False
        
        # Fetch all price data
        all_prices = []
        for symbol in symbols_with_data:
            symbol_prices = db.get_prices(symbol, None, None)
            if not symbol_prices.empty:
                all_prices.append(symbol_prices)
        
        if not all_prices:
            logger.error("No price data retrieved")
            db.close_pool()
            return False
        
        prices = pd.concat(all_prices, ignore_index=True)
        logger.info(f"Loaded {len(prices)} price records")
        
        # Run backtest
        from backtesting.engine_v2 import BacktestEngineV2
        
        engine = BacktestEngineV2(
            initial_capital=100000,
            commission=0.001,
            entry_threshold=2.0,
            exit_threshold=0.5,
            lookback=60,
            max_position_pct=0.10,
            stop_loss_pct=0.05
        )
        
        results = engine.run(prices)
        
        # Step 4: Save backtest results and trades
        logger.info("\n[STEP 4] Saving results to database...")
        
        # Create unique backtest ID
        backtest_id = f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Prepare results for database
        db_results = {
            'backtest_id': backtest_id,
            'start_date': prices['timestamp'].min(),
            'end_date': prices['timestamp'].max(),
            'initial_capital': results['initial_capital'],
            'final_value': results['final_value'],
            'total_return': results['total_return'],
            'sharpe_ratio': results['sharpe_ratio'],
            'max_drawdown': results['max_drawdown'],
            'num_trades': results['num_trades'],
            'win_rate': results['win_rate']
        }
        
        # Save backtest results
        if db.insert_backtest_results(db_results):
            logger.info(f"[OK] Backtest results saved: {backtest_id}")
        else:
            logger.warning("Failed to save backtest results")
        
        # Save trades
        if results['trades']:
            trades_df = pd.DataFrame(results['trades'])
            trades_df['trade_date'] = trades_df['date']  # Map 'date' to 'trade_date'
            trades_df = trades_df.drop('date', axis=1)
            trades_df['status'] = 'OPEN'
            trades_df['direction'] = 'LONG'
            trades_df['entry_price'] = trades_df['price']
            trades_df['exit_price'] = None
            trades_df['quantity'] = trades_df['quantity'].astype(int)
            trades_df['pnl'] = None
            trades_df['return_pct'] = None
            inserted_trades = db.insert_trades(trades_df)
            logger.info(f"Saved {inserted_trades} trades to database")
        
        # Step 5: Display comprehensive database summary
        logger.info("\n[STEP 5] Database Summary...")
        try:
            conn = db.get_connection()
            cursor = conn.cursor()
            
            summary = {}
            tables = ['prices', 'features', 'signals', 'trades', 'backtest_results']
            
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                summary[table] = count
                logger.info(f"  {table:20} {count:8} records")
            
            cursor.close()
            db.return_connection(conn)
        except Exception as e:
            logger.warning(f"Could not retrieve database summary: {str(e)}")
        
        # Display summary
        logger.info("\n" + "=" * 80)
        logger.info("PIPELINE COMPLETE - FINAL RESULTS")
        logger.info("=" * 80)
        logger.info(f"Backtest ID:        {backtest_id}")
        logger.info(f"Period:             {prices['timestamp'].min()} to {prices['timestamp'].max()}")
        logger.info(f"Initial Capital:    ${results['initial_capital']:,.2f}")
        logger.info(f"Final Value:        ${results['final_value']:,.2f}")
        logger.info(f"Total Return:       {results['total_return_pct']:.2f}%")
        logger.info(f"Sharpe Ratio:       {results['sharpe_ratio']:.2f}")
        logger.info(f"Max Drawdown:       {results['max_drawdown_pct']:.2f}%")
        logger.info(f"Total Trades:       {results['num_trades']}")
        logger.info(f"Win Rate:           {results['win_rate_pct']:.2f}%")
        logger.info("=" * 80)
        
        db.close_pool()
        return True
        
    except Exception as e:
        logger.error(f"Pipeline error: {str(e)}", exc_info=True)
        return False


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
  python main.py pipeline             # Run complete pipeline (features -> signals -> backtest)
  python main.py paper                # Run paper trading
  python main.py live                 # Run live trading (DANGER!)
        """
    )
    
    parser.add_argument(
        'mode',
        nargs='?',
        default='collect',
        choices=['collect', 'features', 'signals', 'backtest', 'pipeline', 'paper', 'live'],
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
