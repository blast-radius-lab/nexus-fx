from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from .. import _ops
from ..providers.base import SUPPORTED_INSTRUMENTS

router = APIRouter()

_cache = None


def init_router(cache):
    global _cache
    _cache = cache


@router.get("/prices/current")
async def get_current_prices(
    instruments: str = Query(default="", description="Comma-separated instrument list"),
):
    if _ops.is_active("price_stopped"):
        raise HTTPException(status_code=503, detail="Price service unavailable")
    await _ops.apply_latency("price_latency")

    if not instruments:
        requested = [i.symbol for i in SUPPORTED_INSTRUMENTS]
    else:
        requested = [s.strip() for s in instruments.split(",")]

    prices = {}
    for inst in requested:
        quote = _cache.get_price(inst)
        if quote:
            prices[inst] = asdict(quote)
    return {"prices": prices}


@router.get("/prices/candles")
async def get_candles(
    instrument: str = Query(...),
    granularity: str = Query(default="H1"),
    count: int = Query(default=100, le=500),
):
    candles = await _cache._provider.get_candles(instrument, granularity, count)
    return {"instrument": instrument, "candles": [asdict(c) for c in candles]}


@router.get("/prices/instruments")
async def get_instruments():
    return {
        "instruments": [asdict(i) for i in SUPPORTED_INSTRUMENTS]
    }
