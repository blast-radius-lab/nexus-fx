import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from . import _ops
from .config import settings
from .middleware.request_logging import RequestLoggingMiddleware
from .middleware.telemetry import setup_telemetry
from .routes import account, auth, orders, prices, trades, ws
from .routes import ops as ops_routes

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_telemetry("api-gateway")

    price_http = httpx.AsyncClient(base_url=settings.price_service_url, timeout=10.0)
    engine_http = httpx.AsyncClient(base_url=settings.engine_service_url, timeout=10.0)

    prices.init_router(price_http)
    orders.init_router(engine_http)
    trades.init_router(engine_http)
    account.init_router(engine_http)
    ws.init_router(price_http)
    ops_routes.init_router(price_http, engine_http)

    yield

    await price_http.aclose()
    await engine_http.aclose()


app = FastAPI(title="Nexus API Gateway", version="0.1.0", lifespan=lifespan)


class OpsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not request.url.path.startswith(("/ops/", "/health", "/metrics")):
            if _ops.should_fail("generic_errors"):
                return JSONResponse(status_code=500, content={"detail": "Internal server error"})
        return await call_next(request)


app.add_middleware(OpsMiddleware)

app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(prices.router)
app.include_router(orders.router)
app.include_router(trades.router)
app.include_router(account.router)
app.include_router(ws.router)
app.include_router(ops_routes.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Nexus FX API Gateway", "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "api-gateway"}
