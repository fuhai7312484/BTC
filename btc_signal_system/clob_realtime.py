from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    import truststore
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect as websocket_connect
except ImportError:  # REST collection remains available when optional dependencies are absent.
    truststore = None
    ConnectionClosed = None
    websocket_connect = None


@dataclass(slots=True)
class TokenQuote:
    best_bid: float | None = None
    best_ask: float | None = None
    last_trade: float | None = None
    timestamp: float | None = None
    received_at: float | None = None
    tick_size: float | None = None
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    trades: deque[tuple[float, str, float]] = field(default_factory=lambda: deque(maxlen=4096))


class PolymarketClobFeed:
    endpoint = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    def __init__(
        self,
        on_update: Callable[[set[str]], None] | None = None,
        max_event_lag_seconds: float = 2.0,
        subscription_outcomes: tuple[str, ...] = ("up", "down"),
        reconnect_delay_seconds: float = 1.0,
    ) -> None:
        self._on_update = on_update
        self._max_event_lag_seconds = max_event_lag_seconds
        self._subscription_outcomes = set(subscription_outcomes)
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._market_tokens: dict[str, tuple[str, str]] = {}
        self._token_markets: dict[str, tuple[str, str]] = {}
        self._quotes: dict[str, TokenQuote] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._tokens_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: Any = None
        self._connected = False
        self._synced = False
        self._fresh_event_streak = 0
        self._last_message_timestamp: float | None = None
        self._last_error: str | None = None
        self._dropped_stale_events = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if websocket_connect is None or truststore is None:
            self._last_error = "未安装 truststore 或 websockets，CLOB WebSocket 未启动"
            return
        truststore.inject_into_ssl()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="polymarket-clob", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._tokens_ready.set()
        socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def update_markets(self, updates: dict[str, tuple[str, str]]) -> bool:
        normalized = {
            str(market_id): (str(tokens[0]), str(tokens[1]))
            for market_id, tokens in updates.items()
            if tokens[0] and tokens[1]
        }
        if not normalized:
            return False

        with self._lock:
            changed = any(self._market_tokens.get(market_id) != tokens for market_id, tokens in normalized.items())
            if not changed:
                return False
            self._market_tokens.update(normalized)
            active_tokens = {token for tokens in self._market_tokens.values() for token in tokens}
            self._token_markets = {
                token_id: (market_id, outcome)
                for market_id, (up_token, down_token) in self._market_tokens.items()
                for token_id, outcome in ((up_token, "up"), (down_token, "down"))
            }
            self._quotes = {
                token_id: quote
                for token_id, quote in self._quotes.items()
                if token_id in active_tokens
            }
            self._synced = False
            self._fresh_event_streak = 0
            socket = self._socket

        self._tokens_ready.set()
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
        return True

    def market_quote(self, market_id: str, disconnected_max_age_seconds: float = 15.0) -> dict[str, Any] | None:
        with self._lock:
            tokens = self._market_tokens.get(market_id)
            if not tokens:
                return None
            up_token, down_token = tokens
            up_quote = self._quotes.get(up_token)
            down_quote = self._quotes.get(down_token)
            connected = self._connected
            synced = self._synced
            last_error = self._last_error
            dropped_stale_events = self._dropped_stale_events
        if not synced or (up_quote is None and down_quote is None):
            return None

        timestamps = [
            quote.timestamp
            for quote in (up_quote, down_quote)
            if quote is not None and quote.timestamp is not None
        ]
        received_timestamps = [
            quote.received_at
            for quote in (up_quote, down_quote)
            if quote is not None and quote.received_at is not None
        ]
        updated_at = max(timestamps, default=None)
        received_at = max(received_timestamps, default=None)
        if (
            dropped_stale_events > 0
            and received_at is not None
            and time.time() - received_at > 2.0
        ):
            return None
        if (
            not connected
            and updated_at is not None
            and time.time() - updated_at > disconnected_max_age_seconds
        ):
            return None

        up_bid = up_quote.best_bid if up_quote else None
        up_ask = up_quote.best_ask if up_quote else None
        down_bid = down_quote.best_bid if down_quote else None
        down_ask = down_quote.best_ask if down_quote else None
        up_metrics = _quote_metrics(up_quote)
        down_metrics = _quote_metrics(down_quote)
        return {
            "market_id": market_id,
            "up_token_id": up_token,
            "down_token_id": down_token,
            "up_buy_price": up_ask,
            "up_sell_price": up_bid,
            "down_buy_price": down_ask,
            "down_sell_price": down_bid,
            "best_bid": up_bid,
            "best_ask": up_ask,
            "contract_price": (up_bid + up_ask) / 2.0 if up_bid is not None and up_ask is not None else None,
            "last_trade": up_quote.last_trade if up_quote else None,
            "up_tick_size": up_quote.tick_size if up_quote else None,
            "down_tick_size": down_quote.tick_size if down_quote else None,
            "up_bid_depth": up_metrics["bid_depth"],
            "up_ask_depth": up_metrics["ask_depth"],
            "down_bid_depth": down_metrics["bid_depth"],
            "down_ask_depth": down_metrics["ask_depth"],
            "up_order_imbalance": up_metrics["order_imbalance"],
            "down_order_imbalance": down_metrics["order_imbalance"],
            "up_trade_imbalance": up_metrics["trade_imbalance"],
            "down_trade_imbalance": down_metrics["trade_imbalance"],
            "order_imbalance": _directional_imbalance(
                up_metrics["order_imbalance"],
                down_metrics["order_imbalance"],
            ),
            "trade_imbalance": _directional_imbalance(
                up_metrics["trade_imbalance"],
                down_metrics["trade_imbalance"],
            ),
            "updated_at": updated_at,
            "received_at": received_at,
            "connected": connected,
            "last_error": last_error,
            "complete": all(value is not None for value in (up_bid, up_ask, down_bid, down_ask)),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "synced": self._synced,
                "market_count": len(self._market_tokens),
                "token_count": len(self._token_markets),
                "subscribed_token_count": sum(
                    1 for _, outcome in self._token_markets.values() if outcome in self._subscription_outcomes
                ),
                "last_message_timestamp": self._last_message_timestamp,
                "dropped_stale_events": self._dropped_stale_events,
                "last_error": self._last_error,
            }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                has_tokens = bool(self._token_markets)
            if not has_tokens:
                self._tokens_ready.wait(1.0)
                self._tokens_ready.clear()
                continue
            try:
                self._connect_and_receive()
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._last_error = str(exc)
            self._stop_event.wait(self._reconnect_delay_seconds)

    def _connect_and_receive(self) -> None:
        with self._lock:
            token_ids = [
                token_id
                for token_id, (_, outcome) in self._token_markets.items()
                if outcome in self._subscription_outcomes
            ]
        if not token_ids:
            return

        proxy = urllib.parse.urlparse(urllib.request.getproxies().get("https", ""))
        proxy_url = f"http://{proxy.hostname}:{proxy.port}" if proxy.hostname else None
        socket = websocket_connect(
            self.endpoint,
            proxy=proxy_url,
            open_timeout=12.0,
            ping_interval=None,
            close_timeout=1.0,
            compression=None,
            max_size=4 * 1024 * 1024,
            max_queue=(128, 32),
        )
        self._socket = socket
        with self._lock:
            self._connected = True
            self._synced = False
            self._fresh_event_streak = 0
            self._last_error = None
        socket.send(
            json.dumps(
                {
                    "assets_ids": token_ids,
                    "type": "market",
                    "custom_feature_enabled": True,
                }
            )
        )

        try:
            last_ping = time.monotonic()
            while not self._stop_event.is_set():
                if time.monotonic() - last_ping >= 5.0:
                    socket.send("PING")
                    last_ping = time.monotonic()
                try:
                    message = socket.recv(timeout=1.0, decode=False)
                except TimeoutError:
                    continue
                except ConnectionClosed:
                    break
                if message and message not in ("PONG", b"PONG"):
                    self._handle_message(message)
        finally:
            with self._lock:
                self._connected = False
                self._synced = False
                self._fresh_event_streak = 0
            self._socket = None
            socket.close()

    def _handle_message(self, message: str | bytes | dict[str, Any] | list[Any]) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        if isinstance(message, str):
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                return
        else:
            event = message

        if isinstance(event, list):
            changed_markets: set[str] = set()
            for item in event:
                changed_markets.update(self._handle_event(item))
            self._emit_update(changed_markets)
            return
        changed_markets = self._handle_event(event)
        self._emit_update(changed_markets)

    def _handle_event(self, event: Any) -> set[str]:
        if not isinstance(event, dict):
            return set()
        timestamp = _timestamp_seconds(event.get("timestamp")) or time.time()
        if time.time() - timestamp > self._max_event_lag_seconds:
            with self._lock:
                self._dropped_stale_events += 1
                self._fresh_event_streak = 0
            return set()
        with self._lock:
            self._fresh_event_streak += 1
            if self._fresh_event_streak >= 2:
                self._synced = True
        event_type = str(event.get("event_type") or "")
        changed_markets: set[str] = set()

        if isinstance(event.get("price_changes"), list):
            for change in event["price_changes"]:
                if not isinstance(change, dict):
                    continue
                market_id = self._update_token(
                    str(change.get("asset_id") or ""),
                    best_bid=_float_or_none(change.get("best_bid")),
                    best_ask=_float_or_none(change.get("best_ask")),
                    depth_side=str(change.get("side") or ""),
                    depth_price=_float_or_none(change.get("price")),
                    depth_size=_float_or_none(change.get("size")),
                    timestamp=timestamp,
                )
                if market_id:
                    changed_markets.add(market_id)
        elif event_type == "last_trade_price":
            market_id = self._update_token(
                str(event.get("asset_id") or ""),
                last_trade=_float_or_none(event.get("price")),
                trade_side=str(event.get("side") or ""),
                trade_size=_float_or_none(event.get("size")),
                timestamp=timestamp,
            )
            if market_id:
                changed_markets.add(market_id)
        elif event_type == "best_bid_ask":
            market_id = self._update_token(
                str(event.get("asset_id") or ""),
                best_bid=_float_or_none(event.get("best_bid")),
                best_ask=_float_or_none(event.get("best_ask")),
                timestamp=timestamp,
            )
            if market_id:
                changed_markets.add(market_id)
        elif event_type == "tick_size_change":
            market_id = self._update_token(
                str(event.get("asset_id") or ""),
                tick_size=_float_or_none(event.get("new_tick_size") or event.get("tick_size")),
                timestamp=timestamp,
            )
            if market_id:
                changed_markets.add(market_id)
        elif event.get("asset_id") and (event.get("bids") is not None or event.get("asks") is not None):
            market_id = self._update_token(
                str(event.get("asset_id")),
                best_bid=_level_price(event.get("bids"), highest=True),
                best_ask=_level_price(event.get("asks"), highest=False),
                last_trade=_float_or_none(event.get("last_trade_price")),
                bids=_level_sizes(event.get("bids"), highest=True),
                asks=_level_sizes(event.get("asks"), highest=False),
                replace_depth=True,
                tick_size=_float_or_none(event.get("tick_size")),
                timestamp=timestamp,
            )
            if market_id:
                changed_markets.add(market_id)

        if changed_markets:
            with self._lock:
                self._last_message_timestamp = timestamp
        return changed_markets

    def _update_token(
        self,
        token_id: str,
        *,
        best_bid: float | None = None,
        best_ask: float | None = None,
        last_trade: float | None = None,
        bids: dict[float, float] | None = None,
        asks: dict[float, float] | None = None,
        replace_depth: bool = False,
        depth_side: str = "",
        depth_price: float | None = None,
        depth_size: float | None = None,
        trade_side: str = "",
        trade_size: float | None = None,
        tick_size: float | None = None,
        timestamp: float,
    ) -> str | None:
        if not token_id:
            return None
        with self._lock:
            mapping = self._token_markets.get(token_id)
            if mapping is None:
                return None
            quote = self._quotes.setdefault(token_id, TokenQuote())
            if replace_depth:
                quote.bids = dict(bids or {})
                quote.asks = dict(asks or {})
            side = depth_side.upper()
            if depth_price is not None and depth_size is not None and side in {"BUY", "SELL"}:
                levels = quote.bids if side == "BUY" else quote.asks
                if depth_size <= 0:
                    levels.pop(depth_price, None)
                else:
                    levels[depth_price] = depth_size
                _trim_depth(quote.bids, highest=True)
                _trim_depth(quote.asks, highest=False)
                if best_bid is None and quote.bids:
                    best_bid = max(quote.bids)
                if best_ask is None and quote.asks:
                    best_ask = min(quote.asks)
            if best_bid is not None:
                quote.best_bid = best_bid
            if best_ask is not None:
                quote.best_ask = best_ask
            if last_trade is not None:
                quote.last_trade = last_trade
            if tick_size is not None and 0.0 < tick_size <= 1.0:
                quote.tick_size = tick_size
            normalized_trade_side = trade_side.upper()
            if trade_size is not None and trade_size > 0 and normalized_trade_side in {"BUY", "SELL"}:
                quote.trades.append((timestamp, normalized_trade_side, trade_size))
            quote.timestamp = max(timestamp, quote.timestamp or timestamp)
            quote.received_at = time.time()
            return mapping[0]

    def _emit_update(self, market_ids: set[str]) -> None:
        if not market_ids or self._on_update is None:
            return
        try:
            self._on_update(market_ids)
        except Exception:
            return


