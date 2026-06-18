from fastapi import APIRouter, Depends, Query
from starlette.responses import JSONResponse

from ..auth.dependencies import get_current_user

router = APIRouter(prefix="/api/prices")

_http = None


def init_router(http_client):
    global _http
    _http = http_client


@router.get("")
async def get_prices(
    instruments: str = Query(default=""),
    _user: dict = Depends(get_current_user),
):
    resp = await _http.get("/prices/current", params={"instruments": instruments})
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


@router.get("/candles")
async def get_candles(
    instrument: str = Query(...),
    granularity: str = Query(default="H1"),
    count: int = Query(default=100),
    _user: dict = Depends(get_current_user),
):
    resp = await _http.get(
        "/prices/candles",
        params={"instrument": instrument, "granularity": granularity, "count": count},
    )
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


@router.get("/instruments")
async def get_instruments(_user: dict = Depends(get_current_user)):
    resp = await _http.get("/prices/instruments")
    return JSONResponse(content=resp.json(), status_code=resp.status_code)
