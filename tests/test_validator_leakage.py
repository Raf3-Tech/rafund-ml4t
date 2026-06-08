"""Anti-leakage and significance-gate tests for models.validator.ModelValidator."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtesting.splitter import TimeSeriesSplitter
from models.train import ModelMetadata
from models.validator import ModelValidator


# --------------------------------------------------------------------------- #
# 1. Train/validation disjointness
# --------------------------------------------------------------------------- #
def test_drop_rows_on_or_before_enforces_disjointness():
    ts = pd.date_range("2023-01-01", periods=10, freq="D", tz="UTC")
    df = pd.DataFrame({"timestamp": ts, "x": range(10)})

    out = ModelValidator._drop_rows_on_or_before(df, date(2023, 1, 5))

    # Strictly post-dates the training end: 2023-01-05 itself is excluded.
    assert out["timestamp"].min() == pd.Timestamp("2023-01-06", tz="UTC")
    assert (out["timestamp"] > pd.Timestamp("2023-01-05", tz="UTC")).all()
    assert len(out) == 5


def test_drop_rows_on_or_before_noop_when_train_end_none():
    ts = pd.date_range("2023-01-01", periods=4, freq="D", tz="UTC")
    df = pd.DataFrame({"timestamp": ts})
    assert len(ModelValidator._drop_rows_on_or_before(df, None)) == 4


# --------------------------------------------------------------------------- #
# 2. Significance gate in the promotion path
# --------------------------------------------------------------------------- #
def _candidate_meta() -> ModelMetadata:
    return ModelMetadata(
        run_id="run-1",
        model_name="rafund_stat_arb_ADAUSDT",
        model_version=1,
        symbol="ADA/USDT",
        trained_at=datetime.now(timezone.utc),
        train_start=date(2023, 1, 1),
        train_end=date(2023, 6, 1),
        in_sample_sharpe=1.2,
        feature_names=["z_score", "spread_std"],
        stage="Staging",
    )


def _walk_result(fold_daily_returns):
    """Build a minimal walk-result with a healthy perf gate but the supplied
    per-fold OOS daily returns (drives the significance gate)."""
    folds = [SimpleNamespace(equity_curve=[{"daily_return": r} for r in fold_daily_returns])]
    aggregate = SimpleNamespace(mean_oos_sharpe=1.5, worst_oos_drawdown=3.0)
    return SimpleNamespace(aggregate=aggregate, folds=folds)


def _run_validate(db, walk_result):
    with patch("models.validator.mlflow.pyfunc.load_model"), \
         patch("models.validator.WalkForwardRunner") as runner_cls, \
         patch("models.validator.MlflowClient"), \
         patch("models.validator.ModelValidator._load_validation_data") as load_data, \
         patch.object(ModelValidator, "_promote_candidate", return_value=None), \
         patch.object(ModelValidator, "_tag_run", return_value=None):
        load_data.return_value = pd.DataFrame({"z_score": [0.1, 0.2]})
        runner_cls.return_value.run.return_value = walk_result
        validator = ModelValidator(db=db, splitter=TimeSeriesSplitter(), engine_config={})
        return validator.validate_for_promotion(_candidate_meta(), "ADA/USDT", "1d")


def test_significance_gate_blocks_insignificant_returns():
    db = _make_db()
    rng = np.random.default_rng(1)
    noise = rng.normal(0.0, 0.02, 300)  # zero-mean noise -> not significant
    decision = _run_validate(db, _walk_result(noise))

    assert decision.approved is False
    assert "Significance gate failed" in decision.reason
    assert decision.significant is False


def test_significance_gate_allows_significant_returns():
    db = _make_db()
    rng = np.random.default_rng(0)
    edge = rng.normal(0.002, 0.001, 300)  # strong stable positive edge
    decision = _run_validate(db, _walk_result(edge))

    assert decision.approved is True
    assert decision.significant is True


def _make_db():
    from unittest.mock import MagicMock

    db = MagicMock()
    db.get_active_production_model.return_value = None
    return db
