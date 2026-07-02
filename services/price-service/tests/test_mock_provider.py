import pytest

from app.providers.mock import MockProvider, BASE_PRICES


@pytest.fixture
def provider():
    return MockProvider()


class TestMockProviderPrices:
    @pytest.mark.asyncio
    async def test_returns_quotes_for_requested_instruments(self, provider):
        quotes = await provider.get_prices(["EUR_USD", "GBP_USD"])
        assert "EUR_USD" in quotes
        assert "GBP_USD" in quotes
        assert len(quotes) == 2

    @pytest.mark.asyncio
    async def test_skips_unknown_instruments(self, provider):
        quotes = await provider.get_prices(["FAKE_PAIR"])
        assert len(quotes) == 0

    @pytest.mark.asyncio
    async def test_bid_less_than_ask(self, provider):
        quotes = await provider.get_prices(["EUR_USD"])
        q = quotes["EUR_USD"]
        assert q.bid < q.ask

    @pytest.mark.asyncio
    async def test_price_walks_from_base(self, provider):
        quotes = await provider.get_prices(["EUR_USD"])
        q = quotes["EUR_USD"]
        base = BASE_PRICES["EUR_USD"]
        assert abs(q.mid - base) < base * 0.01  # Within 1% of base on first tick


class TestMockProviderExecution:
    @pytest.mark.asyncio
    async def test_buy_execution_succeeds(self, provider):
        result = await provider.execute_order("EUR_USD", "BUY", 100000.0)
        assert result.success is True
        assert result.fill_price > 0
        assert result.filled_units == 100000.0
        assert result.order_id.startswith("mock-")

    @pytest.mark.asyncio
    async def test_sell_execution_succeeds(self, provider):
        result = await provider.execute_order("EUR_USD", "SELL", 50000.0)
        assert result.success is True
        assert result.filled_units == 50000.0

    @pytest.mark.asyncio
    async def test_buy_fills_higher_than_sell(self, provider):
        buy = await provider.execute_order("EUR_USD", "BUY", 100000.0)
        sell = await provider.execute_order("EUR_USD", "SELL", 100000.0)
        assert buy.fill_price > sell.fill_price

    @pytest.mark.asyncio
    async def test_unknown_instrument_rejected(self, provider):
        result = await provider.execute_order("FAKE_PAIR", "BUY", 100000.0)
        assert result.success is False
        assert "Unknown instrument" in result.rejection_reason


class TestMockProviderCandles:
    @pytest.mark.asyncio
    async def test_returns_requested_count(self, provider):
        candles = await provider.get_candles("EUR_USD", "H1", 10)
        assert len(candles) == 10

    @pytest.mark.asyncio
    async def test_candles_ordered_oldest_first(self, provider):
        candles = await provider.get_candles("EUR_USD", "H1", 5)
        for i in range(len(candles) - 1):
            assert candles[i].time < candles[i + 1].time

    @pytest.mark.asyncio
    async def test_high_gte_low(self, provider):
        candles = await provider.get_candles("EUR_USD", "H1", 20)
        for c in candles:
            assert c.high >= c.low

    @pytest.mark.asyncio
    async def test_close_within_high_low_range(self, provider):
        candles = await provider.get_candles("EUR_USD", "H1", 20)
        for c in candles:
            assert c.low <= c.close <= c.high
