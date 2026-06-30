import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .cache.price_cache import PriceCache
from .middleware.metrics import MetricsMiddleware
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
app.add_middleware(MetricsMiddleware)

FastAPIInstrumentor.instrument_app(app)

app.include_router(prices.router)
app.include_router(lp.router)
app.include_router(health.router)
app.include_router(ops_internal.router)


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)