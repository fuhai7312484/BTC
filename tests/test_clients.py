import ssl
import urllib.error
from unittest import TestCase
from unittest.mock import patch

from btc_signal_system.clients import (
    HttpJsonClient,
    PolymarketClient,
    _clob_market_candidates,
    _extract_book,
    _matches_market,
    _market_schedule,
    _select_outcome,
)
from btc_signal_system.config import AppConfig
from btc_signal_system.models import MarketDefinition


class FakeHttpJsonClient:
    def get_json(self, url: str):
        if "/events/slug/" in url:
            return {
                "priceToBeat": "64750.25",
                "endDate": "2026-07-20T16:00:00Z",
                "markets": [
                    {
                        "slug": "btc-updown-5m-1784559750",
                        "question": "Bitcoin Up or Down - 5 Minutes",
                        "description": "Will Bitcoin be above $65,000 at the end of this window?",
                        "outcomes": '["Up", "Down"]',
                        "clobTokenIds": '["up-token", "down-token"]',
                        "outcomePrices": '["0.42", "0.58"]',
                        "bestBid": "0.35",
                        "bestAsk": "0.65",
                    }
                ]
            }
        if "/book?token_id=up-token" in url:
            return {"bids": [{"price": "0.41", "size": "10"}], "asks": [{"price": "0.47", "size": "8"}]}
        if "/book?token_id=down-token" in url:
            return {"bids": [{"price": "0.53", "size": "9"}], "asks": [{"price": "0.59", "size": "7"}]}
        if "/price?" in url and "token_id=up-token" in url and "side=BUY" in url:
            return {"price": "0.48"}
        if "/price?" in url and "token_id=up-token" in url and "side=SELL" in url:
            return {"price": "0.40"}
        if "/price?" in url and "token_id=down-token" in url and "side=BUY" in url:
            return {"price": "0.60"}
        if "/price?" in url and "token_id=down-token" in url and "side=SELL" in url:
            return {"price": "0.52"}
        if "/last-trade-price?token_id=up-token" in url:
            return {"price": "0.46", "side": "BUY"}
        if url.endswith("/time"):
            return 1784564300
        if "/markets/slug/" in url:
            return []
        if "/markets?" in url:
            return []
        raise AssertionError(f"unexpected URL: {url}")


