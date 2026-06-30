import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .matching.engine import MatchingEngine
from .middleware.metrics import MetricsMiddleware
from .middleware.request_logging import RequestLoggingMiddleware
from .middleware.telemetry import setup_telemetry
from .routes import health, ops_internal, orders

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_telemetry("engine")

    engine = MatchingEngine()
    await engine.start()

    orders.init_router(engine)
    health.init_router(engine)

    yield

    await engine.stop()


app = FastAPI(title="Nexus Engine", version="0.1.0", lifespan=lifespan)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(MetricsMiddleware)

app.include_router(orders.router)
app.include_router(health.router)
app.include_router(ops_internal.router)


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)