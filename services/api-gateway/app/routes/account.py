from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from ..auth.dependencies import get_current_user

router = APIRouter(prefix="/api/account")

_http = None


def init_router(http_client):
    global _http
    _http = http_client


@router.get("/summary")
async def account_summary(user: dict = Depends(get_current_user)):
    resp = await _http.get("/account/summary", params={"user_id": user["user_id"]})
    return JSONResponse(content=resp.json(), status_code=resp.status_code)
