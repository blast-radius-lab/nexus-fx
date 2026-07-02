import time
from uuid import uuid4

from app.matching.order_book import BookEntry, OrderBook


class TestBookEntry:
    def test_buy_entry_sort_key_negates_price(self):
        entry = BookEntry.buy(uuid4(), uuid4(), 100.0, 1.1050)
        assert entry.sort_key[0] == -1.1050
        assert entry.side == "BUY"

    def test_sell_entry_sort_key_uses_raw_price(self):
        entry = BookEntry.sell(uuid4(), uuid4(), 100.0, 1.1050)
        assert entry.sort_key[0] == 1.1050
        assert entry.side == "SELL"

    def test_buy_entries_sort_highest_price_first(self):
        low = BookEntry.buy(uuid4(), uuid4(), 100.0, 1.1000)
        high = BookEntry.buy(uuid4(), uuid4(), 100.0, 1.1050)
        assert high < low  # Negated price: -1.1050 < -1.1000

    def test_sell_entries_sort_lowest_price_first(self):
        low = BookEntry.sell(uuid4(), uuid4(), 100.0, 1.1000)
        high = BookEntry.sell(uuid4(), uuid4(), 100.0, 1.1050)
        assert low < high

    def test_same_price_sorts_by_time(self):
        first = BookEntry.buy(uuid4(), uuid4(), 100.0, 1.1000)
        time.sleep(0.01)
        second = BookEntry.buy(uuid4(), uuid4(), 100.0, 1.1000)
        assert first < second  # Earlier timestamp wins


class TestOrderBook:
    def test_add_buy_order_increases_bid_depth(self):
        book = OrderBook("EUR_USD")
        assert book.bid_depth == 0
        book.add_limit_order(uuid4(), uuid4(), "BUY", 100.0, 1.1050)
        assert book.bid_depth == 1

    def test_add_sell_order_increases_ask_depth(self):
        book = OrderBook("EUR_USD")
        assert book.ask_depth == 0
        book.add_limit_order(uuid4(), uuid4(), "SELL", 100.0, 1.1060)
        assert book.ask_depth == 1

    def test_cancel_order_reduces_depth(self):
        book = OrderBook("EUR_USD")
        oid = uuid4()
        book.add_limit_order(oid, uuid4(), "BUY", 100.0, 1.1050)
        assert book.bid_depth == 1
        book.cancel_order(oid)
        assert book.bid_depth == 0

    def test_buy_fills_when_limit_above_ask(self):
        book = OrderBook("EUR_USD")
        oid = uuid4()
        book.add_limit_order(oid, uuid4(), "BUY", 100.0, 1.1050)

        results = book.check_fills(current_bid=1.1040, current_ask=1.1045)
        assert len(results) == 1
        assert results[0].matched is True
        assert results[0].order_id == oid
        assert results[0].match_price == 1.1045  # Fills at ask

    def test_buy_does_not_fill_when_limit_below_ask(self):
        book = OrderBook("EUR_USD")
        book.add_limit_order(uuid4(), uuid4(), "BUY", 100.0, 1.1030)

        results = book.check_fills(current_bid=1.1040, current_ask=1.1045)
        assert len(results) == 0

    def test_sell_fills_when_limit_below_bid(self):
        book = OrderBook("EUR_USD")
        oid = uuid4()
        book.add_limit_order(oid, uuid4(), "SELL", 100.0, 1.1030)

        results = book.check_fills(current_bid=1.1040, current_ask=1.1045)
        assert len(results) == 1
        assert results[0].matched is True
        assert results[0].order_id == oid
        assert results[0].match_price == 1.1040  # Fills at bid

    def test_sell_does_not_fill_when_limit_above_bid(self):
        book = OrderBook("EUR_USD")
        book.add_limit_order(uuid4(), uuid4(), "SELL", 100.0, 1.1050)

        results = book.check_fills(current_bid=1.1040, current_ask=1.1045)
        assert len(results) == 0

    def test_cancelled_orders_skipped_during_fill_check(self):
        book = OrderBook("EUR_USD")
        oid1 = uuid4()
        oid2 = uuid4()
        book.add_limit_order(oid1, uuid4(), "BUY", 100.0, 1.1060)
        book.add_limit_order(oid2, uuid4(), "BUY", 100.0, 1.1050)
        book.cancel_order(oid1)

        results = book.check_fills(current_bid=1.1040, current_ask=1.1045)
        assert len(results) == 1
        assert results[0].order_id == oid2

    def test_multiple_orders_fill_in_price_priority(self):
        book = OrderBook("EUR_USD")
        low_oid = uuid4()
        high_oid = uuid4()
        book.add_limit_order(low_oid, uuid4(), "BUY", 100.0, 1.1050)
        book.add_limit_order(high_oid, uuid4(), "BUY", 100.0, 1.1060)

        results = book.check_fills(current_bid=1.1040, current_ask=1.1045)
        assert len(results) == 2
        assert results[0].order_id == high_oid  # Higher price fills first
        assert results[1].order_id == low_oid

    def test_mixed_buy_and_sell_fills(self):
        book = OrderBook("EUR_USD")
        buy_oid = uuid4()
        sell_oid = uuid4()
        book.add_limit_order(buy_oid, uuid4(), "BUY", 100.0, 1.1050)
        book.add_limit_order(sell_oid, uuid4(), "SELL", 100.0, 1.1030)

        results = book.check_fills(current_bid=1.1040, current_ask=1.1045)
        assert len(results) == 2
        filled_ids = {r.order_id for r in results}
        assert buy_oid in filled_ids
        assert sell_oid in filled_ids

    def test_empty_book_returns_no_fills(self):
        book = OrderBook("EUR_USD")
        results = book.check_fills(current_bid=1.1040, current_ask=1.1045)
        assert results == []
