from fastapi import APIRouter, Depends
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..auth.dependencies import get_current_user

router = APIRouter(prefix="/api/orders")

_http = None


def init_router(http_client):
    global _http
    _http = http_client


class CreateOrderRequest(BaseModel):
    instrument: str
    side: str
    order_type: str
    quantity: float
    limit_price: float | None = None


@router.post("")
async def create_order(
    req: CreateOrderRequest,
    user: dict = Depends(get_current_user),
):
    resp = await _http.post(
        "/orders/submit",
        json={
            "user_id": user["user_id"],
            "instrument": req.instrument,
            "side": req.side,
            "order_type": req.order_type,
            "quantity": req.quantity,
            "limit_price": req.limit_price,
        },
    )
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


@router.get("")
async def list_orders(
    status: str | None = None,
    user: dict = Depends(get_current_user),
):
    params = {"user_id": user["user_id"]}
    if status:
        params["status"] = status
    resp = await _http.get("/orders", params=params)
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


@router.get("/{order_id}")
async def get_order(
    order_id: str,
    _user: dict = Depends(get_current_user),
):
    resp = await _http.get(f"/orders/{order_id}")
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


@router.delete("/{order_id}")
async def cancel_order(
    order_id: str,
    _user: dict = Depends(get_current_user),
):
    resp = await _http.delete(f"/orders/{order_id}")
    return JSONResponse(content=resp.json(), status_code=resp.status_code)
