"""Offline regression tests locking in two retraining-lifecycle bugs.

BUG 1: engine_config leakage — ``_retrain_one`` used to forward the whole
``retraining`` config (with models/symbols/timeframe) to ``ModelValidator`` as
its ``engine_config``, which eventually hit ``BacktestEngine(**engine_config)``
and raised ``TypeError`` for unexpected kwargs. The fix forwards only the
dedicated ``engine`` sub-block.

BUG 2: retraining symbols must have feature data — the ``features`` table is
keyed by ``symbol_a``/``symbol_b``; a symbol with no matching rows yields an
empty query and the trainer raises ``ValueError("No training data found ...")``.

All tests are 100% offline: DB is a ``MagicMock``, MLflow is patched, trainers /
validators / ``read_sql`` are patched. No network, Postgres, or MLflow writes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from models.retraining_scheduler import run_retraining_cycle
from models.train import ModelTrainer


def _fake_meta() -> MagicMock:
    """A stand-in for ModelMetadata returned by ModelTrainer.train."""
    meta = MagicMock()
    meta.model_name = "rafund_stat_arb_ADA_USDT"
    meta.run_id = "run-123"
    meta.feature_names = ["spread", "z_score"]
    return meta


# ---------------------------------------------------------------------------
# BUG 1 — engine_config leakage
# ---------------------------------------------------------------------------

def test_validator_engine_config_excludes_non_engine_keys():
    """The validator must NOT receive models/symbols/timeframe as engine_config.

    Pins the fix: with no explicit ``engine`` block, the forwarded engine_config
    is a dict free of the leaked retraining keys (defaults to {}).
    """
    db = MagicMock()
    config = {
        "retraining": {
            "models": ["stat_arb"],
            "symbols": ["ADA/USDT"],
            "timeframe": "1d",
        }
    }

    with patch("models.retraining_scheduler.ModelTrainer") as trainer_cls, \
         patch("models.retraining_scheduler.ModelValidator") as validator_cls, \
         patch("models.retraining_scheduler.configure_mlflow"):
        trainer_cls.return_value.train.return_value = _fake_meta()
        validator_cls.return_value.validate_for_promotion.return_value = MagicMock(
            approved=True, reason="ok"
        )

        results = run_retraining_cycle(db, config)

    assert results and results[0].success

    # ModelValidator(self.db, splitter, engine_config) -> engine_config is 3rd positional.
    assert validator_cls.called
    engine_config = validator_cls.call_args[0][2]
    assert isinstance(engine_config, dict)
    for leaked in ("models", "symbols", "timeframe"):
        assert leaked not in engine_config, f"{leaked!r} leaked into engine_config"
    assert engine_config == {}


def test_validator_receives_explicit_engine_block():
    """When an ``engine`` sub-block exists, exactly that block is forwarded."""
    db = MagicMock()
    config = {
        "retraining": {
            "models": ["stat_arb"],
            "symbols": ["ADA/USDT"],
            "timeframe": "1d",
            "engine": {"commission_pct": 0.002},
        }
    }

    with patch("models.retraining_scheduler.ModelTrainer") as trainer_cls, \
         patch("models.retraining_scheduler.ModelValidator") as validator_cls, \
         patch("models.retraining_scheduler.configure_mlflow"):
        trainer_cls.return_value.train.return_value = _fake_meta()
        validator_cls.return_value.validate_for_promotion.return_value = MagicMock(
            approved=True, reason="ok"
        )

        run_retraining_cycle(db, config)

    engine_config = validator_cls.call_args[0][2]
    assert engine_config == {"commission_pct": 0.002}
    for leaked in ("models", "symbols", "timeframe"):
        assert leaked not in engine_config


def test_backtest_engine_rejects_non_engine_kwargs():
    """Contract guard: engine_config must be engine kwargs only.

    This is the downstream failure the leaked config used to trigger.
    """
    with pytest.raises(TypeError):
        BacktestEngine(**{"models": ["stat_arb"]})

    # A valid engine kwarg constructs fine.
    engine = BacktestEngine(**{"commission_pct": 0.001})
    assert engine.commission_pct == 0.001


# ---------------------------------------------------------------------------
# BUG 2 — retraining symbols must have feature data
# ---------------------------------------------------------------------------

def test_train_raises_when_symbol_has_no_feature_data():
    """A symbol absent from the features table -> empty query -> ValueError.

    Mirrors the real "No training data found" failure mode without a DB or
    MLflow: ``pandas.read_sql`` returns an empty frame and ``train`` raises
    before any MLflow call.
    """
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()  # symbol has no feature rows
    settings = MagicMock()  # avoid hitting real config loader / files

    trainer = ModelTrainer(db, settings=settings)

    with pytest.raises(ValueError, match="No training data"):
        trainer.train("stat_arb", "BTC/USDT")

    # The empty-data path was exercised (failed at the query, not later).
    assert db.read_sql.called
