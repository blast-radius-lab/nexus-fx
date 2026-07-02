import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .cache.price_cache import PriceCache
from .middleware.request_logging import RequestLoggingMiddleware
from .middleware.telemetry import setup_telemetry
from .providers.mock import MockProvider
from .routes import health, lp, ops_internal, prices

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

app.include_router(prices.router)
app.include_router(lp.router)
app.include_router(health.router)
app.include_router(ops_internal.router)
