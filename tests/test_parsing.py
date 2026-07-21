from unittest import TestCase

from btc_signal_system.parsing import infer_market_bias, infer_timeframe_minutes, parse_target_price


class ParsingTests(TestCase):
    def test_parse_target_price_with_dollars(self) -> None:
        self.assertEqual(parse_target_price("Will BTC be above $68,500 in 5 minutes?"), 68500.0)

    def test_parse_target_price_with_suffix(self) -> None:
        self.assertEqual(parse_target_price("Bitcoin over 70k by close"), 70000.0)

    def test_infer_market_bias(self) -> None:
        self.assertEqual(infer_market_bias("BTC above 65000"), "long")
        self.assertEqual(infer_market_bias("BTC below 65000"), "short")

    def test_infer_timeframe(self) -> None:
        self.assertEqual(infer_timeframe_minutes("BTC 15m market", 5), 15)

