from fastapi import APIRouter

router = APIRouter()

_engine = None


def init_router(matching_engine):
    global _engine
    _engine = matching_engine


@router.get("/health")
async def health():
    book_stats = {}
    if _engine:
        for inst, book in _engine._books.items():
            book_stats[inst] = {
                "bid_depth": book.bid_depth,
                "ask_depth": book.ask_depth,
            }
    return {
        "status": "ok",
        "order_books": book_stats,
    }
