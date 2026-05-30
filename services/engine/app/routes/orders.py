from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import _ops
from ..models.database import ClientOrder, get_session

router = APIRouter()

_engine = None


def init_router(matching_engine):
    global _engine
    _engine = matching_engine


class SubmitOrderRequest(BaseModel):
    user_id: str
    instrument: str
    side: str
    order_type: str
    quantity: float
    limit_price: float | None = None


@router.post("/orders/submit")
async def submit_order(
    req: SubmitOrderRequest,
    session: AsyncSession = Depends(get_session),
):
    user_id = UUID(req.user_id)

    order = ClientOrder(
        user_id=user_id,
        instrument=req.instrument,
        side=req.side.upper(),
        order_type=req.order_type.upper(),
        quantity=req.quantity,
        limit_price=req.limit_price,
        status="PENDING",
    )
    session.add(order)
    await _ops.apply_latency("db_write_delay")
    if _ops.should_fail("db_write_fail"):
        raise HTTPException(status_code=500, detail="Database write failed")
    await session.commit()
    await session.refresh(order)

    if req.order_type.upper() == "MARKET":
        result = await _engine.submit_market_order(
            order.id, user_id, req.instrument, req.side.upper(), req.quantity
        )
    elif req.order_type.upper() == "LIMIT":
        if req.limit_price is None:
            return {"error": "limit_price required for LIMIT orders"}, 400
        result = await _engine.submit_limit_order(
            order.id, user_id, req.instrument, req.side.upper(),
            req.quantity, req.limit_price
        )
    else:
        return {"error": f"Unsupported order type: {req.order_type}"}, 400

    return {"order_id": str(order.id), **result}


@router.get("/orders")
async def list_orders(
    user_id: str,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    query = (
        select(ClientOrder)
        .options(selectinload(ClientOrder.lp_order))
        .where(ClientOrder.user_id == UUID(user_id))
        .order_by(ClientOrder.created_at.desc())
    )
    if status:
        query = query.where(ClientOrder.status == status.upper())

    result = await session.execute(query)
    orders = result.scalars().all()

    return {
        "orders": [_serialize_order(o) for o in orders]
    }


@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(ClientOrder)
        .options(selectinload(ClientOrder.lp_order))
        .where(ClientOrder.id == UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        return {"error": "Order not found"}, 404
    return _serialize_order(order)


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str):
    success = await _engine.cancel_order(UUID(order_id))
    return {"cancelled": success}


@router.get("/trades/open")
async def open_trades(
    user_id: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(ClientOrder)
        .options(selectinload(ClientOrder.lp_order))
        .where(
            ClientOrder.user_id == UUID(user_id),
            ClientOrder.status == "FILLED",
        )
        .order_by(ClientOrder.filled_at.desc())
    )
    orders = result.scalars().all()
    return {"trades": [_serialize_order(o) for o in orders]}


@router.get("/trades/closed")
async def closed_trades(
    user_id: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(ClientOrder)
        .options(selectinload(ClientOrder.lp_order))
        .where(
            ClientOrder.user_id == UUID(user_id),
            ClientOrder.status.in_(["FILLED", "REJECTED", "CANCELLED"]),
        )
        .order_by(ClientOrder.created_at.desc())
        .limit(100)
    )
    orders = result.scalars().all()
    return {"trades": [_serialize_order(o) for o in orders]}


@router.get("/account/summary")
async def account_summary(
    user_id: str,
    session: AsyncSession = Depends(get_session),
):
    from ..models.database import User
    result = await session.execute(
        select(User).where(User.id == UUID(user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        return {"error": "User not found"}, 404

    filled_result = await session.execute(
        select(ClientOrder).where(
            ClientOrder.user_id == UUID(user_id),
            ClientOrder.status == "FILLED",
        )
    )
    filled_orders = filled_result.scalars().all()
    total_orders = len(filled_orders)

    return {
        "user_id": str(user.id),
        "username": user.username,
        "balance": float(user.balance),
        "open_trades": total_orders,
        "currency": "USD",
    }


def _serialize_order(order: ClientOrder) -> dict:
    d = {
        "id": str(order.id),
        "user_id": str(order.user_id),
        "instrument": order.instrument,
        "side": order.side,
        "order_type": order.order_type,
        "quantity": float(order.quantity),
        "limit_price": float(order.limit_price) if order.limit_price else None,
        "status": order.status,
        "matched_price": float(order.matched_price) if order.matched_price else None,
        "matched_at": order.matched_at.isoformat() if order.matched_at else None,
        "fill_price": float(order.fill_price) if order.fill_price else None,
        "filled_at": order.filled_at.isoformat() if order.filled_at else None,
        "rejection_reason": order.rejection_reason,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }
    if order.lp_order:
        lp = order.lp_order
        d["lp_order"] = {
            "id": str(lp.id),
            "lp_name": lp.lp_name,
            "lp_order_id": lp.lp_order_id,
            "status": lp.status,
            "fill_price": float(lp.fill_price) if lp.fill_price else None,
            "rejection_reason": lp.rejection_reason,
        }
    return d
