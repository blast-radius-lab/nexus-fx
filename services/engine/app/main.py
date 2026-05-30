import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from .matching.engine import MatchingEngine
from .middleware.request_logging import RequestLoggingMiddleware
from .middleware.telemetry import instrument_app, setup_telemetry
from .routes import ops_internal, health, orders

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

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
instrument_app(app)

app.include_router(orders.router)
app.include_router(health.router)
app.include_router(ops_internal.router)
