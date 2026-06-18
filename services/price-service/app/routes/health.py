from fastapi import APIRouter

router = APIRouter()

_cache = None


def init_router(cache):
    global _cache
    _cache = cache


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": _cache._provider.provider_name() if _cache else "unknown",
        "last_update": _cache.last_update.isoformat() if _cache and _cache.last_update else None,
        "instruments_cached": len(_cache.prices) if _cache else 0,
    }
