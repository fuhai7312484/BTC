import time
from unittest import TestCase

from btc_signal_system.config import AppConfig
from btc_signal_system.models import MarketSnapshot, utc_now
from btc_signal_system.realtime import PolymarketRealtimeFeed
from btc_signal_system.service import MarketService


class MarketServiceTests(TestCase):
    def test_default_refresh_interval_is_one_second(self) -> None:
        self.assertEqual(AppConfig().poll_interval_seconds, 1.0)

    def test_default_http_timeout_allows_proxied_api_requests(self) -> None:
        self.assertEqual(AppConfig().request_timeout_seconds, 8.0)

    def test_default_history_covers_realtime_sampling_window(self) -> None:
        self.assertEqual(AppConfig().history_size, 1800)

    def test_default_mode_uses_live_data(self) -> None:
        self.assertFalse(AppConfig().use_simulation)

    def test_refresh_returns_5m_and_15m_signals(self) -> None:
        service = MarketService(AppConfig(use_simulation=True, poll_interval_seconds=0.1))
        payload = service.refresh()

        self.assertEqual(len(payload["markets"]), 2)
        self.assertEqual({market["timeframe_minutes"] for market in payload["markets"]}, {5, 15})
        for market in payload["markets"]:
            self.assertIn(market["direction"], {"long", "short", "neutral"})
            self.assertIn("snapshot", market)
            self.assertIsNotNone(market["snapshot"]["current_price"])
            self.assertIsNotNone(market["snapshot"]["target_price"])
            self.assertIsNotNone(market["snapshot"]["price_gap"])
            self.assertRegex(market["timestamp"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
            self.assertIsNotNone(market["snapshot"]["up_buy_price"])
            self.assertIsNotNone(market["snapshot"]["up_sell_price"])
            self.assertIsNotNone(market["snapshot"]["down_buy_price"])
            self.assertIsNotNone(market["snapshot"]["down_sell_price"])
            self.assertIsNotNone(market["snapshot"]["metadata"]["market_end_timestamp"])

    def test_market_state_returns_latest_signal_and_history(self) -> None:
        service = MarketService(AppConfig(use_simulation=True, poll_interval_seconds=0.1))
        service.refresh()

        state = service.market_state("btc-5m")

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["timeframe_minutes"], 5)
        self.assertIsNotNone(state["latest"])
        self.assertEqual(len(state["history"]), 1)
        self.assertIsNotNone(state["history"][0]["price_gap"])
        self.assertIsNone(service.market_state("missing-market"))

    def test_live_data_gaps_remain_null_and_are_not_reported_as_live(self) -> None:
        service = MarketService(
            AppConfig(use_simulation=False, polymarket_discovery_enabled=False)
        )
        service.polymarket.fetch = lambda definition: None
        service.realtime = PolymarketRealtimeFeed(cache_path=None)

        snapshot = service._collect_snapshot(service.market_definitions[0], None)

        self.assertIsNone(snapshot.current_price)
        self.assertIsNone(snapshot.contract_price)
        self.assertIsNone(snapshot.target_price)
        self.assertIsNone(snapshot.price_gap)
        self.assertEqual(snapshot.metadata["source"], "fallback")
        signal = service.signal_engine.score(service.market_definitions[0], snapshot, [snapshot])
        self.assertEqual(signal.direction, "neutral")
        self.assertEqual(signal.confidence, 0.0)

    def test_clob_websocket_quote_replaces_rest_quote_immediately(self) -> None:
        service = MarketService(AppConfig(use_simulation=False))
        service.realtime = PolymarketRealtimeFeed(cache_path=None)
        definition = service.market_definitions[0]
        state = service.states[definition.market_id]
        base = MarketSnapshot(
            market_id=definition.market_id,
            label=definition.label,
            timeframe_minutes=definition.timeframe_minutes,
            timestamp=utc_now(),
            current_price=66700.0,
            target_price=66650.0,
            contract_price=0.5,
            best_bid=0.49,
            best_ask=0.51,
            up_buy_price=0.51,
            up_sell_price=0.49,
            down_buy_price=0.51,
            down_sell_price=0.49,
            metadata={"source": "live", "slug": "btc-updown-5m-test"},
        )
        with service._lock:
            service._record_snapshot(state, base)
        service.clob_realtime.update_markets({definition.market_id: ("up-token", "down-token")})
        up_feed = service.clob_realtime._feeds[(definition.market_id, "up")]
        down_feed = service.clob_realtime._feeds[(definition.market_id, "down")]
        timestamp = int(time.time() * 1000)
        up_feed._handle_message(
            [
                {
                    "asset_id": "up-token",
                    "timestamp": timestamp,
                    "bids": [{"price": "0.44"}],
                    "asks": [{"price": "0.46"}],
                },
                {
                    "event_type": "best_bid_ask",
                    "asset_id": "up-token",
                    "timestamp": timestamp + 1,
                    "best_bid": "0.44",
                    "best_ask": "0.46",
                },
            ]
        )
        down_feed._handle_message(
            [
                {
                    "asset_id": "down-token",
                    "timestamp": timestamp,
                    "bids": [{"price": "0.53"}],
                    "asks": [{"price": "0.55"}],
                },
                {
                    "event_type": "best_bid_ask",
                    "asset_id": "down-token",
                    "timestamp": timestamp + 1,
                    "best_bid": "0.53",
                    "best_ask": "0.55",
                },
            ]
        )

        service._publish_clob_updates({definition.market_id})

        latest = state.latest
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.snapshot.up_buy_price, 0.46)
        self.assertEqual(latest.snapshot.up_sell_price, 0.44)
        self.assertEqual(latest.snapshot.down_buy_price, 0.55)
        self.assertEqual(latest.snapshot.down_sell_price, 0.53)
        self.assertEqual(latest.snapshot.contract_price, 0.45)
        self.assertEqual(latest.snapshot.metadata["quote_source"], "clob_websocket")
