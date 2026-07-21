import json
from unittest import TestCase

from btc_signal_system.realtime import PolymarketRealtimeFeed


class RealtimeFeedTests(TestCase):
    def test_subscription_snapshot_provides_current_and_window_open_prices(self) -> None:
        feed = PolymarketRealtimeFeed(cache_path=None)
        feed._handle_message(
            json.dumps(
                {
                    "topic": "crypto_prices",
                    "type": "subscribe",
                    "payload": {
                        "symbol": "btc/usd",
                        "data": [
                            {"timestamp": 1784645400000, "value": 66750.25},
                            {"timestamp": 1784645401000, "value": 66751.00},
                        ],
                    },
                }
            )
        )

        self.assertEqual(feed.price_at(1784645400), 66750.25)
        self.assertEqual(feed.current_price(max_age_seconds=10**9), 66751.00)
