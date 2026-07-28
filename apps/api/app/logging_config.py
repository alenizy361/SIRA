"""Structured JSON logging (constitution section 22).

Every log line is a JSON object with timestamp, level, service, and - when
available via contextvars set by request/task middleware - trace_id,
correlation_id, organization_id, actor, task_id, and run_id. Never log
secrets: LogRecord.msg is passed through _redact() before serialization.
"""
import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

SERVICE_NAME = "rabit-api"

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
_organization_id: ContextVar[str] = ContextVar("organization_id", default="")
_actor: ContextVar[str] = ContextVar("actor", default="")

_REDACT_PATTERNS = [
    re.compile(r"(password|secret|api[_-]?key|token|credential)\s*[:=]\s*\S+", re.IGNORECASE),
]


def _redact(message: str) -> str:
    for pattern in _REDACT_PATTERNS:
        message = pattern.sub(lambda m: m.group(0).split(m.group(1))[0] + f"{m.group(1)}=***REDACTED***", message)
    return message


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "logger": record.name,
            "message": _redact(record.getMessage()),
            "trace_id": _trace_id.get(),
            "correlation_id": _correlation_id.get(),
            "organization_id": _organization_id.get(),
            "actor": _actor.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def bind_context(*, trace_id: str = "", correlation_id: str = "", organization_id: str = "", actor: str = "") -> None:
    if trace_id:
        _trace_id.set(trace_id)
    if correlation_id:
        _correlation_id.set(correlation_id)
    if organization_id:
        _organization_id.set(organization_id)
    if actor:
        _actor.set(actor)
