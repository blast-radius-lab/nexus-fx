import asyncio
import logging
from datetime import datetime, timezone

from .. import _ops
from ..providers.base import BasePriceProvider, PriceQuote, SUPPORTED_INSTRUMENTS

logger = logging.getLogger(__name__)


class PriceCache:
    def __init__(self, provider: BasePriceProvider, poll_interval: float = 1.5):
        self._provider = provider
        self._poll_interval = poll_interval
        self._prices: dict[str, PriceQuote] = {}
        self._last_update: datetime | None = None
        self._task: asyncio.Task | None = None
        self._instruments = [i.symbol for i in SUPPORTED_INSTRUMENTS]

    @property
    def prices(self) -> dict[str, PriceQuote]:
        return dict(self._prices)

    @property
    def last_update(self) -> datetime | None:
        return self._last_update

    def get_price(self, instrument: str) -> PriceQuote | None:
        return self._prices.get(instrument)

    async def start(self):
        await self._provider.connect()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Price cache started with provider=%s", self._provider.provider_name())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._provider.disconnect()

    async def _poll_loop(self):
        while True:
            try:
                if not _ops.is_active("stale_prices"):
                    quotes = await self._provider.get_prices(self._instruments)
                    self._prices.update(quotes)
                    self._last_update = datetime.now(timezone.utc)
            except Exception:
                logger.exception("Price poll failed")
            await asyncio.sleep(self._poll_interval)
