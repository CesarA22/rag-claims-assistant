from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.domain.errors import InsurCoError

logger = logging.getLogger(__name__)

_STATUS = {
    "provider_timeout": 504,
    "provider_unavailable": 503,
    "rate_limited": 429,
    "invalid_request": 400,
    "provider_degraded": 503,
    "circuit_open": 503,
}

_TITLES = {
    "provider_timeout": "Provider timeout",
    "provider_unavailable": "Provider unavailable",
    "rate_limited": "Rate limited",
    "invalid_request": "Invalid request",
    "provider_degraded": "Provider degraded",
    "circuit_open": "Circuit open",
    "internal_error": "Internal error",
}

_DETAILS = {
    "provider_timeout": "The language-model provider timed out.",
    "provider_unavailable": "The language-model provider is unavailable.",
    "rate_limited": "The language-model provider rate-limited the request.",
    "invalid_request": "The request was rejected.",
    "provider_degraded": "The language-model provider could not complete the request.",
    "circuit_open": "The language-model provider circuit is open.",
    "internal_error": "The request could not be completed.",
}

_LOG_CONTEXT_KEYS = ("model", "prompt", "upstream_status", "attempt", "latency_ms")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "event": getattr(record, "event", record.getMessage()),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
            "error_code": getattr(record, "error_code", None),
        }
        for key in _LOG_CONTEXT_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    root = logging.getLogger()
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(InsurCoError)
    async def handle_insurco_error(request: Request, exc: InsurCoError) -> JSONResponse:
        extra = {
            "event": "typed_error",
            "trace_id": getattr(request.state, "trace_id", None),
            "error_code": exc.code,
            **exc.context,
        }
        logger.warning("typed_error code=%s %s", exc.code, exc, extra=extra)
        return problem_response(request, exc.code)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_error trace_id=%s",
            getattr(request.state, "trace_id", None),
            extra={
                "event": "unhandled_error",
                "trace_id": getattr(request.state, "trace_id", None),
            },
        )
        return problem_response(request, "internal_error")


def problem_response(request: Request, code: str) -> JSONResponse:
    status = _STATUS.get(code, 500)
    trace_id = getattr(request.state, "trace_id", None) or str(uuid.uuid4())
    body = {
        "type": f"https://insurco.local/errors/{code}",
        "title": _TITLES.get(code, "Internal error"),
        "status": status,
        "detail": _DETAILS.get(code, "The request could not be completed."),
        "trace_id": trace_id,
    }
    return JSONResponse(
        body,
        status_code=status,
        media_type="application/problem+json",
        headers={"X-Trace-Id": trace_id},
    )
