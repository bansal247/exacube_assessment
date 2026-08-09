"""Structured JSON logging with a trace_id that follows one request through
every layer -- router, agent loop, each provider call, each plugin call,
down to the SQL a plugin runs. One log line per JSON object (easy to grep,
easy to feed into any log aggregator), trace_id present on every line for
the lifetime of a request so `grep trace_id logs | jq` reconstructs the
whole turn in order.

trace_id is carried via a contextvar, not threaded through every function
signature -- the loop/plugins/repositories were already built and tested
without a trace_id parameter; a contextvar adds this cross-cutting concern
without touching any of those signatures. asyncio.create_task() copies the
current context by default, so it survives into the loop's per-tool-call
tasks (see loop.py) without extra plumbing.
"""

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)

# Standard attributes every LogRecord carries -- anything else on the
# record came from a caller's `extra={...}` and should be surfaced as its
# own JSON field.
_STANDARD_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        trace_id = trace_id_var.get()
        if trace_id:
            payload["trace_id"] = trace_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn's own loggers otherwise use their own (non-JSON) formatter --
    # route them through the same handler for one consistent log stream.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
