import heapq
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID


@dataclass(order=True)
class BookEntry:
    sort_key: tuple = field(compare=True)
    order_id: UUID = field(compare=False)
    user_id: UUID = field(compare=False)
    side: str = field(compare=False)
    quantity: float = field(compare=False)
    limit_price: float = field(compare=False)
    submitted_at: datetime = field(compare=False)

    @classmethod
    def buy(cls, order_id: UUID, user_id: UUID, quantity: float, limit_price: float) -> "BookEntry":
        now = datetime.now(timezone.utc)
        return cls(
            sort_key=(-limit_price, now.timestamp()),
            order_id=order_id,
            user_id=user_id,
            side="BUY",
            quantity=quantity,
            limit_price=limit_price,
            submitted_at=now,
        )

    @classmethod
    def sell(cls, order_id: UUID, user_id: UUID, quantity: float, limit_price: float) -> "BookEntry":
        now = datetime.now(timezone.utc)
        return cls(
            sort_key=(limit_price, now.timestamp()),
            order_id=order_id,
            user_id=user_id,
            side="SELL",
            quantity=quantity,
            limit_price=limit_price,
            submitted_at=now,
        )


@dataclass
class MatchResult:
    matched: bool
    order_id: UUID
    match_price: float = 0.0


class OrderBook:
    """Per-instrument order book with price-time priority."""

    def __init__(self, instrument: str):
        self.instrument = instrument
        self._bids: list[BookEntry] = []  # max-heap (negated price)
        self._asks: list[BookEntry] = []  # min-heap
        self._cancelled: set[UUID] = set()

    @property
    def bid_depth(self) -> int:
        return sum(1 for e in self._bids if e.order_id not in self._cancelled)

    @property
    def ask_depth(self) -> int:
        return sum(1 for e in self._asks if e.order_id not in self._cancelled)

    def add_limit_order(self, order_id: UUID, user_id: UUID, side: str, quantity: float, limit_price: float):
        if side == "BUY":
            entry = BookEntry.buy(order_id, user_id, quantity, limit_price)
            heapq.heappush(self._bids, entry)
        else:
            entry = BookEntry.sell(order_id, user_id, quantity, limit_price)
            heapq.heappush(self._asks, entry)

    def cancel_order(self, order_id: UUID) -> bool:
        self._cancelled.add(order_id)
        return True

    def check_fills(self, current_bid: float, current_ask: float) -> list[MatchResult]:
        """Check limit orders against current LP prices."""
        results = []

        while self._bids:
            top = self._bids[0]
            if top.order_id in self._cancelled:
                heapq.heappop(self._bids)
                continue
            if top.limit_price >= current_ask:
                heapq.heappop(self._bids)
                results.append(MatchResult(
                    matched=True,
                    order_id=top.order_id,
                    match_price=current_ask,
                ))
            else:
                break

        while self._asks:
            top = self._asks[0]
            if top.order_id in self._cancelled:
                heapq.heappop(self._asks)
                continue
            if top.limit_price <= current_bid:
                heapq.heappop(self._asks)
                results.append(MatchResult(
                    matched=True,
                    order_id=top.order_id,
                    match_price=current_bid,
                ))
            else:
                break

        return results
