from app.providers.base import PriceQuote, SUPPORTED_INSTRUMENTS


class TestPriceQuote:
    def test_from_bid_ask_calculates_mid(self):
        quote = PriceQuote.from_bid_ask("EUR_USD", 1.10000, 1.10020)
        assert quote.mid == round((1.10000 + 1.10020) / 2, 5)

    def test_from_bid_ask_calculates_spread(self):
        quote = PriceQuote.from_bid_ask("EUR_USD", 1.10000, 1.10020)
        assert quote.spread == round(1.10020 - 1.10000, 5)

    def test_from_bid_ask_rounds_to_five_decimals(self):
        quote = PriceQuote.from_bid_ask("EUR_USD", 1.123456789, 1.234567891)
        assert quote.bid == round(1.123456789, 5)
        assert quote.ask == round(1.234567891, 5)
        assert quote.mid == round((1.123456789 + 1.234567891) / 2, 5)

    def test_from_bid_ask_sets_instrument(self):
        quote = PriceQuote.from_bid_ask("GBP_USD", 1.2650, 1.2652)
        assert quote.instrument == "GBP_USD"

    def test_from_bid_ask_sets_timestamp(self):
        quote = PriceQuote.from_bid_ask("EUR_USD", 1.1000, 1.1002)
        assert quote.time is not None
        assert "T" in quote.time  # ISO format

    def test_jpy_pair_spread(self):
        quote = PriceQuote.from_bid_ask("USD_JPY", 154.500, 154.515)
        assert quote.spread == 0.015


class TestSupportedInstruments:
    def test_nine_instruments_defined(self):
        assert len(SUPPORTED_INSTRUMENTS) == 9

    def test_all_have_symbol_and_display_name(self):
        for inst in SUPPORTED_INSTRUMENTS:
            assert inst.symbol
            assert inst.display_name
            assert "/" in inst.display_name

    def test_jpy_pairs_have_pip_location_neg2(self):
        jpy_pairs = [i for i in SUPPORTED_INSTRUMENTS if "JPY" in i.symbol]
        assert len(jpy_pairs) == 2
        for pair in jpy_pairs:
            assert pair.pip_location == -2

    def test_non_jpy_pairs_have_pip_location_neg4(self):
        non_jpy = [i for i in SUPPORTED_INSTRUMENTS if "JPY" not in i.symbol]
        for pair in non_jpy:
            assert pair.pip_location == -4
