from __future__ import annotations

import json
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import AppConfig
from .models import MarketDefinition, format_unix_timestamp
from .parsing import infer_market_bias, parse_target_price
from .utils import coerce_float


class HttpJsonClient:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self._curl_path = shutil.which("curl")
        self._proxies = urllib.request.getproxies()
        self._prefer_curl = bool(self._curl_path and self._proxies.get("https"))

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        request_headers = {"User-Agent": "btc-signal-system/0.1", **(headers or {})}
        if self._prefer_curl:
            payload = self._get_text_with_curl(url, request_headers)
        else:
            payload = self._get_text_with_urllib(url, request_headers)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"解析接口 JSON 失败 {url}：{exc}") from exc

    def _get_text_with_urllib(self, url: str, headers: dict[str, str]) -> str:
        request = urllib.request.Request(
            url,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError) and self._curl_path:
                self._prefer_curl = True
                return self._get_text_with_curl(url, headers)
            raise RuntimeError(f"请求接口失败 {url}：{exc}") from exc
        except ssl.SSLCertVerificationError as exc:
            if self._curl_path:
                self._prefer_curl = True
                return self._get_text_with_curl(url, headers)
            raise RuntimeError(f"请求接口失败 {url}：{exc}") from exc
        except (TimeoutError, ValueError) as exc:
            raise RuntimeError(f"请求接口失败 {url}：{exc}") from exc

    def _get_text_with_curl(self, url: str, headers: dict[str, str]) -> str:
        if not self._curl_path:
            raise RuntimeError(f"请求接口失败 {url}：系统未安装 curl")
        command = [
            self._curl_path,
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--max-time",
            str(self.timeout_seconds),
        ]
        proxy = self._proxies.get(urllib.parse.urlparse(url).scheme)
        if proxy:
            command.extend(("--proxy", proxy))
        for key, value in headers.items():
            command.extend(("--header", f"{key}: {value}"))
        command.append(url)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds + 2.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"请求接口失败 {url}：{exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"curl 退出码 {completed.returncode}"
            raise RuntimeError(f"请求接口失败 {url}：{detail}")
        return completed.stdout


def _walk_json(value: Any):
    yield value
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _first_float(value: Any, keys: tuple[str, ...]) -> float | None:
    for node in _walk_json(value):
        if isinstance(node, dict):
            for key in keys:
                candidate = coerce_float(node.get(key))
                if candidate is not None:
                    return candidate
    return None


def _extract_book(value: Any) -> tuple[float | None, float | None]:
    best_bid: float | None = None
    best_ask: float | None = None
    for node in _walk_json(value):
        if not isinstance(node, dict):
            continue
        if best_bid is None:
            best_bid = _first_direct_float(node, ("bestBid", "bid", "bidPrice", "best_bid"))
        if best_ask is None:
            best_ask = _first_direct_float(node, ("bestAsk", "ask", "askPrice", "best_ask"))
        if best_bid is None:
            best_bid = _level_price(node.get("bids"), highest=True)
        if best_ask is None:
            best_ask = _level_price(node.get("asks"), highest=False)
        if best_bid is not None and best_ask is not None:
            break
    return best_bid, best_ask


def _first_direct_float(value: Any, keys: tuple[str, ...]) -> float | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        candidate = coerce_float(value.get(key))
        if candidate is not None:
            return candidate
    return None


def _level_price(levels: Any, highest: bool) -> float | None:
    if not isinstance(levels, list):
        return None
    prices: list[float] = []
    for level in levels:
        if isinstance(level, (list, tuple)) and level:
            price = coerce_float(level[0])
        else:
            price = _first_direct_float(level, ("price", "p", "rate"))
        if price is not None:
            prices.append(price)
    if not prices:
        return None
    return max(prices) if highest else min(prices)


def _extract_market_price(value: Any) -> float | None:
    return _first_float(
        value,
        (
            "mid",
            "midPrice",
            "price",
            "last",
            "lastPrice",
            "last_trade_price",
            "lastTradePrice",
            "markPrice",
            "value",
        ),
    )


