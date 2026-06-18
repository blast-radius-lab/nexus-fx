"""Structured JSON logging configuration for the service."""

import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from pythonjsonlogger.json import JsonFormatter

# Context variable for per-request tracking
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

_service_name: str = "unknown"


class ServiceJsonFormatter(JsonFormatter):
    """JSON formatter that injects service name and request_id."""

    def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict):
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).isoformat()
        log_record["level"] = record.levelname
        log_record["service"] = _service_name
        log_record["logger"] = record.name

        rid = request_id_ctx.get()
        if rid is not None:
            log_record["request_id"] = rid


def setup_logging(service_name: str, level: int = logging.INFO) -> None:
    """Configure root logger with JSON output.

    Args:
        service_name: identifier written to every log line (e.g. "price-service").
        level: root log level, defaults to INFO.
    """
    global _service_name
    _service_name = service_name

    formatter = ServiceJsonFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Disable uvicorn's default access log — our middleware handles it
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False

    # Suppress noisy httpx INFO logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
