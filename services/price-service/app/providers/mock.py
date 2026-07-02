import random
import uuid
from datetime import datetime, timedelta, timezone

from .base import (
    BasePriceProvider,
    Candle,
    ExecutionResult,
    PriceQuote,
)

BASE_PRICES = {
    "EUR_USD": 1.0850,
    "GBP_USD": 1.2650,
    "USD_JPY": 154.50,
    "USD_CHF": 0.8820,
    "AUD_USD": 0.6580,
    "NZD_USD": 0.6120,
    "USD_CAD": 1.3650,
    "EUR_GBP": 0.8580,
    "EUR_JPY": 167.60,
}

SPREADS = {
    "EUR_USD": 0.00015,
    "GBP_USD": 0.00020,
    "USD_JPY": 0.015,
    "USD_CHF": 0.00020,
    "AUD_USD": 0.00020,
    "NZD_USD": 0.00025,
    "USD_CAD": 0.00020,
    "EUR_GBP": 0.00020,
    "EUR_JPY": 0.020,
}


class MockProvider(BasePriceProvider):
    def __init__(self):
        self._prices: dict[str, float] = dict(BASE_PRICES)

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    def _walk_price(self, instrument: str) -> float:
        current = self._prices[instrument]
        is_jpy = "JPY" in instrument
        tick = 0.01 if is_jpy else 0.00005
        change = random.gauss(0, tick * 3)
        max_drift = current * 0.005
        change = max(-max_drift, min(max_drift, change))
        new_price = current + change
        self._prices[instrument] = new_price
        return new_price

    async def get_prices(self, instruments: list[str]) -> dict[str, PriceQuote]:
        result = {}
        for inst in instruments:
            if inst not in self._prices:
                continue
            mid = self._walk_price(inst)
            half_spread = SPREADS.get(inst, 0.00015) / 2
            result[inst] = PriceQuote.from_bid_ask(inst, mid - half_spread, mid + half_spread)
        return result

    async def get_candles(
        self, instrument: str, granularity: str, count: int
    ) -> list[Candle]:
        granularity_minutes = {
            "M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D": 1440,
        }
        minutes = granularity_minutes.get(granularity, 60)
        now = datetime.now(timezone.utc)
        base = self._prices.get(instrument, 1.0)
        candles = []
        price = base * 0.998

        for i in range(count):
            t = now - timedelta(minutes=minutes * (count - i))
            tick = 0.01 if "JPY" in instrument else 0.00005
            o = price
            h = o + abs(random.gauss(0, tick * 10))
            lo = o - abs(random.gauss(0, tick * 10))
            c = random.uniform(lo, h)
            price = c
            candles.append(Candle(
                time=t.isoformat(),
                open=round(o, 5),
                high=round(h, 5),
                low=round(lo, 5),
                close=round(c, 5),
                volume=round(random.uniform(1000, 50000), 0),
            ))
        return candles

    async def execute_order(
        self, instrument: str, side: str, units: float, order_type: str = "MARKET",
        limit_price: float | None = None,
    ) -> ExecutionResult:
        if instrument not in self._prices:
            return ExecutionResult(
                success=False,
                rejection_reason=f"Unknown instrument: {instrument}",
            )

        mid = self._prices[instrument]
        half_spread = SPREADS.get(instrument, 0.00015) / 2
        fill_price = (mid + half_spread) if side == "BUY" else (mid - half_spread)

        return ExecutionResult(
            success=True,
            order_id=f"mock-{uuid.uuid4().hex[:12]}",
            fill_price=round(fill_price, 5),
            filled_units=units,
        )

    def provider_name(self) -> str:
        return "mock"
