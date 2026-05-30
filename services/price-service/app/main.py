import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from .cache.price_cache import PriceCache
from .middleware.request_logging import RequestLoggingMiddleware
from .middleware.telemetry import instrument_app, setup_telemetry
from .providers.mock import MockProvider
from .routes import ops_internal, health, lp, prices

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_telemetry("price-service")

    provider = MockProvider()

    cache = PriceCache(provider)
    await cache.start()

    prices.init_router(cache)
    lp.init_router(cache)
    health.init_router(cache)

    yield

    await cache.stop()


app = FastAPI(title="Nexus Price Service", version="0.1.0", lifespan=lifespan)

app.add_middleware(RequestLoggingMiddleware)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
instrument_app(app)

app.include_router(prices.router)
app.include_router(lp.router)
app.include_router(health.router)
app.include_router(ops_internal.router)
