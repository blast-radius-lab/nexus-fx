from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class PriceQuote:
    instrument: str
    bid: float
    ask: float
    mid: float
    spread: float
    time: str

    @classmethod
    def from_bid_ask(cls, instrument: str, bid: float, ask: float) -> "PriceQuote":
        return cls(
            instrument=instrument,
            bid=round(bid, 5),
            ask=round(ask, 5),
            mid=round((bid + ask) / 2, 5),
            spread=round(ask - bid, 5),
            time=datetime.now(timezone.utc).isoformat(),
        )


@dataclass
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class ExecutionResult:
    success: bool
    order_id: str = ""
    fill_price: float = 0.0
    filled_units: float = 0.0
    rejection_reason: str = ""


@dataclass
class InstrumentInfo:
    symbol: str
    display_name: str
    pip_location: int = -4


SUPPORTED_INSTRUMENTS = [
    InstrumentInfo("EUR_USD", "EUR/USD", -4),
    InstrumentInfo("GBP_USD", "GBP/USD", -4),
    InstrumentInfo("USD_JPY", "USD/JPY", -2),
    InstrumentInfo("USD_CHF", "USD/CHF", -4),
    InstrumentInfo("AUD_USD", "AUD/USD", -4),
    InstrumentInfo("NZD_USD", "NZD/USD", -4),
    InstrumentInfo("USD_CAD", "USD/CAD", -4),
    InstrumentInfo("EUR_GBP", "EUR/GBP", -4),
    InstrumentInfo("EUR_JPY", "EUR/JPY", -2),
]


class BasePriceProvider(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def get_prices(self, instruments: list[str]) -> dict[str, PriceQuote]: ...

    @abstractmethod
    async def get_candles(
        self, instrument: str, granularity: str, count: int
    ) -> list[Candle]: ...

    @abstractmethod
    async def execute_order(
        self, instrument: str, side: str, units: float, order_type: str = "MARKET",
        limit_price: float | None = None,
    ) -> ExecutionResult: ...

    @abstractmethod
    def provider_name(self) -> str: ...
