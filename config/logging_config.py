import logging
import os
import sys
from typing import Any

import structlog
from structlog.dev import ConsoleRenderer
from structlog.processors import (
    TimeStamper,
    add_log_level,
    format_exc_info,
    StackInfoRenderer,
)
from structlog.stdlib import LoggerFactory, add_logger_name


def configure_logging() -> None:
    """Configure structlog for JSON or human-friendly output."""
    level_name = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_format = os.getenv('LOG_FORMAT', 'pretty').lower()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        format='%(message)s',
        stream=sys.stdout,
        level=level,
    )

    processors = [
        add_log_level,
        add_logger_name,
        TimeStamper(fmt='iso', utc=True),
        format_exc_info,
        StackInfoRenderer(),
    ]

    if log_format == 'json':
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=LoggerFactory(),
        cache_logger_on_first_use=True,
    )


configure_logging()


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a bound structlog logger with service context."""
    return structlog.get_logger(name).bind(service='rafund-ml4t')
