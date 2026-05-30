import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Numeric, String, Text, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship

from ..config import settings


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    email = Column(String(255))
    balance = Column(Numeric(15, 2), default=100000.00)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime(timezone=True))

    orders = relationship("ClientOrder", back_populates="user")


class ClientOrder(Base):
    __tablename__ = "client_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    instrument = Column(String(20), nullable=False)
    side = Column(String(4), nullable=False)
    order_type = Column(String(10), nullable=False)
    quantity = Column(Numeric(15, 2), nullable=False)
    limit_price = Column(Numeric(15, 6))
    stop_price = Column(Numeric(15, 6))
    status = Column(String(20), nullable=False, default="PENDING")
    matched_price = Column(Numeric(15, 6))
    matched_at = Column(DateTime(timezone=True))
    fill_price = Column(Numeric(15, 6))
    filled_at = Column(DateTime(timezone=True))
    rejection_reason = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="orders")
    lp_order = relationship("LpOrder", back_populates="client_order", uselist=False)


class LpOrder(Base):
    __tablename__ = "lp_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_order_id = Column(UUID(as_uuid=True), ForeignKey("client_orders.id"), nullable=False)
    lp_name = Column(String(50), nullable=False, default="simulator")
    lp_order_id = Column(String(100))
    instrument = Column(String(20), nullable=False)
    side = Column(String(4), nullable=False)
    quantity = Column(Numeric(15, 2), nullable=False)
    submitted_price = Column(Numeric(15, 6))
    fill_price = Column(Numeric(15, 6))
    status = Column(String(20), nullable=False, default="SUBMITTED")
    rejection_reason = Column(Text)
    submitted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    filled_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    client_order = relationship("ClientOrder", back_populates="lp_order")


engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
