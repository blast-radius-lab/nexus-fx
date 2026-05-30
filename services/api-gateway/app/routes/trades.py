from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from ..auth.dependencies import get_current_user

router = APIRouter(prefix="/api/trades")

_http = None


def init_router(http_client):
    global _http
    _http = http_client


@router.get("/open")
async def open_trades(user: dict = Depends(get_current_user)):
    resp = await _http.get("/trades/open", params={"user_id": user["user_id"]})
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


@router.get("/closed")
async def closed_trades(user: dict = Depends(get_current_user)):
    resp = await _http.get("/trades/closed", params={"user_id": user["user_id"]})
    return JSONResponse(content=resp.json(), status_code=resp.status_code)
