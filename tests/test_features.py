from datetime import timedelta
from unittest import TestCase

from btc_signal_system.features import FeatureEngine
from btc_signal_system.models import MarketDefinition, MarketSnapshot, utc_now
from btc_signal_system.signal_engine import SignalEngine


class FeatureEngineTests(TestCase):
    def test_entry_price_reserves_edge_after_fee_and_tick_rounding(self) -> None:
        snapshot = MarketSnapshot(
            market_id="btc-5m",
            label="BTC 5 分钟",
            timeframe_minutes=5,
            timestamp=utc_now(),
            current_price=None,
            target_price=None,
            metadata={
                "fees_enabled": True,
                "taker_fee_rate": 0.07,
                "up_tick_size": 0.001,
            },
        )
        engine = SignalEngine()

        entry_price = engine._entry_price(0.64, snapshot, "up")

        self.assertIsNotNone(entry_price)
        assert entry_price is not None
        self.assertGreaterEqual(engine._trade_edge(0.64, entry_price, snapshot) or 0.0, 2.0)
        self.assertLess(engine._trade_edge(0.64, entry_price + 0.001, snapshot) or 0.0, 2.0)

    def test_time_and_two_sided_market_probabilities_are_built(self) -> None:
        timestamp = utc_now()
        snapshot = MarketSnapshot(
            market_id="btc-5m",
            label="BTC 5 分钟",
            timeframe_minutes=5,
            timestamp=timestamp,
            current_price=101.0,
            target_price=100.0,
            contract_price=0.60,
            best_bid=0.59,
            best_ask=0.61,
            up_buy_price=0.61,
            up_sell_price=0.59,
            down_buy_price=0.41,
            down_sell_price=0.39,
            spot_price=101.0,
            perp_price=101.02,
            order_imbalance=0.2,
            trade_imbalance=0.3,
            metadata={"market_end_timestamp": timestamp.timestamp() + 60, "fees_enabled": False},
        )

        features = FeatureEngine().build(snapshot, [snapshot])

        self.assertAlmostEqual(features.market_probability or 0.0, 60.0)
        self.assertAlmostEqual(features.seconds_to_expiry or 0.0, 60.0, places=2)
        self.assertIsNotNone(features.time_probability)
        assert features.time_probability is not None
        self.assertGreater(features.time_probability, 85.0)
        signal = SignalEngine().score(MarketDefinition("btc-5m", "BTC 5 分钟", 5), snapshot, [snapshot])
        self.assertEqual(signal.trade_action, "hold")
        self.assertIsNone(signal.up_entry_price)
        self.assertIsNone(signal.down_entry_price)

    def test_signal_uses_time_probability_and_returns_trade_reference(self) -> None:
        timestamp = utc_now()
        history: list[MarketSnapshot] = []
        for seconds_ago, price in ((3, 100.70), (2, 100.76), (1, 100.86), (0, 101.0)):
            history.append(
                MarketSnapshot(
                    market_id="btc-5m",
                    label="BTC 5 分钟",
                    timeframe_minutes=5,
                    timestamp=timestamp - timedelta(seconds=seconds_ago),
                    current_price=price,
                    target_price=100.0,
                    contract_price=0.695,
                    best_bid=0.69,
                    best_ask=0.70,
                    up_buy_price=0.70,
                    up_sell_price=0.69,
                    down_buy_price=0.31,
                    down_sell_price=0.30,
                    spot_price=price,
                    perp_price=price + 0.02,
                    order_imbalance=0.2,
                    trade_imbalance=0.3,
                    metadata={"market_end_timestamp": timestamp.timestamp() + 60, "fees_enabled": False},
                )
            )
        definition = MarketDefinition("btc-5m", "BTC 5 分钟", 5)

        signal = SignalEngine().score(definition, history[-1], history)

        self.assertEqual(signal.direction, "long")
        self.assertGreater(signal.model_up_probability, 50.0)
        self.assertEqual(signal.trade_action, "buy_up")
        self.assertIsNotNone(signal.up_edge)
        self.assertIsNotNone(signal.up_entry_price)
        assert signal.up_entry_price is not None
        self.assertGreaterEqual(signal.up_entry_price, history[-1].up_buy_price or 1.0)
        retained_edge = signal.model_up_probability - signal.up_entry_price * 100.0
        self.assertGreaterEqual(retained_edge, 2.0)
        self.assertLess(retained_edge - 1.0, 2.0)
