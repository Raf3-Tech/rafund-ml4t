"""Offline tests for the ops-dashboard launcher.

No real database is constructed and no real socket is bound: the launcher's
``DatabaseConnection`` and ``create_app`` dependencies are patched at their
use site in ``monitoring.run_dashboard``, and the returned app is a MagicMock
whose ``run`` is asserted on rather than executed.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from monitoring import run_dashboard


def test_build_config_returns_retraining_key():
    config = run_dashboard.build_config()
    assert isinstance(config, dict)
    assert "retraining" in config
    assert isinstance(config["retraining"], dict)


def test_build_config_with_mocked_settings():
    settings = MagicMock()
    settings.timeframe = "1d"
    config = run_dashboard.build_config(settings=settings)
    assert "retraining" in config
    assert isinstance(config["retraining"], dict)


def test_main_constructs_db_builds_app_and_runs_defaults():
    fake_app = MagicMock()
    fake_db = MagicMock()
    with patch.dict(os.environ, {}, clear=True), patch(
        "monitoring.run_dashboard.DatabaseConnection", return_value=fake_db
    ) as mock_db, patch(
        "monitoring.run_dashboard.create_app", return_value=fake_app
    ) as mock_create_app:
        run_dashboard.main()

    # DB was constructed (the canonical way, inside main).
    assert mock_db.called
    # App built from the constructed db plus a retraining config.
    mock_create_app.assert_called_once()
    args, _ = mock_create_app.call_args
    assert args[0] is fake_db
    assert "retraining" in args[1]
    # Served on the defaults; no real socket bound.
    fake_app.run.assert_called_once_with(host="0.0.0.0", port=8000)


def test_main_respects_function_arg_overrides():
    fake_app = MagicMock()
    with patch.dict(os.environ, {}, clear=True), patch(
        "monitoring.run_dashboard.DatabaseConnection", return_value=MagicMock()
    ), patch("monitoring.run_dashboard.create_app", return_value=fake_app):
        run_dashboard.main(host="127.0.0.1", port=9999)

    fake_app.run.assert_called_once_with(host="127.0.0.1", port=9999)


def test_main_respects_env_overrides():
    fake_app = MagicMock()
    env = {"DASH_HOST": "10.0.0.1", "DASH_PORT": "5555"}
    with patch.dict(os.environ, env, clear=True), patch(
        "monitoring.run_dashboard.DatabaseConnection", return_value=MagicMock()
    ), patch("monitoring.run_dashboard.create_app", return_value=fake_app):
        run_dashboard.main()

    fake_app.run.assert_called_once_with(host="10.0.0.1", port=5555)


def test_main_does_not_bind_real_socket():
    fake_app = MagicMock()
    with patch.dict(os.environ, {}, clear=True), patch(
        "monitoring.run_dashboard.DatabaseConnection", return_value=MagicMock()
    ), patch("monitoring.run_dashboard.create_app", return_value=fake_app):
        run_dashboard.main()

    # app.run is the only network entrypoint and it is the patched mock,
    # so no real listening socket was opened.
    assert fake_app.run.call_count == 1
    assert isinstance(fake_app.run, MagicMock)
