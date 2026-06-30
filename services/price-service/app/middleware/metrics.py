"""Prometheus metrics middleware for price-service."""

import time

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

_SKIP_PATHS = {"/metrics", "/health"}


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