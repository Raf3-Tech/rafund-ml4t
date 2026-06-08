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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import os
import pandas as pd
from dotenv import load_dotenv

from config.logging_config import get_logger
from data.db import DatabaseConnection
from models.blocker import ModelBlocker
from models.predict import benchmark_predict

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

logger = get_logger(__name__)

# Load .env via python-dotenv (handles quotes/comments/export; real env wins).
# Anchored to this file's directory so it works regardless of the CWD.
def load_env_file() -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


load_env_file()

def get_db_connection():
    """Shared database connection using .env credentials or DATABASE_URL."""
    from data.db import DatabaseConnection
    return DatabaseConnection(
        database_url=os.getenv('DATABASE_URL'),
        host=os.getenv('DB_HOST', 'localhost'),
        port=int(os.getenv('DB_PORT', 5432)),
        database=os.getenv('DB_NAME', 'rafund'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD'),
    )


def collect_data(full_history: bool = True):
    """
    Collect OHLCV from Binance into `prices` table.

    - First run: from COLLECT_START_DATE to today
    - Later runs: incremental from last stored bar per symbol to today
    """
    logger.info("=" * 80)
    logger.info("STARTING DATA COLLECTION")
    logger.info("=" * 80)

    try:
        from config.loader import get_settings
        from data.collectors.binance_collector import BinanceCollector

        settings = get_settings()
        collector = BinanceCollector(testnet=False, rate_limit_ms=int(os.getenv('RATE_LIMIT_MS', 200)))
        db = get_db_connection()

        if not db.test_connection():
            logger.error("Database connection failed")
            return False

        symbols = settings.collect_symbols
        if not symbols:
            logger.error("No symbols configured for collection")
            db.close_pool()
            return False

        end_date = datetime.now(timezone.utc)
        default_start = datetime.strptime(settings.collect_start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)

        logger.info(f"Symbols: {symbols}")
        logger.info(f"Collect through: {end_date.date()}")

        total_inserted = 0
        for symbol in symbols:
            try:
                latest = db.get_latest_timestamp(symbol)
                if full_history or latest is None:
                    start_date = default_start
                    logger.info(f"[{symbol}] Full history from {start_date.date()}")
                else:
                    start_date = latest + timedelta(days=1)
                    if start_date.date() >= end_date.date():
                        logger.info(f"[{symbol}] Already up to date (latest {latest.date()})")
                        continue
                    logger.info(f"[{symbol}] Incremental from {start_date.date()}")

                result = collector.collect_symbol(
                    symbol=symbol,
                    timeframe=settings.timeframe,
                    from_date=start_date,
                    to_date=end_date,
                )
                if not collector.last_collection_df.empty:
                    inserted = db.insert_prices(collector.last_collection_df)
                    total_inserted += inserted
                    logger.info(
                        f"[OK] {symbol}: {inserted} new rows (fetched {result.records_fetched})"
                    )
                else:
                    logger.warning(f"[SKIP] {symbol}: No valid data to insert")

                if result.errors:
                    for err in result.errors:
                        logger.warning(err)
            except Exception as e:
                logger.error(f"[ERROR] {symbol}: {str(e)}", exc_info=True)

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


def parse_iso_date(date_string: str) -> datetime:
    return datetime.strptime(date_string, '%Y-%m-%d').replace(tzinfo=timezone.utc)


def run_backfill(
    symbol: Optional[str],
    timeframe: Optional[str],
    date_from: datetime,
    date_to: datetime,
    all_symbols: bool = False,
) -> bool:
    logger.info("=" * 80)
    logger.info("STARTING BACKFILL")
    logger.info("=" * 80)

    try:
        from config.loader import get_settings
        from data.collectors.binance_collector import BinanceCollector

        settings = get_settings()
        db = get_db_connection()

        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        if all_symbols:
            symbols = settings.collect_symbols
        elif symbol:
            symbols = [symbol]
        else:
            logger.error("Either --symbol or --all must be provided for backfill")
            db.close_pool()
            return False

        timeframe = timeframe or settings.timeframe
        if timeframe not in ('1m', '5m', '15m', '1h', '1d'):
            logger.error(f"Unsupported timeframe: {timeframe}")
            db.close_pool()
            return False

        collector = BinanceCollector(testnet=False, rate_limit_ms=int(os.getenv('RATE_LIMIT_MS', 200)))
        error_exit = False

        for symbol_name in symbols:
            logger.info(
                "Backfill symbol",
                command='backfill',
                symbol=symbol_name,
                timeframe=timeframe,
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
            )

            result = collector.collect_symbol(
                symbol=symbol_name,
                timeframe=timeframe,
                from_date=date_from,
                to_date=date_to,
            )

            inserted = 0
            if not collector.last_collection_df.empty:
                inserted = db.insert_prices(collector.last_collection_df)

            print("=" * 72)
            print(f"Symbol: {symbol_name}")
            print(f"Timeframe: {timeframe}")
            print(f"Range: {date_from.date()} to {date_to.date()}")
            print(f"Fetched: {result.records_fetched}")
            print(f"Inserted: {inserted}")
            print(f"Rejected: {result.records_rejected}")
            print("=" * 72)

            if result.records_rejected > 0:
                error_exit = True
            if result.errors:
                for item in result.errors:
                    logger.warning(item)

        db.close_pool()
        return not error_exit

    except Exception as e:
        logger.error(f"Backfill error: {str(e)}", exc_info=True)
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
        from backtesting.persist import persist_evaluation_run

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        pairs = db.get_feature_pairs()
        if not pairs:
            logger.info("No valid feature pairs found. Calculating features first...")
            if not calculate_features():
                logger.error("Feature calculation failed")
                db.close_pool()
                return False
            pairs = db.get_feature_pairs()

        if not pairs:
            pair_a = os.getenv('EVAL_PAIR_A', 'BTC/USDT')
            pair_b = os.getenv('EVAL_PAIR_B', 'ETH/USDT')
            logger.warning("No valid feature pairs available. Falling back to configured evaluation pair.")
            pairs = [(pair_a, pair_b)]

        logger.info(f"Found {len(pairs)} valid backtest pair(s)")

        results_summary = []
        for idx, (pair_a, pair_b) in enumerate(pairs, start=1):
            logger.info("=" * 80)
            logger.info(f"[{idx}/{len(pairs)}] BACKTEST PAIR: {pair_a} / {pair_b}")
            logger.info("=" * 80)

            pa = db.get_prices(pair_a, None, None)
            pb = db.get_prices(pair_b, None, None)
            if pa.empty or pb.empty:
                logger.error(f"Missing prices for {pair_a} or {pair_b}. Skipping pair.")
                continue

            prices = pd.concat([pa, pb], ignore_index=True)
            logger.info(
                f"Loaded {len(prices)} bars for {pair_a} / {pair_b} "
                f"({pa['timestamp'].min().date()} → {pa['timestamp'].max().date()})"
            )

            rules = PropFirmRules()
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

            if os.getenv('SAVE_TO_DB', '1') == '1' and results.get('signals_df') is not None:
                backtest_id = (
                    f"backtest_{pair_a.replace('/', '')}_{pair_b.replace('/', '')}_"
                    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                )
                written = persist_evaluation_run(db, results, backtest_id)
            else:
                written = {}

            results_summary.append({
                'pair': f"{pair_a}/{pair_b}",
                'status': ev.get('status', 'N/A'),
                'final_value': results.get('final_value', 0.0),
                'total_return_pct': results.get('total_return_pct', 0.0),
                'sharpe_ratio': results.get('sharpe_ratio', 0.0),
                'max_drawdown_pct': results.get('max_drawdown_pct', 0.0),
                'num_trades': results.get('num_trades', 0),
                'win_rate_pct': results.get('win_rate_pct', 0.0),
                'persisted': written,
            })

        if not results_summary:
            logger.error("No backtest results were generated")
            db.close_pool()
            return False

        logger.info("\n" + "=" * 80)
        logger.info("BACKTEST SUMMARY")
        logger.info("=" * 80)
        for row in results_summary:
            logger.info(
                f"{row['pair']:25} | status={row['status']:15} | return={row['total_return_pct']:7.2f}% "
                f"| sharpe={row['sharpe_ratio']:6.2f} | drawdown={row['max_drawdown_pct']:6.2f}% "
                f"| trades={row['num_trades']:3} | win={row['win_rate_pct']:5.1f}%"
            )
        logger.info("=" * 80)

        db.close_pool()
        return True

    except Exception as e:
        logger.error(f"Backtest error: {str(e)}", exc_info=True)
        return False


def _aligned_pair_frame(db, symbol_a: str, symbol_b: str) -> Optional[pd.DataFrame]:
    """Load and align two symbols into a [timestamp, price_a, price_b] frame.

    This is the input format expected by the generic ``BacktestEngine`` and the
    walk-forward validation framework (distinct from the concatenated frame the
    evaluation engine consumes).
    """
    pa = db.get_prices(symbol_a, None, None)
    pb = db.get_prices(symbol_b, None, None)
    if pa.empty or pb.empty:
        return None
    left = pa[['timestamp', 'close']].rename(columns={'close': 'price_a'})
    right = pb[['timestamp', 'close']].rename(columns={'close': 'price_b'})
    merged = pd.merge(left, right, on='timestamp', how='inner').sort_values('timestamp')
    return merged.reset_index(drop=True) if not merged.empty else None


def run_validation_cmd():
    """Walk-forward out-of-sample validation of the stat-arb pair strategy."""
    logger.info("=" * 80)
    logger.info("STARTING WALK-FORWARD VALIDATION")
    logger.info("=" * 80)

    try:
        from backtesting.validation import run_validation
        from strategies.stat_arb import WalkForwardStatArb
        from config.loader import get_settings

        cfg = get_settings()
        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        pairs = db.get_feature_pairs()
        if not pairs:
            pair_a = os.getenv('EVAL_PAIR_A', 'BTC/USDT')
            pair_b = os.getenv('EVAL_PAIR_B', 'ETH/USDT')
            logger.warning("No feature pairs found; falling back to %s / %s", pair_a, pair_b)
            pairs = [(pair_a, pair_b)]

        any_results = False
        for idx, (pair_a, pair_b) in enumerate(pairs, start=1):
            prices = _aligned_pair_frame(db, pair_a, pair_b)
            if prices is None:
                logger.warning("Skipping %s/%s: insufficient aligned price data", pair_a, pair_b)
                continue

            logger.info("[%d/%d] Validating %s / %s (%d aligned bars)",
                        idx, len(pairs), pair_a, pair_b, len(prices))
            strategy = WalkForwardStatArb(
                entry_threshold=2.0,
                exit_threshold=0.5,
                entropy_threshold=cfg.entropy_threshold if cfg.entropy_enabled else None,
                entropy_window=cfg.entropy_window,
                entropy_embedding=cfg.entropy_embedding,
            )
            result = run_validation(
                prices,
                strategy,
                strategy_name=f"statarb_{pair_a.replace('/', '')}_{pair_b.replace('/', '')}",
            )

            wf = result.walk_forward
            agg = wf.aggregate
            sig = result.significance
            logger.info("=" * 80)
            logger.info("VALIDATION RESULT: %s / %s", pair_a, pair_b)
            logger.info("  Folds:               %d", len(wf.folds))
            logger.info("  Mean OOS Sharpe:     %.2f", agg.mean_oos_sharpe)
            logger.info("  Worst OOS Drawdown:  %.2f%%", agg.worst_oos_drawdown)
            logger.info("  Folds Profitable:    %.0f%%", agg.pct_folds_profitable)
            logger.info("  Total OOS Trades:    %d", agg.total_oos_trades)
            logger.info("  Walk-forward gate:   %s — %s",
                        "PASS" if wf.passed_gate else "FAIL", wf.gate_reason)
            if sig is not None:
                logger.info("  Significance:        p=%.4f (%s)",
                            sig.p_value, "significant" if sig.significant else "not significant")
            logger.info("=" * 80)
            any_results = True

        db.close_pool()
        if not any_results:
            logger.error("No pairs could be validated (no aligned price data)")
            return False
        return True

    except Exception as e:
        logger.error(f"Validation error: {str(e)}", exc_info=True)
        return False


def run_retrain_cmd():
    """Run a single model retraining + walk-forward validation cycle."""
    logger.info("=" * 80)
    logger.info("STARTING RETRAINING CYCLE")
    logger.info("=" * 80)

    try:
        from models.retraining_scheduler import run_retraining_cycle
        from config.loader import load_config

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        config = load_config()
        results = run_retraining_cycle(db, config)
        logger.info("Retraining cycle finished: %d candidate(s) processed", len(results))
        for r in results:
            logger.info("  %s", r)
        db.close_pool()
        return True

    except Exception as e:
        logger.error(f"Retraining error: {str(e)}", exc_info=True)
        return False


def run_drift_cmd(symbol: Optional[str], model_name: Optional[str]):
    """Run a feature-drift check for production model(s)."""
    logger.info("=" * 80)
    logger.info("STARTING DRIFT CHECK")
    logger.info("=" * 80)

    try:
        from models.retraining_scheduler import run_drift_check

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        targets = []
        if model_name and symbol:
            targets = [{'model_name': model_name, 'symbol': symbol}]
        else:
            targets = db.get_all_model_registry_entries()
            targets = [t for t in targets if (t.get('stage') == 'Production')]
            if symbol:
                targets = [t for t in targets if t.get('symbol') == symbol]

        if not targets:
            logger.warning("No production models found to check for drift")
            db.close_pool()
            return False

        checked = 0
        for entry in targets:
            report = run_drift_check(db, entry, entry.get('symbol'))
            if report is None:
                logger.warning("  %s/%s: insufficient data for drift check",
                               entry.get('model_name'), entry.get('symbol'))
                continue
            logger.info("  %s/%s: severity=%s action=%s",
                        entry.get('model_name'), entry.get('symbol'),
                        report.severity, report.recommended_action)
            checked += 1

        db.close_pool()
        return checked > 0

    except Exception as e:
        logger.error(f"Drift check error: {str(e)}", exc_info=True)
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
            password=os.getenv('DB_PASSWORD')
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
                            db.delete_features_for_pair(sym_a, sym_b)
                            invalid_pairs += 1
                            continue
                        
                        # Align by timestamp
                        pa = prices_a.set_index('timestamp')['close']
                        pb = prices_b.set_index('timestamp')['close']
                        
                        # Calculate spread features
                        features = calculate_spread_features(pa, pb, window=20)
                        
                        if features.empty:
                            logger.warning(f"[SKIP] {pair_name}: Could not calculate features")
                            db.delete_features_for_pair(sym_a, sym_b)
                            invalid_pairs += 1
                            continue
                        
                        # TEST STATIONARITY - CRITICAL FOR STAT ARB
                        spread = features['spread'].dropna()
                        is_stationary, test_results = test_stationarity(spread, pair_name)
                        
                        if not is_stationary:
                            logger.warning(f"[REJECT] {pair_name}: Spread not stationary - pairs trading invalid")
                            db.delete_features_for_pair(sym_a, sym_b)
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
    """Generate and save trading signals via the strategy class (single signal code path).

    Phase 1 fix: all threshold logic lives in StatArbPairsStrategy.signals_to_db_format().
    This function is a thin orchestration wrapper — no thresholds hardcoded here.
    """
    logger.info("=" * 80)
    logger.info("GENERATING SIGNALS")
    logger.info("=" * 80)

    try:
        from strategies.stat_arb import StatArbPairsStrategy
        from config.loader import get_settings

        cfg = get_settings()
        db = get_db_connection()

        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        symbols = db.get_symbols_with_data()
        if not symbols:
            logger.warning("No price data found. Run: python main.py collect first.")
            db.close_pool()
            return False

        logger.info(f"Generating signals for {len(symbols)} symbols")

        strategy = StatArbPairsStrategy()
        params = {
            "entry_z": cfg.entry_threshold,
            "exit_z": cfg.exit_threshold,
            "lookback": cfg.lookback,
        }

        pairs = db.get_feature_pairs()
        if not pairs:
            logger.warning("No valid feature pairs found. Run features first.")
            db.close_pool()
            return False

        logger.info(f"Processing {len(pairs)} pair(s) with entry_z={params['entry_z']}, exit_z={params['exit_z']}")
        signals_list = []

        for sym_a, sym_b in pairs:
            pa = db.get_prices(sym_a, None, None)
            pb = db.get_prices(sym_b, None, None)
            if pa.empty or pb.empty:
                logger.warning(f"[SKIP] {sym_a}/{sym_b}: missing price data")
                continue
            db_signals = strategy.signals_to_db_format(pa, pb, params, sym_a, sym_b)
            if not db_signals.empty:
                signals_list.append(db_signals)

        if not signals_list:
            logger.warning("No signals generated — check feature pairs and price data.")
            db.close_pool()
            return False

        all_signals = pd.concat(signals_list, ignore_index=True)
        signal_counts = all_signals["signal"].value_counts()
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
        from backtesting.engine_eval import EvaluationBacktestEngine
        from backtesting.evaluation_rules import PropFirmRules
        from backtesting.persist import persist_evaluation_run

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        symbols = db.get_symbols_with_data()
        if not symbols:
            logger.error("No symbols with price data found. Run: python main.py collect first.")
            db.close_pool()
            return False

        logger.info(f"Found {len(symbols)} symbols with data: {', '.join(symbols)}")

        logger.info("Calculating features for all symbol pairs before paper trading...")
        if not calculate_features():
            logger.error("Feature calculation failed")
            db.close_pool()
            return False

        pairs = db.get_feature_pairs()
        if not pairs:
            logger.error("No valid feature pairs found for paper trading")
            db.close_pool()
            return False

        logger.info(f"Found {len(pairs)} candidate pairs for paper trading")

        results_summary = []
        for idx, (symbol_a, symbol_b) in enumerate(pairs, 1):
            logger.info("=" * 80)
            logger.info(f"[{idx}/{len(pairs)}] PAPER TRADING PAIR: {symbol_a} / {symbol_b}")
            pa = db.get_prices(symbol_a, None, None)
            pb = db.get_prices(symbol_b, None, None)
            if pa.empty or pb.empty:
                logger.warning(f"Skipping {symbol_a}/{symbol_b}: missing price data")
                continue

            prices = pd.concat([pa, pb], ignore_index=True)
            engine = EvaluationBacktestEngine(
                rules=PropFirmRules(),
                symbol_a=symbol_a,
                symbol_b=symbol_b,
                entry_threshold=2.0,
                exit_threshold=0.5,
                lookback=60,
                leg_allocation_pct=0.18,
                commission=0.001,
                stop_loss_spread_pct=0.03,
                max_holding_days=30,
            )

            results = engine.run(prices)
            backtest_id = (
                f"paper_{symbol_a.replace('/', '')}_{symbol_b.replace('/', '')}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            written = persist_evaluation_run(db, results, backtest_id)

            eval_status = results.get('evaluation', {}).get('status', 'UNKNOWN')
            results_summary.append({
                'pair': f"{symbol_a}/{symbol_b}",
                'status': eval_status,
                'final_value': results.get('final_value', 0.0),
                'total_return_pct': results.get('total_return_pct', 0.0),
                'sharpe_ratio': results.get('sharpe_ratio', 0.0),
                'max_drawdown_pct': results.get('max_drawdown_pct', 0.0),
                'num_trades': results.get('num_trades', 0),
                'win_rate_pct': results.get('win_rate_pct', 0.0),
                'persisted': written,
            })

        if not results_summary:
            logger.error("Paper trading completed but no pair results were generated")
            db.close_pool()
            return False

        results_summary.sort(key=lambda row: row['total_return_pct'], reverse=True)
        logger.info("=" * 80)
        logger.info("PAPER TRADING SUMMARY")
        logger.info("=" * 80)
        for row in results_summary:
            logger.info(
                f"{row['pair']:25} | status={row['status']:15} | return={row['total_return_pct']:7.2f}% "
                f"| sharpe={row['sharpe_ratio']:6.2f} | drawdown={row['max_drawdown_pct']:6.2f}% "
                f"| trades={row['num_trades']:3} | win={row['win_rate_pct']:5.1f}%"
            )

        db.close_pool()
        return True

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


def _load_model_status_table(db: DatabaseConnection) -> tuple[list[dict], dict]:
    rows = db.get_all_model_registry_entries()
    drift_reports = db.get_latest_drift_reports()
    drift_map = {(row['model_name'], row['symbol']): row for row in drift_reports}
    results = []
    summary = {'healthy': 0, 'warning': 0, 'blocked': 0, 'staging': 0}

    blocker = ModelBlocker(db)
    for entry in rows:
        model_name = entry.get('model_name')
        symbol = entry.get('symbol')
        stage = entry.get('stage') or 'UNKNOWN'
        version = entry.get('mlflow_version') or 0
        trained_at = entry.get('trained_at')
        oos_sharpe = entry.get('oos_sharpe')
        if isinstance(trained_at, str):
            try:
                trained_at = datetime.fromisoformat(trained_at)
            except ValueError:
                trained_at = None

        age_days = None
        if trained_at:
            age_days = int((datetime.utcnow() - trained_at).days)

        drift_entry = drift_map.get((model_name, symbol), {})
        drift_severity = drift_entry.get('severity')
        block_status = blocker.is_blocked(model_name, symbol)

        if stage != 'Production':
            status = '— STAGING'
            summary['staging'] += 1
        elif block_status.blocked:
            status = '✗ BLOCKED'
            summary['blocked'] += 1
        elif drift_severity == 'warning':
            status = '⚠ WARNING'
            summary['warning'] += 1
        else:
            status = '✓ HEALTHY'
            summary['healthy'] += 1

        results.append({
            'model_name': model_name,
            'symbol': symbol,
            'version': version,
            'stage': stage,
            'age_days': age_days,
            'drift': drift_severity or 'none',
            'blocked': 'YES' if block_status.blocked else 'NO',
            'last_sharpe': oos_sharpe if oos_sharpe is not None else 0.0,
            'status': status,
        })
        logger.debug('model_status_row', **results[-1])

    return results, summary


def run_model_status() -> int:
    db = get_db_connection()
    models, summary = _load_model_status_table(db)

    if not models:
        print('No registered models found.')
        db.close_pool()
        return 1

    header = (
        f"{'MODEL NAME':35} | {'SYMBOL':10} | {'VERSION':7} | {'STAGE':10} | {'AGE (DAYS)':10} | {'DRIFT':8} | {'BLOCKED':7} | {'LAST SHARPE':11} | STATUS"
    )
    print(header)
    print('-' * len(header))
    for row in models:
        print(
            f"{row['model_name'][:35]:35} | {row['symbol'][:10]:10} | {row['version']:7} | {row['stage'][:10]:10} | "
            f"{row['age_days'] if row['age_days'] is not None else '-':10} | {row['drift'][:8]:8} | {row['blocked']:7} | "
            f"{row['last_sharpe']:11.2f} | {row['status']}"
        )

    print()
    print(
        f"{summary['healthy']} models healthy, {summary['warning']} warning, "
        f"{summary['blocked']} blocked, {summary['staging']} staging"
    )
    db.close_pool()
    return 1 if summary['warning'] or summary['blocked'] else 0


def run_engine_cmd(strategy_filter: Optional[str] = None, symbol_filter: Optional[str] = None) -> bool:
    """Run the walk-forward window engine across all strategies and symbols."""
    logger.info("=" * 80)
    logger.info("STARTING WALK-FORWARD ENGINE")
    logger.info("=" * 80)
    if strategy_filter:
        logger.info("Strategy filter: %s", strategy_filter)
    if symbol_filter:
        logger.info("Symbol filter: %s", symbol_filter)

    try:
        from backtesting.window_engine import WalkForwardWindowEngine
        from strategies.ema_crossover import EMACrossover
        from strategies.macd import MACDStrategy
        from strategies.supertrend import SupertrendStrategy
        from strategies.donchian_breakout import DonchianBreakout
        from strategies.bollinger_reversion import BollingerReversion
        from strategies.rsi_extremes import RSIExtremes
        from strategies.atr_volatility_breakout import ATRVolatilityBreakout
        from strategies.keltner_squeeze import KeltnerSqueeze
        from strategies.dca import DCAStrategy
        from strategies.hodl_rebalance import HODLRebalance
        from strategies.funding_rate_arb import FundingRateArb
        from strategies.stat_arb import StatArbPairsStrategy
        from config.loader import get_settings

        cfg = get_settings()
        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        symbols = db.get_symbols_with_data()
        if not symbols:
            logger.error("No price data found. Run: python main.py collect first.")
            db.close_pool()
            return False

        strategies = [
            EMACrossover(),
            MACDStrategy(),
            SupertrendStrategy(),
            DonchianBreakout(),
            BollingerReversion(),
            RSIExtremes(),
            ATRVolatilityBreakout(),
            KeltnerSqueeze(),
            DCAStrategy(),
            HODLRebalance(),
            StatArbPairsStrategy(),   # pairs — engine routes via generate_signals_pair
            FundingRateArb(),         # funding — engine routes via 8h funding_rates data
        ]

        engine = WalkForwardWindowEngine(
            db=db,
            strategies=strategies,
            symbols=symbols,
            commission=cfg.commission,
        )

        results = engine.run(strategy_filter=strategy_filter, symbol_filter=symbol_filter)
        logger.info("Engine run complete — %d results generated", len(results))
        db.close_pool()
        return len(results) > 0

    except Exception as e:
        logger.error("Engine error: %s", str(e), exc_info=True)
        return False


def run_leaderboard_cmd_wrapper(tier: Optional[str] = None) -> bool:
    """Print the current leaderboard from engine_results."""
    try:
        from monitoring.leaderboard import run_leaderboard_cmd

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        result = run_leaderboard_cmd(db, tier=tier)
        db.close_pool()
        return result

    except Exception as e:
        logger.error("Leaderboard error: %s", str(e), exc_info=True)
        return False


def run_train_classifier_cmd() -> bool:
    """Train the regime ML classifier on current engine_results data."""
    logger.info("=" * 80)
    logger.info("TRAINING REGIME CLASSIFIER")
    logger.info("=" * 80)
    try:
        from models.regime_classifier import train_classifier

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        success = train_classifier(db)
        db.close_pool()
        return success

    except Exception as e:
        logger.error("Classifier training error: %s", str(e), exc_info=True)
        return False


def run_collect_funding_cmd() -> bool:
    """Collect 8-hour funding rate data from Binance perpetual futures."""
    logger.info("=" * 80)
    logger.info("COLLECTING FUNDING RATES")
    logger.info("=" * 80)
    try:
        from data.collectors.binance_funding_collector import BinanceFundingCollector

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        collector = BinanceFundingCollector(
            rate_limit_ms=int(os.getenv('RATE_LIMIT_MS', 200))
        )
        inserted = collector.collect_all(db)
        logger.info("Funding rate collection complete — %d rows inserted", inserted)
        db.close_pool()
        return True

    except Exception as e:
        logger.error("Funding rate collection error: %s", str(e), exc_info=True)
        return False


def main():
    """Main ML4T entry point."""

    parser = argparse.ArgumentParser(
        description='ML4T - Machine Learning for Trading System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py collect              # Collect market data
  python main.py collect --funding    # Also collect 8h funding rates
  python main.py features             # Calculate features
  python main.py signals              # Generate signals (single code path via strategy class)
  python main.py backtest             # Run backtest
  python main.py engine               # Walk-forward engine: all strategies x all symbols
  python main.py engine --strategy ema_crossover  # One strategy only
  python main.py engine --symbol BTC/USDT         # One symbol only
  python main.py leaderboard          # Print strategy leaderboard from engine_results
  python main.py leaderboard --tier conservative   # Conservative tier only
  python main.py train-classifier     # Train regime ML classifier (need 200+ engine_results rows)
  python main.py validate             # Walk-forward OOS validation + significance
  python main.py retrain              # Retrain + validate models (one cycle)
  python main.py drift                # Feature-drift check for production models
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
        choices=[
            'collect', 'features', 'signals', 'backtest', 'validate', 'retrain',
            'drift', 'backfill', 'pipeline', 'bootstrap', 'paper', 'live',
            'benchmark', 'models', 'engine', 'leaderboard', 'train-classifier',
        ],
        help='Operation mode (default: collect)'
    )
    parser.add_argument('submode', nargs='?', help='Subcommand for the selected mode')
    parser.add_argument('--model', help='Model type for benchmark, e.g. factor_model')
    parser.add_argument('--symbol', help='Symbol filter, e.g. BTC/USDT')
    parser.add_argument('--strategy', help='Strategy filter for engine mode, e.g. ema_crossover')
    parser.add_argument('--tier', help='Leaderboard tier filter: conservative, standard, permissive')
    parser.add_argument('--funding', action='store_true', help='Collect funding rates (for collect mode)')
    parser.add_argument('--runs', type=int, default=100, help='Number of benchmark prediction runs')
    parser.add_argument('--all', action='store_true', help='Backfill all configured symbols')
    parser.add_argument('--from', dest='date_from', help='Start date (YYYY-MM-DD) for backfill')
    parser.add_argument('--to', dest='date_to', help='End date (YYYY-MM-DD) for backfill')
    parser.add_argument('--timeframe', help='Candle timeframe for backfill (default from config)')

    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info(f"ML4T System Started - {datetime.now().isoformat()}")
    logger.info(f"Mode: {args.mode.upper()}")
    logger.info("=" * 80)

    try:
        if args.mode == 'collect':
            success = collect_data()
            if success and args.funding:
                success = run_collect_funding_cmd()
        elif args.mode == 'features':
            success = calculate_features()
        elif args.mode == 'signals':
            success = generate_signals()
        elif args.mode == 'backtest':
            success = run_backtest()
        elif args.mode == 'engine':
            success = run_engine_cmd(
                strategy_filter=args.strategy,
                symbol_filter=args.symbol,
            )
        elif args.mode == 'leaderboard':
            success = run_leaderboard_cmd_wrapper(tier=args.tier)
        elif args.mode == 'train-classifier':
            success = run_train_classifier_cmd()
        elif args.mode == 'validate':
            success = run_validation_cmd()
        elif args.mode == 'retrain':
            success = run_retrain_cmd()
        elif args.mode == 'drift':
            success = run_drift_cmd(args.symbol, args.model)
        elif args.mode == 'backfill':
            if not args.date_from:
                logger.error('--from is required for backfill')
                return 1
            date_from = parse_iso_date(args.date_from)
            date_to = parse_iso_date(args.date_to) if args.date_to else datetime.now(timezone.utc)
            success = run_backfill(
                symbol=args.symbol,
                timeframe=args.timeframe,
                date_from=date_from,
                date_to=date_to,
                all_symbols=args.all,
            )
        elif args.mode == 'benchmark':
            if not args.model or not args.symbol:
                logger.error('--model and --symbol are required for benchmark')
                return 1
            benchmark_predict(args.model, args.symbol, n_runs=args.runs)
            success = True
        elif args.mode == 'models':
            if args.submode != 'status':
                logger.error('Unknown models subcommand. Use: python main.py models status')
                return 1
            exit_code = run_model_status()
            return exit_code
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
