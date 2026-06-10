"""CLI commands: model retraining, drift checking, status, classifier training."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from config.logging_config import get_logger

logger = get_logger(__name__)


def _load_model_status_table(db) -> tuple[list[dict], dict]:
    from models.blocker import ModelBlocker

    rows = db.get_all_model_registry_entries()
    drift_reports = db.get_latest_drift_reports()
    drift_map = {(r["model_name"], r["symbol"]): r for r in drift_reports}
    results = []
    summary = {"healthy": 0, "warning": 0, "blocked": 0, "staging": 0}
    blocker = ModelBlocker(db)

    for entry in rows:
        model_name = entry.get("model_name")
        symbol = entry.get("symbol")
        stage = entry.get("stage") or "UNKNOWN"
        trained_at = entry.get("trained_at")
        oos_sharpe = entry.get("oos_sharpe")

        if isinstance(trained_at, str):
            try:
                trained_at = datetime.fromisoformat(trained_at)
            except ValueError:
                trained_at = None
        age_days = int((datetime.utcnow() - trained_at).days) if trained_at else None

        drift_entry = drift_map.get((model_name, symbol), {})
        drift_severity = drift_entry.get("severity")
        block_status = blocker.is_blocked(model_name, symbol)

        if stage != "Production":
            status = "— STAGING"
            summary["staging"] += 1
        elif block_status.blocked:
            status = "✗ BLOCKED"
            summary["blocked"] += 1
        elif drift_severity == "warning":
            status = "⚠ WARNING"
            summary["warning"] += 1
        else:
            status = "✓ HEALTHY"
            summary["healthy"] += 1

        results.append({
            "model_name": model_name,
            "symbol": symbol,
            "version": entry.get("mlflow_version") or 0,
            "stage": stage,
            "age_days": age_days,
            "drift": drift_severity or "none",
            "blocked": "YES" if block_status.blocked else "NO",
            "last_sharpe": oos_sharpe if oos_sharpe is not None else 0.0,
            "status": status,
        })

    return results, summary


def run_model_status() -> int:
    from cli.db import get_db_connection

    db = get_db_connection()
    models, summary = _load_model_status_table(db)

    if not models:
        print("No registered models found.")
        db.close_pool()
        return 1

    header = (
        f"{'MODEL NAME':35} | {'SYMBOL':10} | {'VER':5} | {'STAGE':10} | "
        f"{'AGE (D)':7} | {'DRIFT':8} | {'BLOCKED':7} | {'SHARPE':7} | STATUS"
    )
    print(header)
    print("-" * len(header))
    for row in models:
        print(
            f"{row['model_name'][:35]:35} | {row['symbol'][:10]:10} | {row['version']:5} | "
            f"{row['stage'][:10]:10} | {str(row['age_days'] or '-'):7} | "
            f"{row['drift'][:8]:8} | {row['blocked']:7} | {row['last_sharpe']:7.2f} | {row['status']}"
        )

    print(
        f"\n{summary['healthy']} healthy, {summary['warning']} warning, "
        f"{summary['blocked']} blocked, {summary['staging']} staging"
    )
    db.close_pool()
    return 1 if summary["warning"] or summary["blocked"] else 0


def run_retrain_cmd() -> bool:
    logger.info("=" * 80)
    logger.info("STARTING RETRAINING CYCLE")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from config.loader import load_config
        from models.retraining_scheduler import run_retraining_cycle

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


def run_drift_cmd(symbol: Optional[str], model_name: Optional[str]) -> bool:
    logger.info("=" * 80)
    logger.info("STARTING DRIFT CHECK")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
        from models.retraining_scheduler import run_drift_check

        db = get_db_connection()
        if not db.test_connection():
            logger.error("Database connection failed")
            db.close_pool()
            return False

        if model_name and symbol:
            targets = [{"model_name": model_name, "symbol": symbol}]
        else:
            targets = [t for t in db.get_all_model_registry_entries() if t.get("stage") == "Production"]
            if symbol:
                targets = [t for t in targets if t.get("symbol") == symbol]

        if not targets:
            logger.warning("No production models found to check for drift")
            db.close_pool()
            return False

        checked = 0
        for entry in targets:
            report = run_drift_check(db, entry, entry.get("symbol"))
            if report is None:
                continue
            logger.info("  %s/%s: severity=%s action=%s",
                        entry.get("model_name"), entry.get("symbol"),
                        report.severity, report.recommended_action)
            checked += 1

        db.close_pool()
        return checked > 0
    except Exception as e:
        logger.error(f"Drift check error: {str(e)}", exc_info=True)
        return False


def run_train_classifier_cmd() -> bool:
    logger.info("=" * 80)
    logger.info("TRAINING REGIME CLASSIFIER")
    logger.info("=" * 80)
    try:
        from cli.db import get_db_connection
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