def _format_template(template: str | None, definition: MarketDefinition, config: AppConfig) -> str | None:
    if not template:
        return None
    return template.format(
        market_id=definition.market_id,
        label=definition.label,
        timeframe_minutes=definition.timeframe_minutes,
        symbol=config.symbol,
    )


@dataclass(slots=True)
class BinanceSnapshot:
    spot_price: float | None = None
    spot_bid: float | None = None
    spot_ask: float | None = None
    perp_price: float | None = None
    perp_bid: float | None = None
    perp_ask: float | None = None
    spot_volume_24h: float | None = None
    perp_volume_24h: float | None = None
    source: str = "live"


class BinanceClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.http = HttpJsonClient(config.request_timeout_seconds)

    def fetch(self) -> BinanceSnapshot | None:
        symbol = self.config.symbol.upper()
        spot_book_url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={urllib.parse.quote(symbol)}"
        spot_24h_url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={urllib.parse.quote(symbol)}"
        perp_book_url = f"https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol={urllib.parse.quote(symbol)}"
        perp_24h_url = f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={urllib.parse.quote(symbol)}"

        def fetch_or_none(url: str) -> Any:
            try:
                return self.http.get_json(url)
            except RuntimeError:
                return None

        with ThreadPoolExecutor(max_workers=4) as executor:
            spot_book, spot_24h, perp_book, perp_24h = executor.map(
                fetch_or_none,
                (spot_book_url, spot_24h_url, perp_book_url, perp_24h_url),
            )

        if all(value is None for value in (spot_book, spot_24h, perp_book, perp_24h)):
            return None

        spot_bid = coerce_float(spot_book.get("bidPrice")) if isinstance(spot_book, dict) else None
        spot_ask = coerce_float(spot_book.get("askPrice")) if isinstance(spot_book, dict) else None
        perp_bid = coerce_float(perp_book.get("bidPrice")) if isinstance(perp_book, dict) else None
        perp_ask = coerce_float(perp_book.get("askPrice")) if isinstance(perp_book, dict) else None

        spot_price = None
        if spot_bid is not None and spot_ask is not None:
            spot_price = (spot_bid + spot_ask) / 2.0
        if spot_price is None and isinstance(spot_24h, dict):
            spot_price = coerce_float(spot_24h.get("lastPrice")) or coerce_float(spot_24h.get("weightedAvgPrice"))

        perp_price = None
        if perp_bid is not None and perp_ask is not None:
            perp_price = (perp_bid + perp_ask) / 2.0
        if perp_price is None and isinstance(perp_24h, dict):
            perp_price = coerce_float(perp_24h.get("lastPrice")) or coerce_float(perp_24h.get("markPrice"))

        spot_volume = coerce_float(spot_24h.get("quoteVolume")) if isinstance(spot_24h, dict) else None
        perp_volume = coerce_float(perp_24h.get("quoteVolume")) if isinstance(perp_24h, dict) else None

        return BinanceSnapshot(
            spot_price=spot_price,
            spot_bid=spot_bid,
            spot_ask=spot_ask,
            perp_price=perp_price,
            perp_bid=perp_bid,
            perp_ask=perp_ask,
            spot_volume_24h=spot_volume,
            perp_volume_24h=perp_volume,
        )


@dataclass(slots=True)
class PolymarketSnapshot:
    contract_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    up_buy_price: float | None = None
    up_sell_price: float | None = None
    down_buy_price: float | None = None
    down_sell_price: float | None = None
    last_trade: float | None = None
    target_price: float | None = None
    market_bias: str = "unknown"
    label: str | None = None
    description: str | None = None
    source: str = "live"
    metadata: dict[str, Any] | None = None


class PolymarketClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.http = HttpJsonClient(config.request_timeout_seconds)
        self._discovery_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._server_time_offset_cache: tuple[float, float] | None = None
        self._gamma_unavailable_until = 0.0
        self._clob_discovery_unavailable_until = 0.0

    def fetch(self, definition: MarketDefinition) -> PolymarketSnapshot | None:
        metadata = definition.metadata if isinstance(definition.metadata, dict) else {}
        explicit_market_template = metadata.get("polymarket_market_url") or self.config.polymarket_market_url_template
        raw_templates = [
            metadata.get("polymarket_market_url"),
            self.config.polymarket_market_url_template,
            metadata.get("polymarket_book_url"),
            self.config.polymarket_book_url_template,
            metadata.get("polymarket_trades_url"),
            self.config.polymarket_trades_url_template,
        ]
        urls = [_format_template(template, definition, self.config) for template in raw_templates if template]
        urls = [url for url in urls if url]

        payloads: list[Any] = []
        for url in urls:
            try:
                payloads.append(self.http.get_json(url))
            except RuntimeError:
                continue

        market_payload: dict[str, Any] | None = None
        if self.config.polymarket_discovery_enabled and not explicit_market_template:
            market_payload = self._discover_market(definition)
        if market_payload is not None:
            payloads.insert(0, market_payload)
        if not payloads:
            return None

        contract_price = definition.contract_price
        best_bid = definition.best_bid
        best_ask = definition.best_ask
        up_buy_price = definition.best_ask
        up_sell_price = definition.best_bid
        down_buy_price = None
        down_sell_price = None
        last_trade = None
        label = definition.label
        description = definition.description
        target_price = definition.target_price
        target_source = "definition" if definition.target_price is not None else None
        market_bias = definition.market_bias
        token_id = None
        down_token_id = None
        selected_outcome = None
        market_slug = None
        quote_source = None
        schedule_metadata: dict[str, Any] = {}

        if market_payload is not None:
            label = str(market_payload.get("question") or market_payload.get("title") or label)
            description = str(market_payload.get("description") or market_payload.get("rules") or description)
            price_to_beat = _market_target_price(market_payload)
            if target_price is None and price_to_beat is not None:
                target_price = price_to_beat
                target_source = "polymarket_price_to_beat"
            if target_price is None:
                target_price = parse_target_price(f"{label} {description}")
                if target_price is not None:
                    target_source = "polymarket_rules"
            market_bias = market_bias if market_bias != "unknown" else infer_market_bias(f"{label} {description}")
            outcomes = _select_outcomes(market_payload)
            up_outcome = outcomes.get("up", {})
            down_outcome = outcomes.get("down", {})
            token_id = up_outcome.get("token_id")
            down_token_id = down_outcome.get("token_id")
            selected_outcome = up_outcome.get("outcome")
            market_price = up_outcome.get("price")
            contract_price = contract_price or market_price
            market_slug = str(market_payload.get("slug") or "") or None
            schedule_metadata = _market_schedule(market_payload, definition.timeframe_minutes)
            with ThreadPoolExecutor(max_workers=4) as executor:
                quotes_future = executor.submit(self._fetch_clob_quotes, token_id, down_token_id)
                books_future = executor.submit(self._fetch_clob_books, token_id, down_token_id, definition)
                clock_future = executor.submit(self._polymarket_clock_offset)
                trade_future = executor.submit(
                    self._fetch_clob_payload,
                    "last-trade-price",
                    token_id,
                    definition,
                )
                quotes = quotes_future.result()
                books = books_future.result()
                clock_offset = clock_future.result()
                trade_payload = trade_future.result()
            up_bid, up_ask = books.get("up", (None, None))
            down_bid, down_ask = books.get("down", (None, None))
            up_buy_price = up_ask if up_ask is not None else quotes.get("up_buy")
            up_sell_price = up_bid if up_bid is not None else quotes.get("up_sell")
            down_buy_price = down_ask if down_ask is not None else quotes.get("down_buy")
            down_sell_price = down_bid if down_bid is not None else quotes.get("down_sell")
            book_count = sum(value is not None for value in (up_bid, up_ask, down_bid, down_ask))
            quote_count = sum(value is not None for value in quotes.values())

            if book_count == 4:
                quote_source = "clob_book"
            elif book_count > 0:
                quote_source = "clob_book_with_price_fallback"
            elif quote_count == 4:
                quote_source = "clob_price"
            elif quote_count > 0:
                quote_source = "clob_price_partial"

            if up_buy_price is not None:
                best_ask = up_buy_price
            if up_sell_price is not None:
                best_bid = up_sell_price
            if up_buy_price is not None and up_sell_price is not None:
                contract_price = (up_buy_price + up_sell_price) / 2.0

            if book_count > 0 or quote_count > 0:
                if clock_offset is not None:
                    schedule_metadata["polymarket_clock_offset_seconds"] = round(clock_offset, 4)
                if trade_payload is not None:
                    last_trade = _first_float(trade_payload, ("price", "last_trade_price", "lastTradePrice"))

        for payload in payloads:
            if contract_price is None:
                contract_price = _extract_market_price(payload)
            if best_bid is None or best_ask is None:
                book_bid, book_ask = _extract_book(payload)
                best_bid = best_bid if best_bid is not None else book_bid
                best_ask = best_ask if best_ask is not None else book_ask
            if last_trade is None:
                last_trade = _first_float(payload, ("lastTradePrice", "last_trade_price", "last", "price"))
            if isinstance(payload, dict) and label == definition.label:
                label = str(payload.get("question") or payload.get("title") or payload.get("name") or definition.label)
            if isinstance(payload, dict) and description == definition.description:
                description = str(payload.get("description") or payload.get("rules") or definition.description)
            if target_price is None:
                candidate = _market_target_price(payload) if isinstance(payload, dict) else None
                if candidate is not None:
                    target_price = candidate
                    target_source = "polymarket_price_to_beat"
                else:
                    candidate = parse_target_price(f"{label or ''} {description or ''}")
                    if candidate is not None:
                        target_price = candidate
                        target_source = "polymarket_rules"
            if market_bias == "unknown":
                market_bias = infer_market_bias(f"{label or ''} {description or ''}")

        if contract_price is None and best_bid is not None and best_ask is not None:
            contract_price = (best_bid + best_ask) / 2.0
        up_buy_price = up_buy_price if up_buy_price is not None else best_ask
        up_sell_price = up_sell_price if up_sell_price is not None else best_bid
        if quote_source is None and (up_buy_price is not None or up_sell_price is not None):
            quote_source = "gamma_fallback"

        return PolymarketSnapshot(
            contract_price=contract_price,
            best_bid=best_bid,
            best_ask=best_ask,
            last_trade=last_trade,
            target_price=target_price,
            market_bias=market_bias,
            label=label,
            description=description,
            metadata={
                "payload_count": len(payloads),
                "token_id": token_id,
                "up_token_id": token_id,
                "down_token_id": down_token_id,
                "outcome": selected_outcome,
                "slug": market_slug,
                "target_source": target_source,
                "quote_source": quote_source,
                "source": "live",
                **schedule_metadata,
            },
            up_buy_price=up_buy_price,
            up_sell_price=up_sell_price,
            down_buy_price=down_buy_price,
            down_sell_price=down_sell_price,
        )

    def _fetch_clob_quotes(self, up_token_id: str | None, down_token_id: str | None) -> dict[str, float | None]:
        requests = {
            "up_buy": (up_token_id, "BUY"),
            "up_sell": (up_token_id, "SELL"),
            "down_buy": (down_token_id, "BUY"),
            "down_sell": (down_token_id, "SELL"),
        }
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                key: executor.submit(self._fetch_clob_price, token_id, side)
                for key, (token_id, side) in requests.items()
            }
            return {key: future.result() for key, future in futures.items()}

    def _fetch_clob_price(self, token_id: str | None, side: str) -> float | None:
        if not token_id:
            return None
        params = urllib.parse.urlencode({"token_id": token_id, "side": side})
        try:
            payload = self.http.get_json(f"{self.config.polymarket_clob_api_url}/price?{params}")
        except RuntimeError:
            return None
        return _first_float(payload, ("price",))

    def _fetch_clob_books(
        self,
        up_token_id: str | None,
        down_token_id: str | None,
        definition: MarketDefinition,
    ) -> dict[str, tuple[float | None, float | None]]:
        with ThreadPoolExecutor(max_workers=2) as executor:
            up_future = executor.submit(self._fetch_clob_payload, "book", up_token_id, definition)
            down_future = executor.submit(self._fetch_clob_payload, "book", down_token_id, definition)
            up_payload = up_future.result()
            down_payload = down_future.result()
        return {
            "up": _extract_book(up_payload) if up_payload is not None else (None, None),
            "down": _extract_book(down_payload) if down_payload is not None else (None, None),
        }

    def _fetch_clob_payload(self, endpoint: str, token_id: str | None, definition: MarketDefinition) -> Any:
        if not token_id:
            return None
        metadata = definition.metadata if isinstance(definition.metadata, dict) else {}
        template_key = {
            "book": "polymarket_book_url",
            "last-trade-price": "polymarket_trades_url",
        }.get(endpoint)
        template = metadata.get(template_key) if template_key else None
        if not template:
            template = {
                "book": self.config.polymarket_book_url_template,
                "last-trade-price": self.config.polymarket_trades_url_template,
            }.get(endpoint)
        if template:
            url = _format_template(str(template), definition, self.config)
            if not url:
                return None
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}token_id={urllib.parse.quote(token_id)}"
        else:
            url = f"{self.config.polymarket_clob_api_url}/{endpoint}?token_id={urllib.parse.quote(token_id)}"
        try:
            return self.http.get_json(url)
        except RuntimeError:
            return None

    def _polymarket_clock_offset(self) -> float | None:
        now = time.time()
        if self._server_time_offset_cache and self._server_time_offset_cache[0] > now:
            return self._server_time_offset_cache[1]

        request_started = time.time()
        try:
            payload = self.http.get_json(f"{self.config.polymarket_clob_api_url}/time")
        except RuntimeError:
            return None
        request_finished = time.time()
        if isinstance(payload, dict):
            server_timestamp = _first_float(payload, ("timestamp", "server_time", "time"))
        else:
            server_timestamp = coerce_float(payload)
        if server_timestamp is None:
            return None
        if server_timestamp > 10_000_000_000:
            server_timestamp /= 1000.0

        local_midpoint = (request_started + request_finished) / 2.0
        offset = server_timestamp - local_midpoint
        self._server_time_offset_cache = (request_finished + 30.0, offset)
        return offset

    def _discover_market(self, definition: MarketDefinition) -> dict[str, Any] | None:
        now = time.time()
        cached = self._discovery_cache.get(definition.market_id)
        if cached and cached[0] > now:
            return cached[1]

        candidates: list[dict[str, Any]] = []
        metadata = definition.metadata if isinstance(definition.metadata, dict) else {}
        explicit_slug = metadata.get("polymarket_slug") or metadata.get("event_slug") or metadata.get("slug")
        if explicit_slug:
            explicit_candidates = [
                candidate
                for candidate in self._fetch_slug_candidates(str(explicit_slug))
                if _matches_market(candidate, definition)
            ]
            if explicit_candidates:
                result = min(explicit_candidates, key=lambda item: _market_distance(item, now))
                self._discovery_cache[definition.market_id] = (now + 45.0, result)
                return result

        timeframe_seconds = definition.timeframe_minutes * 60
        window_start = int(now // timeframe_seconds) * timeframe_seconds
        prefixes = (
            f"btc-updown-{definition.timeframe_minutes}m-",
            f"btc-up-or-down-{definition.timeframe_minutes}m-",
        )
        for offset in (0,):
            for prefix in prefixes:
                discovered = self._fetch_slug_candidates(f"{prefix}{window_start + offset}")
                candidates.extend(discovered)
                current_matches = [item for item in discovered if _matches_market(item, definition)]
                if offset == 0 and current_matches:
                    result = min(current_matches, key=lambda item: _market_distance(item, now))
                    next_boundary = (int(now // timeframe_seconds) + 1) * timeframe_seconds
                    cache_seconds = min(45.0, max(2.0, next_boundary - now + 1.0))
                    self._discovery_cache[definition.market_id] = (now + cache_seconds, result)
                    return result

        clob_candidates = self._fetch_clob_market_candidates()
        current_clob_matches = [item for item in clob_candidates if _matches_market(item, definition)]
        if current_clob_matches:
            result = min(current_clob_matches, key=lambda item: _market_distance(item, now))
            next_boundary = (int(now // timeframe_seconds) + 1) * timeframe_seconds
            cache_seconds = min(45.0, max(2.0, next_boundary - now + 1.0))
            self._discovery_cache[definition.market_id] = (now + cache_seconds, result)
            return result

        for offset in (-timeframe_seconds, timeframe_seconds):
            for prefix in prefixes:
                candidates.extend(self._fetch_slug_candidates(f"{prefix}{window_start + offset}"))

        if not candidates:
            candidates.extend(self._fetch_market_list())

        matched = [candidate for candidate in candidates if _matches_market(candidate, definition)]
        result = min(matched, key=lambda item: _market_distance(item, now), default=None)
        next_boundary = (int(now // timeframe_seconds) + 1) * timeframe_seconds
        cache_seconds = min(45.0, max(2.0, next_boundary - now + 1.0)) if result else 10.0
        self._discovery_cache[definition.market_id] = (now + cache_seconds, result)
        return result

    def _fetch_slug_candidates(self, slug: str) -> list[dict[str, Any]]:
        if self._gamma_unavailable_until > time.time():
            return []
        candidates: list[dict[str, Any]] = []
        quoted_slug = urllib.parse.quote(slug)
        for path in (f"/events/slug/{quoted_slug}", f"/markets/slug/{quoted_slug}"):
            try:
                payload = self.http.get_json(f"{self.config.polymarket_gamma_api_url}{path}")
            except RuntimeError:
                self._gamma_unavailable_until = time.time() + 30.0
                break
            candidates.extend(_market_candidates(payload))
            if candidates:
                break
        return candidates

    def _fetch_clob_market_candidates(self) -> list[dict[str, Any]]:
        if self._clob_discovery_unavailable_until > time.time():
            return []
        for endpoint in ("sampling-markets", "markets"):
            try:
                payload = self.http.get_json(f"{self.config.polymarket_clob_api_url}/{endpoint}")
            except RuntimeError:
                continue
            candidates = _clob_market_candidates(payload)
            if candidates:
                return candidates
        self._clob_discovery_unavailable_until = time.time() + 15.0
        return []

    def _fetch_market_list(self) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode(
            {"closed": "false", "limit": "500", "order": "endDate", "ascending": "false"}
        )
        try:
            payload = self.http.get_json(f"{self.config.polymarket_gamma_api_url}/markets?{params}")
        except RuntimeError:
            return []
        return _market_candidates(payload)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _clob_market_candidates(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        raw_markets = value.get("data") or value.get("markets") or []
    elif isinstance(value, list):
        raw_markets = value
    else:
        return []
    if not isinstance(raw_markets, list):
        return []

    candidates: list[dict[str, Any]] = []
    for raw in raw_markets:
        if not isinstance(raw, dict) or raw.get("closed") is True or raw.get("active") is False:
            continue
        tokens = _as_list(raw.get("tokens"))
        outcomes: list[str] = []
        token_ids: list[str] = []
        prices: list[float | None] = []
        for token in tokens:
            if not isinstance(token, dict):
                continue
            token_id = token.get("token_id") or token.get("tokenId") or token.get("asset_id")
            outcome = token.get("outcome") or token.get("name")
            if token_id is None or outcome is None:
                continue
            token_ids.append(str(token_id))
            outcomes.append(str(outcome))
            prices.append(coerce_float(token.get("price")))
        if not token_ids:
            continue

        candidate = dict(raw)
        candidate.update(
            {
                "slug": raw.get("market_slug") or raw.get("slug") or "",
                "question": raw.get("question") or raw.get("title") or "",
                "description": raw.get("description") or "",
                "outcomes": outcomes,
                "clobTokenIds": token_ids,
                "outcomePrices": prices,
                "endDate": raw.get("end_date_iso") or raw.get("endDate") or raw.get("end_date"),
                "startTime": raw.get("game_start_time") or raw.get("startTime") or raw.get("start_date_iso"),
            }
        )
        candidates.append(candidate)
    return candidates


def _market_candidates(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    candidates: list[dict[str, Any]] = []
    if value.get("question") or value.get("clobTokenIds"):
        candidates.append(value)
    nested = value.get("markets")
    if isinstance(nested, list):
        inherited_keys = (
            "priceToBeat",
            "price_to_beat",
            "startPrice",
            "startingPrice",
            "finalPrice",
            "startDate",
            "start_date",
            "startTime",
            "eventStartTime",
            "endDate",
            "end_date",
            "endTime",
        )
        for item in nested:
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            for key in inherited_keys:
                if key not in enriched and key in value:
                    enriched[key] = value[key]
            candidates.append(enriched)
    return candidates


def _market_target_price(market: dict[str, Any]) -> float | None:
    for key in (
        "priceToBeat",
        "price_to_beat",
        "startPrice",
        "startingPrice",
        "referencePrice",
        "reference_price",
    ):
        value = coerce_float(market.get(key))
        if value is not None and value > 0:
            return value
    return None


def _select_outcome(market: dict[str, Any]) -> tuple[str | None, str | None, float | None]:
    selected = _select_outcomes(market).get("up", {})
    return selected.get("token_id"), selected.get("outcome"), selected.get("price")


def _select_outcomes(market: dict[str, Any]) -> dict[str, dict[str, Any]]:
    outcomes = _as_list(market.get("outcomes"))
    token_ids = _as_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
    prices = _as_list(market.get("outcomePrices"))
    selected: dict[str, dict[str, Any]] = {}
    for index, outcome in enumerate(outcomes):
        lowered = str(outcome).strip().lower()
        if lowered in {"up", "yes", "above", "over", "higher"} or lowered.startswith("up"):
            side = "up"
        elif lowered in {"down", "no", "below", "under", "lower"} or lowered.startswith("down"):
            side = "down"
        else:
            side = "up" if index == 0 else "down"
        selected[side] = {
            "token_id": str(token_ids[index]) if index < len(token_ids) and token_ids[index] else None,
            "outcome": str(outcome),
            "price": coerce_float(prices[index]) if index < len(prices) else None,
        }
    return selected


def _parse_market_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        return timestamp / 1000.0 if timestamp > 10_000_000_000 else timestamp
    if isinstance(value, str):
        numeric = coerce_float(value)
        if numeric is not None:
            return numeric / 1000.0 if numeric > 10_000_000_000 else numeric
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _market_schedule(market: dict[str, Any], timeframe_minutes: int) -> dict[str, Any]:
    start_timestamp = None
    end_timestamp = None
    for key in ("eventStartTime", "startTime", "startDate", "start_date"):
        start_timestamp = _parse_market_timestamp(market.get(key))
        if start_timestamp is not None:
            break
    for key in ("endDate", "end_date", "endTime"):
        end_timestamp = _parse_market_timestamp(market.get(key))
        if end_timestamp is not None:
            break

    slug = str(market.get("slug") or "")
    if start_timestamp is None and slug:
        suffix = slug.rsplit("-", 1)[-1]
        start_timestamp = _parse_market_timestamp(suffix)
    if end_timestamp is None and start_timestamp is not None:
        end_timestamp = start_timestamp + timeframe_minutes * 60
    if start_timestamp is None and end_timestamp is not None:
        start_timestamp = end_timestamp - timeframe_minutes * 60

    return {
        "market_start_time": format_unix_timestamp(start_timestamp),
        "market_start_timestamp": start_timestamp,
        "market_end_time": format_unix_timestamp(end_timestamp),
        "market_end_timestamp": end_timestamp,
    }


def _matches_market(market: dict[str, Any], definition: MarketDefinition) -> bool:
    slug = str(market.get("slug") or market.get("market_slug") or "").lower()
    expected_slug = re.compile(
        rf"^btc-up(?:down|-or-down)-{definition.timeframe_minutes}m-\d+$"
    )
    if expected_slug.fullmatch(slug):
        return True

    text = " ".join(
        str(market.get(key) or "") for key in ("slug", "question", "title", "description", "rules")
    ).lower()
    if "btc" not in text and "bitcoin" not in text:
        return False
    if not any(token in text for token in ("updown", "up or down", "up/down")):
        return False
    explicit_timeframe = re.search(r"(?<!\d)(\d{1,3})\s*(?:m|min|minute|minutes)\b", text)
    if explicit_timeframe is None:
        return False
    return int(explicit_timeframe.group(1)) == definition.timeframe_minutes


def _market_distance(market: dict[str, Any], now: float) -> float:
    from datetime import datetime

    for key in ("endDate", "end_date", "startDate", "start_date"):
        raw = market.get(key)
        if isinstance(raw, (int, float)):
            return abs(float(raw) - now)
        if isinstance(raw, str):
            try:
                return abs(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() - now)
            except ValueError:
                continue
    return float("inf")
