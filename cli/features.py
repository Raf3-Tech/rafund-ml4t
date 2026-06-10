"""CLI commands: feature calculation and signal generation."""

from __future__ import annotations

import os

import pandas as pd

from config.logging_config import get_logger

logger = get_logger(__name__)


def calculate_features() -> bool:
    """Calculate and save spread features for all symbol pairs."""
    logger.info("=" * 80)
    logger.info("CALCULATING FEATURES")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from features.price_features import (
            calculate_spread_features,
            test_stationarity,
        )

        db = get_db_connection()
        symbols = db.get_symbols_with_data()
        if not symbols:
            logger.error("No symbols found in database")
            db.close_pool()
            return False

        total_features = valid_pairs = invalid_pairs = 0

        for i, sym_a in enumerate(symbols):
            for j, sym_b in enumerate(symbols):
                if i >= j:
                    continue
                pair_name = f"{sym_a}/{sym_b}"
                try:
                    prices_a = db.get_prices(sym_a, None, None)
                    prices_b = db.get_prices(sym_b, None, None)
                    if prices_a.empty or prices_b.empty:
                        db.delete_features_for_pair(sym_a, sym_b)
                        invalid_pairs += 1
                        continue

                    pa = prices_a.set_index("timestamp")["close"]
                    pb = prices_b.set_index("timestamp")["close"]
                    features = calculate_spread_features(pa, pb, window=20)
                    if features.empty:
                        db.delete_features_for_pair(sym_a, sym_b)
                        invalid_pairs += 1
                        continue

                    spread = features["spread"].dropna()
                    is_stationary, _ = test_stationarity(spread, pair_name)
                    if not is_stationary:
                        db.delete_features_for_pair(sym_a, sym_b)
                        invalid_pairs += 1
                        continue

                    valid_pairs += 1
                    feature_df = pd.DataFrame({
                        "symbol_a": sym_a,
                        "symbol_b": sym_b,
                        "timestamp": features.index,
                        "spread": features["spread"].values,
                        "spread_mean": features["spread_mean"].values,
                        "spread_std": features["spread_std"].values,
                        "z_score": features["z_score"].values,
                        "hedge_ratio": features["hedge_ratio"].values,
                    })
                    inserted = db.insert_features(feature_df)
                    total_features += inserted
                except Exception as e:
                    logger.warning(f"[ERROR] {pair_name}: {str(e)}")
                    invalid_pairs += 1

        logger.info("Valid pairs: %d  Invalid: %d  Features inserted: %d",
                    valid_pairs, invalid_pairs, total_features)
        db.close_pool()
        return True
    except Exception as e:
        logger.error(f"Feature calculation error: {str(e)}", exc_info=True)
        return False


def generate_signals() -> bool:
    """Generate trading signals via the strategy class (single signal code path)."""
    logger.info("=" * 80)
    logger.info("GENERATING SIGNALS")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from config.loader import get_settings
        from strategies.stat_arb import StatArbPairsStrategy

        cfg = get_settings()
        db = get_db_connection()

        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        pairs = db.get_feature_pairs()
        if not pairs:
            logger.warning("No valid feature pairs found. Run features first.")
            db.close_pool()
            return False

        strategy = StatArbPairsStrategy()
        params = {
            "entry_z": cfg.entry_threshold,
            "exit_z": cfg.exit_threshold,
            "lookback": cfg.lookback,
        }
        signals_list = []
        for sym_a, sym_b in pairs:
            pa = db.get_prices(sym_a, None, None)
            pb = db.get_prices(sym_b, None, None)
            if pa.empty or pb.empty:
                continue
            db_signals = strategy.signals_to_db_format(pa, pb, params, sym_a, sym_b)
            if not db_signals.empty:
                signals_list.append(db_signals)

        if not signals_list:
            logger.warning("No signals generated.")
            db.close_pool()
            return False

        all_signals = pd.concat(signals_list, ignore_index=True)
        inserted = db.insert_signals(all_signals)
        logger.info("Generated and saved %d signals", inserted)
        db.close_pool()
        return True
    except Exception as e:
        logger.error(f"Signal generation error: {str(e)}", exc_info=True)
        return False
