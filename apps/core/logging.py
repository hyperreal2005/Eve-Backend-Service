"""Structured logging.

Containers emit one JSON object per line; local development gets a readable console. structlog
loggers and plain `logging` loggers (Django, gunicorn, Celery) share one processor chain, so every
line carries the same fields, including the request id bound by RequestContextMiddleware.
"""

from collections.abc import MutableMapping
from typing import Any

import structlog

_REDACTED = "[REDACTED]"
_SECRET_KEYS = frozenset(
    {"password", "token", "access", "refresh", "authorization", "secret", "signature"}
)


def mask_email(value: str) -> str:
    """`patient@example.com` → `p***@example.com`: enough to correlate, not enough to identify."""
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Keep secrets and personal data out of the logs, whoever wrote the log call."""
    for key, value in event_dict.items():
        name = key.lower()
        if name in _SECRET_KEYS:
            event_dict[key] = _REDACTED
        elif name == "email" and isinstance(value, str):
            event_dict[key] = mask_email(value)
    return event_dict


def build_logging_config(*, json_logs: bool, level: str) -> dict[str, Any]:
    """Configure structlog and return the matching `LOGGING` dict for Django."""
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
    ]
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Not cached, so tests can capture log events with structlog.testing.capture_logs().
        cache_logger_on_first_use=False,
    )

    final_processors: list[Any] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if json_logs:
        final_processors += [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        final_processors.append(structlog.dev.ConsoleRenderer())

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": final_processors,
                "foreign_pre_chain": [*shared_processors, structlog.stdlib.ExtraAdder()],
            }
        },
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "structured"}},
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            # The request middleware logs every request once; Django's own 4xx warnings would
            # duplicate it.
            "django.request": {"level": "ERROR"},
            "django.server": {"level": "WARNING"},
            # One INFO line per outgoing request; webhook deliveries log their own outcome.
            "httpx": {"level": "WARNING"},
        },
    }
