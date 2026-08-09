"""Per-request trace_id: read X-Trace-Id if the caller supplied one (lets an
upstream system correlate its own trace across services), otherwise
generate one. Set on trace_id_var for the lifetime of the request and
echoed back in the response header.

Deliberately a raw ASGI middleware, not Starlette's BaseHTTPMiddleware.
BaseHTTPMiddleware runs the downstream app in a separate task in some
Starlette versions, which can silently break contextvar propagation into
the actual route handler -- exactly the mechanism this depends on. A raw
ASGI middleware calls the downstream app directly in this same coroutine,
so there's no ambiguity about whether the contextvar is still in scope by
the time it reaches the router/loop/plugins.
"""

import logging
import time
import uuid

logger = logging.getLogger("app.request")

# Docker's own healthcheck (see docker-compose.yml) hits this every few
# seconds for the container's entire lifetime -- a *successful* one carries
# no diagnostic value and would otherwise dominate real log volume. A
# failing one is the opposite -- exactly the signal you'd want in the
# logs -- so only 200s on this path are suppressed, not the path itself
# unconditionally.
_QUIET_PATH = "/health"
_QUIET_STATUS = 200


class TraceIdMiddleware:
    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        from app.logging_config import trace_id_var

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-trace-id")
        trace_id = incoming.decode() if incoming else str(uuid.uuid4())
        token = trace_id_var.set(trace_id)

        method = scope.get("method")
        path = scope.get("path")
        started_at = time.monotonic()
        status_holder: dict = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status_code"] = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-trace-id", trace_id.encode())]
            await send(message)

        # "started" alone (before the outcome is known) never carries more
        # signal than "completed" already does below -- and "completed"
        # always fires (this is in `finally`), so skipping "started"
        # outright for the quiet path doesn't cost any failure visibility.
        if path != _QUIET_PATH:
            logger.info("request started", extra={"http_method": method, "http_path": path})
        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            status_code = status_holder.get("status_code")
            if path != _QUIET_PATH or status_code != _QUIET_STATUS:
                logger.info(
                    "request completed",
                    extra={
                        "http_method": method,
                        "http_path": path,
                        "status_code": status_code,
                        "duration_ms": (time.monotonic() - started_at) * 1000,
                    },
                )
            trace_id_var.reset(token)
