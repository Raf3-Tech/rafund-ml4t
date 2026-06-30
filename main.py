"""ML4T System Entry Point — thin CLI dispatcher.

All business logic lives in cli/*.  This file only parses arguments and
dispatches to the appropriate command function.

Usage:
    python main.py collect                          # Collect OHLCV from Binance (incremental)
    python main.py collect --exchange kraken        # Collect from Kraken instead
    python main.py collect --exchange htx           # Collect from HTX instead
    python main.py collect --exchange all           # Collect from all exchanges
    python main.py collect --funding                # Also collect 8h funding rates (Binance)
    python main.py features             # Calculate spread features
    python main.py signals              # Generate signals
    python main.py backtest             # Run evaluation backtest
    python main.py engine               # Walk-forward engine: all strategies × symbols
    python main.py engine --strategy "EMA Crossover" --symbol BTC/USDT
    python main.py leaderboard          # Print all strategies x symbols, qualifying or not, with reasons
    python main.py leaderboard --tier qualifying   # Only show ones that pass
    python main.py train-classifier     # Train regime ML classifier
    python main.py validate             # Walk-forward OOS validation
    python main.py retrain              # Retrain + validate models
    python main.py drift                # Feature-drift check for production models
    python main.py research             # Closed-loop research pipeline
    python main.py research --dry-run   # Gate only, skip engine run
    python main.py research --top-n 5
    python main.py pipeline             # Collect → backtest → persist
    python main.py bootstrap            # Alias for pipeline
    python main.py paper                # Paper trading
    python main.py live                 # Live trading (DANGER — not implemented)
"""

import argparse
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from config.logging_config import get_logger

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            log_dir / "ml4t.log",
            maxBytes=50 * 1024 * 1024,  # 50 MB per file
            backupCount=7,              # keep 7 rotated files (~350 MB max)
        ),
        logging.StreamHandler(),
    ],
)
logger = get_logger(__name__)


def _parse_iso_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ML4T - Machine Learning for Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="collect",
        choices=[
            "collect", "features", "signals", "backtest", "validate", "retrain",
            "drift", "backfill", "pipeline", "bootstrap", "paper", "live",
            "benchmark", "models", "engine", "leaderboard", "train-classifier",
            "research",
        ],
    )
    parser.add_argument("submode", nargs="?")
    parser.add_argument("--model")
    parser.add_argument("--symbol")
    parser.add_argument("--strategy")
    parser.add_argument("--tier")
    parser.add_argument("--funding", action="store_true")
    parser.add_argument(
        "--exchange",
        choices=["binance", "kraken", "htx", "all"],
        default=None,
        help="Exchange to use. collect: default binance. paper: default all "
             "(runs every exchange in one cycle). live: default $LIVE_EXCHANGE or binance.",
    )
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--top-n", type=int, default=5, dest="top_n")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    parser.add_argument("--timeframe")
    parser.add_argument(
        "--replay-days", type=int, dest="replay_days",
        help="paper mode: replay the paper-trading mechanism over this many days of "
             "already-collected price history instead of running one live cycle.",
    )
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("ML4T System Started — %s  Mode: %s", datetime.now().isoformat(), args.mode.upper())
    logger.info("=" * 80)

    try:
        success: bool

        if args.mode == "collect":
            from cli.collect import (
                collect_data,
                collect_htx_data,
                collect_kraken_data,
                run_collect_funding_cmd,
            )
            exchange = args.exchange or "binance"
            if exchange in ("binance", "all"):
                success = collect_data()
            else:
                success = True
            if success and exchange in ("kraken", "all"):
                success = collect_kraken_data()
            if success and exchange in ("htx", "all"):
                success = collect_htx_data()
            if success and args.funding:
                success = run_collect_funding_cmd()

        elif args.mode == "features":
            from cli.features import calculate_features
            success = calculate_features()

        elif args.mode == "signals":
            from cli.features import generate_signals
            success = generate_signals()

        elif args.mode == "backtest":
            from cli.backtest import run_backtest
            success = run_backtest()

        elif args.mode == "engine":
            from cli.engine import run_engine_cmd
            success = run_engine_cmd(strategy_filter=args.strategy, symbol_filter=args.symbol)

        elif args.mode == "leaderboard":
            from cli.research import run_leaderboard_cmd_wrapper
            success = run_leaderboard_cmd_wrapper(tier=args.tier)

        elif args.mode == "train-classifier":
            from cli.models_cmd import run_train_classifier_cmd
            success = run_train_classifier_cmd()

        elif args.mode == "validate":
            from cli.backtest import run_validation_cmd
            success = run_validation_cmd()

        elif args.mode == "retrain":
            from cli.models_cmd import run_retrain_cmd
            success = run_retrain_cmd()

        elif args.mode == "drift":
            from cli.models_cmd import run_drift_cmd
            success = run_drift_cmd(args.symbol, args.model)

        elif args.mode == "backfill":
            if not args.date_from:
                logger.error("--from is required for backfill")
                return 1
            from cli.collect import run_backfill
            success = run_backfill(
                symbol=args.symbol,
                timeframe=args.timeframe,
                date_from=_parse_iso_date(args.date_from),
                date_to=_parse_iso_date(args.date_to) if args.date_to else datetime.now(timezone.utc),
                all_symbols=args.all,
            )

        elif args.mode == "benchmark":
            if not args.model or not args.symbol:
                logger.error("--model and --symbol are required for benchmark")
                return 1
            from models.predict import benchmark_predict
            benchmark_predict(args.model, args.symbol, n_runs=args.runs)
            success = True

        elif args.mode == "models":
            if args.submode != "status":
                logger.error("Unknown models subcommand. Use: python main.py models status")
                return 1
            from cli.models_cmd import run_model_status
            return run_model_status()

        elif args.mode == "research":
            from cli.research import run_research_cmd
            success = run_research_cmd(
                strategy_filter=args.strategy,
                symbol_filter=args.symbol,
                top_n=args.top_n,
                qualifying_only=bool(args.tier),
                dry_run=args.dry_run,
            )

        elif args.mode in ("pipeline", "bootstrap"):
            from cli.backtest import run_full_pipeline
            success = run_full_pipeline()

        elif args.mode == "paper":
            exchange = None if args.exchange in (None, "all") else args.exchange
            if args.replay_days:
                from cli.backtest import run_paper_backfill
                success = run_paper_backfill(exchange=exchange, lookback_days=args.replay_days)
            else:
                from cli.db import get_db_connection
                from monitoring.startup_status import print_startup_status
                _db = get_db_connection()
                print_startup_status(_db, exchange=exchange)
                _db.close_pool()
                from cli.backtest import run_paper_trading
                success = run_paper_trading(exchange=exchange)

        elif args.mode == "live":
            from cli.backtest import run_live_trading
            if args.exchange == "all":
                logger.error("live mode requires a single exchange — use --exchange binance, kraken, or htx")
                success = False
            else:
                success = run_live_trading(exchange=args.exchange)

        else:
            logger.error("Unknown mode: %s", args.mode)
            success = False

        if success:
            logger.info("[SUCCESS] Operation completed successfully")
            return 0
        else:
            logger.error("[FAILED] Operation failed")
            return 1

    except Exception as e:
        logger.error("Fatal error: %s", str(e), exc_info=True)
        return 1
    finally:
        logger.info("ML4T System shutdown — %s", datetime.now().isoformat())
        logger.info("=" * 80)


if __name__ == "__main__":
    sys.exit(main())
