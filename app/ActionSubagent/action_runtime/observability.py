from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from time import perf_counter
from typing import Final
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

DEFAULT_TRACE_ID_HEADER: Final = "X-Trace-ID"
TRACE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HEADER_NAME_PATTERN: Final = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
LOG_FIELDS: Final = (
    "method",
    "path",
    "status_code",
    "duration_ms",
    "error_type",
    "application_id",
    "capability_id",
    "attempt",
    "max_attempts",
    "retry_reason",
)

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def validate_trace_id_header(header_name: str) -> str:
    if not HEADER_NAME_PATTERN.fullmatch(header_name):
        raise ValueError("TRACE_ID_HEADER must be a valid HTTP header name")
    return header_name


def resolve_trace_id(candidate: str | None) -> str:
    if candidate is not None and TRACE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


def current_trace_id() -> str | None:
    return _trace_id.get()


def trace_propagation_headers(header_name: str = DEFAULT_TRACE_ID_HEADER) -> dict[str, str]:
    trace_id = current_trace_id()
    if trace_id is None:
        return {}
    return {validate_trace_id_header(header_name): trace_id}


class JsonLogFormatter(logging.Formatter):
    def __init__(self, *, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "event": record.getMessage(),
        }
        trace_id = getattr(record, "trace_id", None) or current_trace_id()
        if trace_id is not None:
            payload["trace_id"] = trace_id
        for field_name in LOG_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(*, service_name: str, log_level: str) -> None:
    level = logging.getLevelName(log_level.upper())
    if not isinstance(level, int):
        raise ValueError(f"invalid LOG_LEVEL: {log_level}")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter(service_name=service_name))
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True


class TraceIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, service_name: str, header_name: str) -> None:
        super().__init__(app)
        self.service_name = service_name
        self.header_name = validate_trace_id_header(header_name)
        self.logger = logging.getLogger(f"{service_name}.http")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        trace_id = resolve_trace_id(request.headers.get(self.header_name))
        request.state.trace_id = trace_id
        token: Token[str | None] = _trace_id.set(trace_id)
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            self.logger.error(
                "http_request_failed",
                extra={
                    "method": request.method,
                    "path": self._route_path(request),
                    "status_code": 500,
                    "duration_ms": self._duration_ms(started_at),
                    "error_type": type(error).__name__,
                },
            )
            response = Response(
                content='{"detail":"Internal Server Error"}',
                status_code=500,
                media_type="application/json",
            )
            response.headers[self.header_name] = trace_id
            return response
        else:
            response.headers[self.header_name] = trace_id
            self.logger.info(
                "http_request_completed",
                extra={
                    "method": request.method,
                    "path": self._route_path(request),
                    "status_code": response.status_code,
                    "duration_ms": self._duration_ms(started_at),
                },
            )
            return response
        finally:
            _trace_id.reset(token)

    @staticmethod
    def _duration_ms(started_at: float) -> float:
        return round((perf_counter() - started_at) * 1000, 3)

    @staticmethod
    def _route_path(request: Request) -> str:
        route = request.scope.get("route")
        return getattr(route, "path", request.url.path)
