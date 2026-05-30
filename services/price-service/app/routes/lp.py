from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_cache = None


def init_router(cache):
    global _cache
    _cache = cache


class ExecuteOrderRequest(BaseModel):
    instrument: str
    side: str
    units: float
    order_type: str = "MARKET"
    limit_price: float | None = None


@router.post("/lp/execute")
async def execute_order(req: ExecuteOrderRequest):
    result = await _cache._provider.execute_order(
        instrument=req.instrument,
        side=req.side,
        units=req.units,
        order_type=req.order_type,
        limit_price=req.limit_price,
    )
    return asdict(result)
