"""Runnable launcher for the Raf3nd ML4T ops-dashboard Flask app.

Wires a real :class:`DatabaseConnection` into the :func:`create_app`
application factory and serves the dashboard. The retraining config block
consumed by the dashboard's on-demand retraining endpoint is assembled from
``config.loader`` so the launcher mirrors how the rest of the codebase builds
it. No :class:`DatabaseConnection` is constructed at import time; it is only
created inside :func:`main`.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import structlog

from data.db import DatabaseConnection
from monitoring.dashboard_app import create_app

logger = structlog.get_logger(__name__)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def build_config(settings: Optional[Any] = None) -> dict[str, Any]:
    """Assemble the ``{"retraining": {...}}`` config for the dashboard.

    The dashboard's retraining endpoint passes this dict straight to
    :func:`models.retraining_scheduler.run_retraining_cycle`, which reads its
    ``retraining`` sub-dict. We source the ``retraining`` block from the raw
    parsed ``settings.yaml`` (the canonical location used by the CLI retrain
    command) and overlay a ``timeframe`` derived from the resolved settings.

    Args:
        settings: Optional resolved settings object (as returned by
            :func:`config.loader.get_settings`). When ``None`` it is loaded
            lazily. Used only to derive ``timeframe``.

    Returns:
        A dict shaped like ``{"retraining": {...}}``. Falls back to
        ``{"retraining": {}}`` (a no-op cycle) when config is unavailable.
    """
    retraining: dict[str, Any] = {}

    try:
        from config.loader import load_config

        raw = load_config() or {}
        block = raw.get("retraining")
        if isinstance(block, dict):
            retraining = dict(block)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("dashboard_config_load_failed", error=str(exc))

    try:
        if settings is None:
            from config.loader import get_settings

            settings = get_settings()
        timeframe = getattr(settings, "timeframe", None)
        if timeframe and "timeframe" not in retraining:
            retraining["timeframe"] = timeframe
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("dashboard_settings_load_failed", error=str(exc))

    if not retraining:
        logger.warning("dashboard_retraining_config_empty")

    return {"retraining": retraining}


def build_app_from_env():
    """Construct the DB and build the hardened Flask app from the environment.

    This is the canonical WSGI entry point. Auth and CORS are sourced from the
    environment so they can be set per-deployment without code changes:

    * ``DASHBOARD_API_TOKEN`` — bearer token required on the mutating POST
      endpoints. Unset → those endpoints are unprotected (a warning is logged).
    * ``DASH_CORS_ORIGINS`` — comma-separated allow-list of origins for the
      ``/api/*`` endpoints. Unset → CORS is wide-open (a warning is logged).

    Construct under a production WSGI server via gunicorn's factory syntax,
    e.g. ``gunicorn -w 4 -b 0.0.0.0:8000 'monitoring.run_dashboard:build_app_from_env()'``
    so the DB is created at worker boot, never at import time.
    """
    db = DatabaseConnection(
        database_url=os.getenv("DATABASE_URL"),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "rafund"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )

    origins_raw = os.getenv("DASH_CORS_ORIGINS")
    cors_origins = (
        [o.strip() for o in origins_raw.split(",") if o.strip()]
        if origins_raw
        else None
    )

    return create_app(
        db,
        build_config(),
        api_token=os.getenv("DASHBOARD_API_TOKEN"),
        cors_origins=cors_origins,
    )


def main(host: Optional[str] = None, port: Optional[int] = None) -> None:
    """Construct the DB, build the Flask app, and serve the dashboard.

    Uses Flask's built-in server, intended for local/dev use only — run behind
    a real WSGI server (see :func:`build_app_from_env`) in production.

    Host/port resolution order: explicit argument, then environment
    (``DASH_HOST`` / ``DASH_PORT``), then the module defaults.
    """
    app = build_app_from_env()

    if host is None:
        host = os.getenv("DASH_HOST", DEFAULT_HOST)
    if port is None:
        env_port = os.getenv("DASH_PORT")
        port = int(env_port) if env_port else DEFAULT_PORT

    logger.info("dashboard_starting", host=host, port=port)
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
