import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx
from sqlalchemy import select, update

from .. import _ops
from ..config import settings
from ..middleware.metrics import make_downstream_hooks, timed_db_operation
from ..models.database import ClientOrder, LpOrder, async_session
from .order_book import OrderBook

logger = logging.getLogger(__name__)


class MatchingEngine:
    def __init__(self):
        self._books: dict[str, OrderBook] = {}
        self._http: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None

    def _get_book(self, instrument: str) -> OrderBook:
        if instrument not in self._books:
            self._books[instrument] = OrderBook(instrument)
        return self._books[instrument]

    async def start(self):
        self._http = httpx.AsyncClient(
            base_url=settings.price_service_url,
            timeout=10.0,
            event_hooks=make_downstream_hooks("price-service"),
        )
        self._task = asyncio.create_task(self._check_loop())
        logger.info("Matching engine started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._http:
            await self._http.aclose()

    async def submit_market_order(
        self, order_id: UUID, user_id: UUID, instrument: str, side: str, quantity: float
    ) -> dict:
        prices = await self._fetch_prices([instrument])
        quote = prices.get(instrument)
        if not quote:
            await self._reject_order(order_id, "No price available")
            return {"status": "REJECTED", "reason": "No price available"}

        match_price = quote["ask"] if side == "BUY" else quote["bid"]
        await self._match_and_route(order_id, instrument, side, quantity, match_price)
        return {"status": "SUBMITTED", "match_price": match_price}

    async def submit_limit_order(
        self, order_id: UUID, user_id: UUID, instrument: str, side: str,
        quantity: float, limit_price: float
    ) -> dict:
        book = self._get_book(instrument)
        book.add_limit_order(order_id, user_id, side, quantity, limit_price)
        logger.info("Limit order %s added to %s book", order_id, instrument)
        return {"status": "PENDING"}

    async def cancel_order(self, order_id: UUID) -> bool:
        for book in self._books.values():
            book.cancel_order(order_id)

        async with timed_db_operation("order_cancel"):
            async with async_session() as session:
                await session.execute(
                    update(ClientOrder)
                    .where(ClientOrder.id == order_id, ClientOrder.status == "PENDING")
                    .values(status="CANCELLED", updated_at=datetime.now(timezone.utc))
                )
                await session.commit()
        return True

    async def _check_loop(self):
        """Periodically check limit orders against current prices."""
        while True:
            try:
                if self._books:
                    instruments = list(self._books.keys())
                    prices = await self._fetch_prices(instruments)

                    for inst, quote in prices.items():
                        book = self._books.get(inst)
                        if not book:
                            continue
                        matches = book.check_fills(quote["bid"], quote["ask"])
                        for match in matches:
                            order = await self._load_order(match.order_id)
                            if order:
                                await self._match_and_route(
                                    match.order_id, inst, order["side"],
                                    order["quantity"], match.match_price
                                )
            except Exception:
                logger.exception("Check loop error")
            await asyncio.sleep(1.5)

    async def _fetch_prices(self, instruments: list[str]) -> dict:
        try:
            resp = await self._http.get(
                "/prices/current",
                params={"instruments": ",".join(instruments)},
            )
            resp.raise_for_status()
            return resp.json().get("prices", {})
        except Exception:
            logger.exception("Failed to fetch prices")
            return {}

    async def _match_and_route(
        self, order_id: UUID, instrument: str, side: str, quantity: float, match_price: float
    ):
        now = datetime.now(timezone.utc)

        async with timed_db_operation("order_match"):
            async with async_session() as session:
                await session.execute(
                    update(ClientOrder)
                    .where(ClientOrder.id == order_id)
                    .values(
                        status="MATCHED",
                        matched_price=match_price,
                        matched_at=now,
                        updated_at=now,
                    )
                )
                await _ops.apply_latency("db_write_delay")
                if _ops.should_fail("db_write_fail"):
                    raise Exception("Database write failed")
                await session.commit()

        lp_result = await self._execute_on_lp(instrument, side, quantity)

        async with timed_db_operation("lp_order_fill"):
            async with async_session() as session:
                lp_order = LpOrder(
                    client_order_id=order_id,
                    lp_name="simulator",
                    lp_order_id=lp_result.get("order_id", ""),
                    instrument=instrument,
                    side=side,
                    quantity=quantity,
                    submitted_price=match_price,
                    fill_price=lp_result.get("fill_price"),
                    status="FILLED" if lp_result.get("success") else "REJECTED",
                    rejection_reason=lp_result.get("rejection_reason"),
                    filled_at=now if lp_result.get("success") else None,
                )
                session.add(lp_order)

                final_status = "FILLED" if lp_result.get("success") else "REJECTED"
                await session.execute(
                    update(ClientOrder)
                    .where(ClientOrder.id == order_id)
                    .values(
                        status=final_status,
                        fill_price=lp_result.get("fill_price"),
                        filled_at=now if lp_result.get("success") else None,
                        rejection_reason=lp_result.get("rejection_reason"),
                        updated_at=now,
                    )
                )
                await _ops.apply_latency("db_write_delay")
                if _ops.should_fail("db_write_fail"):
                    raise Exception("Database write failed")
                await session.commit()

        logger.info("Order %s → %s (LP fill_price=%s)", order_id, final_status, lp_result.get("fill_price"))

    async def _execute_on_lp(self, instrument: str, side: str, quantity: float) -> dict:
        try:
            resp = await self._http.post(
                "/lp/execute",
                json={
                    "instrument": instrument,
                    "side": side,
                    "units": quantity,
                    "order_type": "MARKET",
                },
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.exception("LP execution failed")
            return {"success": False, "rejection_reason": str(e)}

    async def _reject_order(self, order_id: UUID, reason: str):
        async with timed_db_operation("order_reject"):
            async with async_session() as session:
                await session.execute(
                    update(ClientOrder)
                    .where(ClientOrder.id == order_id)
                    .values(
                        status="REJECTED",
                        rejection_reason=reason,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()

    async def _load_order(self, order_id: UUID) -> dict | None:
        async with timed_db_operation("order_load"):
            async with async_session() as session:
                result = await session.execute(
                    select(ClientOrder).where(ClientOrder.id == order_id)
                )
                order = result.scalar_one_or_none()
                if not order:
                    return None
                return {
                    "side": order.side,
                    "quantity": float(order.quantity),
                    "instrument": order.instrument,
                }