class PolymarketClobRouter:
    """Runs one CLOB socket per market so a busy window cannot delay another one."""

    def __init__(self, on_update: Callable[[set[str]], None] | None = None) -> None:
        self._on_update = on_update
        self._feeds: dict[tuple[str, str], PolymarketClobFeed] = {}
        self._lock = threading.RLock()
        self._started = False

    def start(self) -> None:
        with self._lock:
            self._started = True
            feeds = list(self._feeds.values())
        for feed in feeds:
            feed.start()

    def stop(self) -> None:
        with self._lock:
            self._started = False
            feeds = list(self._feeds.values())
        for feed in feeds:
            feed.stop()

    def update_markets(self, updates: dict[str, tuple[str, str]]) -> bool:
        changed = False
        for market_id, tokens in updates.items():
            for outcome in ("up", "down"):
                key = (market_id, outcome)
                with self._lock:
                    feed = self._feeds.get(key)
                    if feed is None:
                        feed = PolymarketClobFeed(
                            on_update=self._on_update,
                            subscription_outcomes=(outcome,),
                            reconnect_delay_seconds=0.75 + len(self._feeds) * 0.35,
                        )
                        self._feeds[key] = feed
                        should_start = self._started
                    else:
                        should_start = False
                if should_start:
                    feed.start()
                changed = feed.update_markets({market_id: tokens}) or changed
        return changed

    def market_quote(self, market_id: str, disconnected_max_age_seconds: float = 15.0) -> dict[str, Any] | None:
        with self._lock:
            feeds = [self._feeds.get((market_id, outcome)) for outcome in ("up", "down")]
        parts = [
            quote
            for feed in feeds
            if feed is not None
            for quote in [feed.market_quote(market_id, disconnected_max_age_seconds)]
            if quote is not None
        ]
        if len(parts) != 2:
            return None

        def first_value(key: str) -> Any:
            return next((part[key] for part in parts if part.get(key) is not None), None)

        up_bid = first_value("up_sell_price")
        up_ask = first_value("up_buy_price")
        down_bid = first_value("down_sell_price")
        down_ask = first_value("down_buy_price")
        up_order_imbalance = first_value("up_order_imbalance")
        down_order_imbalance = first_value("down_order_imbalance")
        up_trade_imbalance = first_value("up_trade_imbalance")
        down_trade_imbalance = first_value("down_trade_imbalance")
        return {
            "market_id": market_id,
            "up_token_id": first_value("up_token_id"),
            "down_token_id": first_value("down_token_id"),
            "up_buy_price": up_ask,
            "up_sell_price": up_bid,
            "down_buy_price": down_ask,
            "down_sell_price": down_bid,
            "best_bid": up_bid,
            "best_ask": up_ask,
            "contract_price": (up_bid + up_ask) / 2.0 if up_bid is not None and up_ask is not None else None,
            "last_trade": first_value("last_trade"),
            "up_tick_size": first_value("up_tick_size"),
            "down_tick_size": first_value("down_tick_size"),
            "up_bid_depth": first_value("up_bid_depth"),
            "up_ask_depth": first_value("up_ask_depth"),
            "down_bid_depth": first_value("down_bid_depth"),
            "down_ask_depth": first_value("down_ask_depth"),
            "up_order_imbalance": up_order_imbalance,
            "down_order_imbalance": down_order_imbalance,
            "up_trade_imbalance": up_trade_imbalance,
            "down_trade_imbalance": down_trade_imbalance,
            "order_imbalance": _directional_imbalance(up_order_imbalance, down_order_imbalance),
            "trade_imbalance": _directional_imbalance(up_trade_imbalance, down_trade_imbalance),
            "updated_at": max(
                (part["updated_at"] for part in parts if part.get("updated_at") is not None),
                default=None,
            ),
            "received_at": max(
                (part["received_at"] for part in parts if part.get("received_at") is not None),
                default=None,
            ),
            "connected": all(part.get("connected") for part in parts) and len(parts) == 2,
            "last_error": next((part.get("last_error") for part in parts if part.get("last_error")), None),
            "complete": all(value is not None for value in (up_bid, up_ask, down_bid, down_ask)),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            feed_items = list(self._feeds.items())
        statuses = [(key, feed.status()) for key, feed in feed_items]
        market_ids = {key[0] for key, _ in statuses}

        def market_ready(market_id: str, field: str) -> bool:
            market_statuses = [status for (key_market, _), status in statuses if key_market == market_id]
            return len(market_statuses) == 2 and all(status[field] for status in market_statuses)

        return {
            "connected": bool(statuses) and all(item["connected"] for _, item in statuses),
            "synced": bool(statuses) and all(item["synced"] for _, item in statuses),
            "connected_market_count": sum(1 for market_id in market_ids if market_ready(market_id, "connected")),
            "synced_market_count": sum(1 for market_id in market_ids if market_ready(market_id, "synced")),
            "market_count": len(market_ids),
            "connection_count": len(statuses),
            "connections": [
                {
                    "market_id": key[0],
                    "outcome": key[1],
                    **status,
                }
                for key, status in statuses
            ],
            "token_count": sum(item["subscribed_token_count"] for _, item in statuses),
            "last_message_timestamp": max(
                (item["last_message_timestamp"] for _, item in statuses if item["last_message_timestamp"] is not None),
                default=None,
            ),
            "dropped_stale_events": sum(item["dropped_stale_events"] for _, item in statuses),
            "last_error": next((item["last_error"] for _, item in statuses if item["last_error"]), None),
        }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _timestamp_seconds(value: Any) -> float | None:
    timestamp = _float_or_none(value)
    if timestamp is None:
        return None
    return timestamp / 1000.0 if timestamp > 10_000_000_000 else timestamp


def _level_price(levels: Any, highest: bool) -> float | None:
    if not isinstance(levels, list):
        return None
    prices = [
        price
        for level in levels
        if isinstance(level, dict)
        for price in [_float_or_none(level.get("price"))]
        if price is not None
    ]
    if not prices:
        return None
    return max(prices) if highest else min(prices)


def _level_sizes(levels: Any, *, highest: bool, limit: int = 20) -> dict[float, float]:
    if not isinstance(levels, list):
        return {}
    parsed: dict[float, float] = {}
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = _float_or_none(level.get("price"))
        size = _float_or_none(level.get("size"))
        if price is not None and size is not None and size > 0:
            parsed[price] = size
    if len(parsed) <= limit:
        return parsed
    prices = sorted(parsed, reverse=highest)
    return {price: parsed[price] for price in prices[:limit]}


def _top_depth(levels: dict[float, float], *, highest: bool, limit: int = 5) -> float | None:
    if not levels:
        return None
    prices = sorted(levels, reverse=highest)[:limit]
    return sum(levels[price] for price in prices)


def _trim_depth(levels: dict[float, float], *, highest: bool, limit: int = 20) -> None:
    if len(levels) <= limit:
        return
    keep = set(sorted(levels, reverse=highest)[:limit])
    for price in tuple(levels):
        if price not in keep:
            del levels[price]


def _quote_metrics(quote: TokenQuote | None, trade_window_seconds: float = 60.0) -> dict[str, float | None]:
    if quote is None:
        return {
            "bid_depth": None,
            "ask_depth": None,
            "order_imbalance": None,
            "trade_imbalance": None,
        }
    bid_depth = _top_depth(quote.bids, highest=True)
    ask_depth = _top_depth(quote.asks, highest=False)
    depth_total = (bid_depth or 0.0) + (ask_depth or 0.0)
    order_imbalance = (
        ((bid_depth or 0.0) - (ask_depth or 0.0)) / depth_total
        if depth_total > 0
        else None
    )

    cutoff = time.time() - trade_window_seconds
    buy_volume = sum(size for timestamp, side, size in quote.trades if timestamp >= cutoff and side == "BUY")
    sell_volume = sum(size for timestamp, side, size in quote.trades if timestamp >= cutoff and side == "SELL")
    trade_total = buy_volume + sell_volume
    trade_imbalance = (buy_volume - sell_volume) / trade_total if trade_total > 0 else None
    return {
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "order_imbalance": order_imbalance,
        "trade_imbalance": trade_imbalance,
    }


def _directional_imbalance(up_value: float | None, down_value: float | None) -> float | None:
    values = [value for value in (up_value, -down_value if down_value is not None else None) if value is not None]
    return sum(values) / len(values) if values else None
