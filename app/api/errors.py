from __future__ import annotations

import logging
import uuid

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
}

_TITLES = {
    "provider_timeout": "Provider timeout",
    "provider_unavailable": "Provider unavailable",
    "rate_limited": "Rate limited",
    "invalid_request": "Invalid request",
    "internal_error": "Internal error",
}

_DETAILS = {
    "provider_timeout": "The language-model provider timed out.",
    "provider_unavailable": "The language-model provider is unavailable.",
    "rate_limited": "The language-model provider rate-limited the request.",
    "invalid_request": "The request was rejected.",
    "internal_error": "The request could not be completed.",
}


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
        logger.warning(
            "typed_error code=%s trace_id=%s",
            exc.code,
            getattr(request.state, "trace_id", None),
        )
        return problem_response(request, exc.code)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_error trace_id=%s",
            getattr(request.state, "trace_id", None),
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
