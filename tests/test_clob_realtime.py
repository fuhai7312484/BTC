import json
import time
from unittest import TestCase

from btc_signal_system.clob_realtime import PolymarketClobFeed, PolymarketClobRouter


class ClobRealtimeTests(TestCase):
    def test_book_and_price_change_events_update_market_quotes(self) -> None:
        updates: list[set[str]] = []
        feed = PolymarketClobFeed(on_update=updates.append)
        feed.update_markets({"btc-5m": ("up-token", "down-token")})
        timestamp = int(time.time() * 1000)

        feed._handle_message(
            json.dumps(
                [
                    {
                        "asset_id": "up-token",
                        "timestamp": timestamp,
                        "tick_size": "0.01",
                        "bids": [{"price": "0.31"}, {"price": "0.35"}],
                        "asks": [{"price": "0.42"}, {"price": "0.39"}],
                    },
                    {
                        "asset_id": "down-token",
                        "timestamp": timestamp,
                        "bids": [{"price": "0.57"}],
                        "asks": [{"price": "0.63"}],
                    },
                ]
            )
        )

        quote = feed.market_quote("btc-5m", disconnected_max_age_seconds=10**12)
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote["up_buy_price"], 0.39)
        self.assertEqual(quote["up_sell_price"], 0.35)
        self.assertEqual(quote["down_buy_price"], 0.63)
        self.assertEqual(quote["down_sell_price"], 0.57)
        self.assertEqual(quote["contract_price"], 0.37)
        self.assertEqual(quote["up_tick_size"], 0.01)
        self.assertEqual(updates, [{"btc-5m"}])

        feed._handle_message(
            {
                "event_type": "price_change",
                "timestamp": timestamp + 1,
                "price_changes": [
                    {
                        "asset_id": "up-token",
                        "best_bid": "0.36",
                        "best_ask": "0.40",
                    }
                ],
            }
        )
        quote = feed.market_quote("btc-5m", disconnected_max_age_seconds=10**12)
        assert quote is not None
        self.assertEqual((quote["up_buy_price"], quote["up_sell_price"]), (0.40, 0.36))

        feed._handle_message(
            {
                "event_type": "tick_size_change",
                "asset_id": "up-token",
                "new_tick_size": "0.001",
                "timestamp": timestamp + 2,
            }
        )
        quote = feed.market_quote("btc-5m", disconnected_max_age_seconds=10**12)
        assert quote is not None
        self.assertEqual(quote["up_tick_size"], 0.001)

    def test_market_switch_removes_old_token_quotes(self) -> None:
        feed = PolymarketClobFeed()
        feed.update_markets({"btc-5m": ("old-up", "old-down")})
        feed._handle_message(
            {
                "asset_id": "old-up",
                "timestamp": int(time.time() * 1000),
                "bids": [{"price": "0.2"}],
                "asks": [{"price": "0.3"}],
            }
        )
        feed.update_markets({"btc-5m": ("new-up", "new-down")})

        self.assertIsNone(feed.market_quote("btc-5m", disconnected_max_age_seconds=10**12))

    def test_router_uses_an_independent_feed_for_each_market(self) -> None:
        router = PolymarketClobRouter()
        router.update_markets(
            {
                "btc-5m": ("5m-up", "5m-down"),
                "btc-15m": ("15m-up", "15m-down"),
            }
        )

        status = router.status()
        self.assertEqual(len(router._feeds), 4)
        self.assertEqual(status["market_count"], 2)
        self.assertEqual(status["connection_count"], 4)
        self.assertEqual(status["token_count"], 4)

    def test_wire_depth_changes_and_best_bid_ask_are_processed(self) -> None:
        feed = PolymarketClobFeed()
        feed.update_markets({"btc-5m": ("up-token", "down-token")})
        timestamp = int(time.time() * 1000)
        feed._handle_message(
            [
                {
                    "asset_id": "up-token",
                    "timestamp": timestamp,
                    "bids": [{"price": "0.30"}],
                    "asks": [{"price": "0.40"}],
                },
                {
                    "asset_id": "down-token",
                    "timestamp": timestamp,
                    "bids": [{"price": "0.60"}],
                    "asks": [{"price": "0.70"}],
                },
            ]
        )
        feed._handle_message(
            json.dumps(
                {
                    "event_type": "price_change",
                    "timestamp": timestamp + 1,
                    "price_changes": [
                        {
                            "asset_id": "up-token",
                            "best_bid": "0.31",
                            "best_ask": "0.39",
                            "side": "BUY",
                            "price": "0.31",
                            "size": "25",
                        }
                    ],
                }
            )
        )
        quote = feed.market_quote("btc-5m", disconnected_max_age_seconds=10**12)
        assert quote is not None
        self.assertEqual((quote["up_sell_price"], quote["up_buy_price"]), (0.31, 0.39))

        feed._handle_message(
            json.dumps(
                {
                    "event_type": "best_bid_ask",
                    "asset_id": "up-token",
                    "best_bid": "0.32",
                    "best_ask": "0.38",
                    "timestamp": timestamp + 2,
                }
            )
        )
        quote = feed.market_quote("btc-5m", disconnected_max_age_seconds=10**12)
        assert quote is not None
        self.assertEqual((quote["up_sell_price"], quote["up_buy_price"]), (0.32, 0.38))

    def test_depth_and_trade_flow_are_combined_across_up_and_down(self) -> None:
        feed = PolymarketClobFeed()
        feed.update_markets({"btc-5m": ("up-token", "down-token")})
        timestamp = int(time.time() * 1000)
        feed._handle_message(
            [
                {
                    "asset_id": "up-token",
                    "timestamp": timestamp,
                    "bids": [{"price": "0.48", "size": "80"}],
                    "asks": [{"price": "0.52", "size": "20"}],
                },
                {
                    "asset_id": "down-token",
                    "timestamp": timestamp + 1,
                    "bids": [{"price": "0.48", "size": "20"}],
                    "asks": [{"price": "0.52", "size": "80"}],
                },
            ]
        )
        for asset_id, side, size, offset in (
            ("up-token", "BUY", "30", 2),
            ("up-token", "SELL", "10", 3),
            ("down-token", "BUY", "5", 4),
            ("down-token", "SELL", "15", 5),
        ):
            feed._handle_message(
                {
                    "event_type": "last_trade_price",
                    "asset_id": asset_id,
                    "side": side,
                    "size": size,
                    "price": "0.50",
                    "timestamp": timestamp + offset,
                }
            )

        quote = feed.market_quote("btc-5m", disconnected_max_age_seconds=10**12)

        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertAlmostEqual(quote["up_order_imbalance"], 0.6)
        self.assertAlmostEqual(quote["down_order_imbalance"], -0.6)
        self.assertAlmostEqual(quote["order_imbalance"], 0.6)
        self.assertAlmostEqual(quote["trade_imbalance"], 0.5)

    def test_stale_best_bid_ask_event_cannot_replace_current_quote(self) -> None:
        feed = PolymarketClobFeed(max_event_lag_seconds=2.0)
        feed.update_markets({"btc-5m": ("up-token", "down-token")})
        feed._handle_message(
            {
                "event_type": "best_bid_ask",
                "asset_id": "up-token",
                "best_bid": "0.32",
                "best_ask": "0.38",
                "timestamp": int((time.time() - 10.0) * 1000),
            }
        )

        self.assertIsNone(feed.market_quote("btc-5m", disconnected_max_age_seconds=10**12))
        self.assertEqual(feed.status()["dropped_stale_events"], 1)

    def test_out_of_order_stale_event_does_not_revoke_synced_quote(self) -> None:
        feed = PolymarketClobFeed(max_event_lag_seconds=2.0)
        feed.update_markets({"btc-5m": ("up-token", "down-token")})
        timestamp = int(time.time() * 1000)
        for offset in (0, 1):
            feed._handle_message(
                {
                    "event_type": "best_bid_ask",
                    "asset_id": "up-token",
                    "best_bid": "0.32",
                    "best_ask": "0.38",
                    "timestamp": timestamp + offset,
                }
            )
        feed._handle_message(
            {
                "event_type": "best_bid_ask",
                "asset_id": "up-token",
                "best_bid": "0.10",
                "best_ask": "0.90",
                "timestamp": int((time.time() - 10.0) * 1000),
            }
        )

        quote = feed.market_quote("btc-5m", disconnected_max_age_seconds=10**12)
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual((quote["up_sell_price"], quote["up_buy_price"]), (0.32, 0.38))
        self.assertTrue(feed.status()["synced"])
