"""Regression tests for the DB & configuration hardening pass.

These cover the audit findings that were fixed without requiring a live
PostgreSQL instance: SQL placeholder correctness, connection-leak safety on
error paths, accurate insert counts, secret handling and config validation.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import config.loader as loader
import data.db as dbmod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(cursor: MagicMock):
    """A DatabaseConnection with a fully mocked pool (no real DB)."""
    with patch.object(dbmod, "ThreadedConnectionPool"):
        db = dbmod.DatabaseConnection(
            host="h", port=5432, database="d", user="u", password="p"
        )
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = cursor
    db.pool = MagicMock()
    db.pool.getconn.return_value = fake_conn
    return db, fake_conn


# ---------------------------------------------------------------------------
# SQL correctness
# ---------------------------------------------------------------------------


def test_block_model_placeholder_count_matches_params():
    cursor = MagicMock()
    db, _ = _make_db(cursor)

    assert db.block_model("factor_model", "ADA/USDT", "drift") is True

    sql, params = cursor.execute.call_args[0]
    # 3 bound params (model_name, symbol, reason); blocked_at is NOW().
    assert sql.count("%s") == 3
    assert len(params) == 3


# ---------------------------------------------------------------------------
# Connection-leak safety (fault injection)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, args",
    [
        ("delete_signals_for_pair", ("A", "B")),
        ("delete_features_for_pair", ("A", "B")),
        ("delete_trades_for_symbol", ("A",)),
        ("delete_portfolio_for_symbol", ("A",)),
    ],
)
def test_delete_methods_return_connection_on_error(method, args):
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("boom")
    db, fake_conn = _make_db(cursor)

    result = getattr(db, method)(*args)

    assert result == 0  # error contract preserved
    # The connection must be returned exactly once even though execute() raised.
    db.pool.putconn.assert_called_once_with(fake_conn)
    fake_conn.rollback.assert_called_once()


def test_delete_returns_connection_on_success():
    cursor = MagicMock()
    cursor.rowcount = 7
    db, fake_conn = _make_db(cursor)

    assert db.delete_features_for_pair("A", "B") == 7
    db.pool.putconn.assert_called_once_with(fake_conn)


# ---------------------------------------------------------------------------
# Accurate insert counts
# ---------------------------------------------------------------------------


def test_insert_features_returns_rowcount_not_len():
    cursor = MagicMock()
    cursor.rowcount = 2  # DB wrote 2 rows ...
    db, _ = _make_db(cursor)

    df = pd.DataFrame(
        {
            "symbol_a": ["A"] * 5,
            "symbol_b": ["B"] * 5,
            "timestamp": pd.date_range("2023-01-01", periods=5, freq="D"),
            "spread": [1.0] * 5,
            "spread_mean": [1.0] * 5,
            "spread_std": [1.0] * 5,
            "z_score": [0.0] * 5,
            "hedge_ratio": [1.0] * 5,
        }
    )

    with patch.object(dbmod, "execute_values"):
        inserted = db.insert_features(df)

    # ... even though the DataFrame had 5 rows (3 were ON CONFLICT skips).
    assert inserted == 2


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------


def test_password_placeholder_never_leaks(monkeypatch):
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    importlib.reload(loader)
    raw = loader._load_yaml()
    assert raw.get("database", {}).get("password") is None
    s = loader.Settings(raw)
    assert s.db_password is None


def test_password_expands_from_env(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "s3cr3t")
    importlib.reload(loader)
    raw = loader._load_yaml()
    assert raw.get("database", {}).get("password") == "s3cr3t"


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_invalid_drawdown_rejected():
    with pytest.raises(ValueError, match="max_drawdown_pct"):
        loader.Settings({"evaluation": {"max_drawdown_pct": 5.0}})


def test_invalid_leg_allocation_rejected(monkeypatch):
    # Env overrides YAML, so set the override to the invalid value.
    monkeypatch.setenv("LEG_ALLOCATION_PCT", "1.5")
    with pytest.raises(ValueError, match="leg_allocation_pct"):
        loader.Settings({})


def test_symbol_drift_rejected(monkeypatch):
    monkeypatch.setenv("COLLECT_SYMBOLS", "BTC/USDT")
    with pytest.raises(ValueError, match="subset"):
        loader.Settings({"retraining": {"symbols": ["ETH/USDT"]}})


def test_retraining_subset_is_allowed(monkeypatch):
    # subset (not equality) must pass
    monkeypatch.setenv("COLLECT_SYMBOLS", "BTC/USDT,ETH/USDT,ADA/USDT")
    s = loader.Settings({"retraining": {"symbols": ["ADA/USDT"]}})
    assert s.retrain_symbols == ["ADA/USDT"]


def test_default_settings_pass_validation():
    # The shipped settings.yaml must be internally valid.
    importlib.reload(loader)
    assert loader.Settings() is not None
