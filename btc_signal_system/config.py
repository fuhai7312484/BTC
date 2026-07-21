from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import MarketDefinition
from .parsing import parse_market_definition
from .utils import env_bool, env_float, env_int, env_str, split_csv


@dataclass(slots=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    poll_interval_seconds: float = 1.0
    request_timeout_seconds: float = 8.0
    history_size: int = 1800
    use_simulation: bool = False
    symbol: str = "BTCUSDT"
    markets_file: str | None = None
    polymarket_market_url_template: str | None = None
    polymarket_book_url_template: str | None = None
    polymarket_trades_url_template: str | None = None
    polymarket_gamma_api_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_api_url: str = "https://clob.polymarket.com"
    polymarket_discovery_enabled: bool = True
    extra_market_urls: list[str] = field(default_factory=list)


def value_or_none(value: str) -> str | None:
    cleaned = value.strip()
    return cleaned or None


def load_config() -> AppConfig:
    return AppConfig(
        host=env_str("BTC_SIGNAL_HOST", "127.0.0.1"),
        port=env_int("BTC_SIGNAL_PORT", 8000),
        poll_interval_seconds=env_float("BTC_SIGNAL_POLL_INTERVAL", 1.0),
        request_timeout_seconds=env_float("BTC_SIGNAL_HTTP_TIMEOUT", 8.0),
        history_size=env_int("BTC_SIGNAL_HISTORY_SIZE", 1800),
        use_simulation=env_bool("BTC_SIGNAL_USE_SIMULATION", False),
        symbol=env_str("BTC_SIGNAL_SYMBOL", "BTCUSDT"),
        markets_file=value_or_none(env_str("BTC_SIGNAL_MARKETS_FILE", "")),
        polymarket_market_url_template=value_or_none(env_str("BTC_SIGNAL_POLYMARKET_MARKET_URL", "")),
        polymarket_book_url_template=value_or_none(env_str("BTC_SIGNAL_POLYMARKET_BOOK_URL", "")),
        polymarket_trades_url_template=value_or_none(env_str("BTC_SIGNAL_POLYMARKET_TRADES_URL", "")),
        polymarket_gamma_api_url=env_str("BTC_SIGNAL_POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com").rstrip("/"),
        polymarket_clob_api_url=env_str("BTC_SIGNAL_POLYMARKET_CLOB_API", "https://clob.polymarket.com").rstrip("/"),
        polymarket_discovery_enabled=env_bool("BTC_SIGNAL_POLYMARKET_DISCOVERY", True),
        extra_market_urls=split_csv(env_str("BTC_SIGNAL_EXTRA_MARKET_URLS", "")),
    )


def default_market_definitions(config: AppConfig) -> list[MarketDefinition]:
    return [
        parse_market_definition(
            {
                "market_id": "btc-5m",
                "label": "BTC 5 分钟实时市场",
                "timeframe_minutes": 5,
                "source": "default",
            },
            default_market_id="btc-5m",
            default_timeframe_minutes=5,
        ),
        parse_market_definition(
            {
                "market_id": "btc-15m",
                "label": "BTC 15 分钟实时市场",
                "timeframe_minutes": 15,
                "source": "default",
            },
            default_market_id="btc-15m",
            default_timeframe_minutes=15,
        ),
    ]


def load_market_definitions(config: AppConfig) -> list[MarketDefinition]:
    if config.markets_file:
        path = Path(config.markets_file)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw = raw.get("markets", [])
            if isinstance(raw, list):
                parsed: list[MarketDefinition] = []
                for index, item in enumerate(raw):
                    if isinstance(item, dict):
                        default_id = str(item.get("market_id") or item.get("id") or f"market-{index + 1}")
                        default_tf = int(item.get("timeframe_minutes") or item.get("timeframe") or 5)
                        parsed.append(parse_market_definition(item, default_id, default_tf))
                if parsed:
                    return parsed
    return default_market_definitions(config)
