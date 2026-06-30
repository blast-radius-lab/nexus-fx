"""Prometheus metrics middleware, downstream tracking, and DB timing for engine."""

import time
from contextlib import asynccontextmanager

from prometheus_client import Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "route", "status_code"],
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "route", "status_code"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
)

HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "HTTP requests currently in flight",
    ["route"],
)

DOWNSTREAM_REQUESTS_TOTAL = Counter(
    "downstream_requests_total",
    "Total outbound requests to downstream services",
    ["target", "status_code"],
)

DOWNSTREAM_REQUEST_DURATION = Histogram(
    "downstream_request_duration_seconds",
    "Duration of outbound requests to downstream services in seconds",
    ["target"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
)

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database operation duration in seconds",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
)

_SKIP_PATHS = {"/metrics", "/health"}


def make_downstream_hooks(target: str) -> dict:
    """Return httpx event hooks that record downstream RED metrics for the given target name."""
    pending: dict[int, float] = {}

    async def on_request(request) -> None:
        pending[id(request)] = time.perf_counter()

    async def on_response(response) -> None:
        start = pending.pop(id(response.request), None)
        if start is not None:
            DOWNSTREAM_REQUEST_DURATION.labels(target=target).observe(
                time.perf_counter() - start
            )
        DOWNSTREAM_REQUESTS_TOTAL.labels(
            target=target, status_code=str(response.status_code)
        ).inc()

    return {"request": [on_request], "response": [on_response]}


@asynccontextmanager
async def timed_db_operation(operation: str):
    """Async context manager that records the wall-clock duration of a DB operation."""
    start = time.perf_counter()
    try:
        yield
    finally:
        DB_QUERY_DURATION.labels(operation=operation).observe(
            time.perf_counter() - start
        )


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        raw_path = request.url.path
        HTTP_REQUESTS_IN_FLIGHT.labels(route=raw_path).inc()

        start = time.perf_counter()
        status_code = "500"

        try:
            response = await call_next(request)
            status_code = str(response.status_code)
            return response
        finally:
            duration = time.perf_counter() - start
            HTTP_REQUESTS_IN_FLIGHT.labels(route=raw_path).dec()

            route_obj = request.scope.get("route")
            route = route_obj.path if route_obj else raw_path

            HTTP_REQUESTS_TOTAL.labels(
                method=request.method, route=route, status_code=status_code
            ).inc()
            HTTP_REQUEST_DURATION.labels(
                method=request.method, route=route, status_code=status_code
            ).observe(duration)