class ClientTests(TestCase):
    def test_short_market_match_rejects_unrelated_bitcoin_market(self) -> None:
        definition = MarketDefinition(
            market_id="btc-5m",
            label="BTC 5m",
            timeframe_minutes=5,
        )

        self.assertTrue(
            _matches_market(
                {"slug": "btc-updown-5m-1784647800", "question": "Bitcoin Up or Down"},
                definition,
            )
        )
        self.assertFalse(
            _matches_market(
                {
                    "slug": "will-bitcoin-replace-sha-256-before-2027",
                    "question": "Will Bitcoin replace SHA-256 before 2027?",
                },
                definition,
            )
        )

    def test_http_client_uses_curl_when_python_rejects_proxy_certificate(self) -> None:
        client = HttpJsonClient(0.1)
        client._curl_path = "/usr/bin/curl"
        client._prefer_curl = False
        certificate_error = ssl.SSLCertVerificationError(1, "certificate verify failed")

        with patch(
            "btc_signal_system.clients.urllib.request.urlopen",
            side_effect=urllib.error.URLError(certificate_error),
        ), patch.object(client, "_get_text_with_curl", return_value='{"ok": true}') as curl_get:
            payload = client.get_json("https://example.test/data")

        self.assertEqual(payload, {"ok": True})
        self.assertTrue(client._prefer_curl)
        curl_get.assert_called_once()

    def test_market_schedule_uses_each_polymarket_end_date(self) -> None:
        base_market = {
            "slug": "btc-updown-5m-1784564100",
            "startTime": "2026-07-20T16:15:00Z",
        }
        five_minute = _market_schedule(
            {**base_market, "endDate": "2026-07-20T16:20:00Z"},
            5,
        )
        fifteen_minute = _market_schedule(
            {
                **base_market,
                "slug": "btc-updown-15m-1784564100",
                "endDate": "2026-07-20T16:30:00Z",
            },
            15,
        )

        self.assertEqual(five_minute["market_end_timestamp"], 1784564400.0)
        self.assertEqual(fifteen_minute["market_end_timestamp"], 1784565000.0)
        self.assertEqual(five_minute["market_start_timestamp"], 1784564100.0)
        self.assertEqual(five_minute["market_end_time"], "2026-07-21 00:20:00")
        self.assertEqual(fifteen_minute["market_end_time"], "2026-07-21 00:30:00")

    def test_extract_book_uses_best_prices_from_levels(self) -> None:
        bid, ask = _extract_book(
            {
                "bids": [{"price": "0.31"}, {"price": "0.38"}],
                "asks": [{"price": "0.62"}, {"price": "0.55"}],
            }
        )
        self.assertEqual(bid, 0.38)
        self.assertEqual(ask, 0.55)

    def test_select_outcome_prefers_up_token(self) -> None:
        token_id, outcome, price = _select_outcome(
            {
                "outcomes": '["Down", "Up"]',
                "clobTokenIds": '["down-token", "up-token"]',
                "outcomePrices": '["0.58", "0.42"]',
            }
        )
        self.assertEqual((token_id, outcome, price), ("up-token", "Up", 0.42))

    def test_clob_market_candidates_normalizes_sampling_market_payload(self) -> None:
        candidates = _clob_market_candidates(
            {
                "data": [
                    {
                        "market_slug": "btc-updown-5m-1784564100",
                        "question": "Bitcoin Up or Down - 5 Minutes",
                        "active": True,
                        "closed": False,
                        "end_date_iso": "2026-07-20T16:20:00Z",
                        "game_start_time": "2026-07-20T16:15:00Z",
                        "tokens": [
                            {"token_id": "up-token", "outcome": "Up", "price": "0.42"},
                            {"token_id": "down-token", "outcome": "Down", "price": "0.58"},
                        ],
                    }
                ]
            }
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["slug"], "btc-updown-5m-1784564100")
        self.assertEqual(candidates[0]["clobTokenIds"], ["up-token", "down-token"])
        self.assertEqual(candidates[0]["outcomes"], ["Up", "Down"])
        self.assertEqual(candidates[0]["outcomePrices"], [0.42, 0.58])
        self.assertEqual(candidates[0]["endDate"], "2026-07-20T16:20:00Z")

    def test_fetch_discovers_gamma_market_and_reads_clob_prices(self) -> None:
        config = AppConfig(
            use_simulation=False,
            polymarket_gamma_api_url="https://gamma.test",
            polymarket_clob_api_url="https://clob.test",
            request_timeout_seconds=0.1,
        )
        client = PolymarketClient(config)
        client.http = FakeHttpJsonClient()
        definition = MarketDefinition(
            market_id="btc-5m",
            label="BTC 5m",
            timeframe_minutes=5,
        )

        snapshot = client.fetch(definition)

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.target_price, 64750.25)
        self.assertAlmostEqual(snapshot.contract_price, 0.44)
        self.assertEqual(snapshot.best_bid, 0.41)
        self.assertEqual(snapshot.best_ask, 0.47)
        self.assertEqual(snapshot.up_buy_price, 0.47)
        self.assertEqual(snapshot.up_sell_price, 0.41)
        self.assertEqual(snapshot.down_buy_price, 0.59)
        self.assertEqual(snapshot.down_sell_price, 0.53)
        self.assertEqual(snapshot.last_trade, 0.46)
        self.assertEqual(snapshot.metadata["token_id"], "up-token")
        self.assertEqual(snapshot.metadata["outcome"], "Up")
        self.assertEqual(snapshot.metadata["target_source"], "polymarket_price_to_beat")
        self.assertEqual(snapshot.metadata["quote_source"], "clob_book")
        self.assertEqual(snapshot.metadata["market_end_time"], "2026-07-21 00:00:00")
