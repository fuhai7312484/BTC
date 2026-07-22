from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo


SignalDirection = Literal["long", "short", "neutral"]
TradeAction = Literal["buy_up", "buy_down", "hold"]
MarketBias = Literal["long", "short", "unknown"]
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def format_unix_timestamp(value: float | int | None) -> str | None:
    if value is None:
        return None
    return format_datetime(datetime.fromtimestamp(float(value), tz=timezone.utc))


@dataclass(slots=True)
class MarketDefinition:
    market_id: str
    label: str
    timeframe_minutes: int
    target_price: float | None = None
    contract_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    market_bias: MarketBias = "unknown"
    description: str = ""
    source: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MarketSnapshot:
    market_id: str
    label: str
    timeframe_minutes: int
    timestamp: datetime
    current_price: float | None
    target_price: float | None
    contract_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    up_buy_price: float | None = None
    up_sell_price: float | None = None
    down_buy_price: float | None = None
    down_sell_price: float | None = None
    last_trade: float | None = None
    spot_price: float | None = None
    perp_price: float | None = None
    volume_1m: float | None = None
    volume_5m: float | None = None
    volume_15m: float | None = None
    order_imbalance: float | None = None
    trade_imbalance: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def price_gap(self) -> float | None:
        if self.current_price is None or self.target_price is None:
            return None
        return self.current_price - self.target_price

    @property
    def distance_to_target_pct(self) -> float | None:
        if self.current_price is None or self.target_price in (None, 0):
            return None
        return (self.current_price - self.target_price) / self.target_price * 100.0

    @property
    def implied_probability(self) -> float | None:
        if self.contract_price is None or self.contract_price < 0:
            return None
        if self.contract_price > 1.0:
            if self.contract_price <= 100.0:
                return self.contract_price
            return None
        return self.contract_price * 100.0


@dataclass(slots=True)
class SignalResult:
    market_id: str
    label: str
    timeframe_minutes: int
    timestamp: datetime
    direction: SignalDirection
    confidence: float
    long_probability: float
    short_probability: float
    neutral_probability: float
    score: float
    model_up_probability: float
    market_probability: float | None
    time_probability: float | None
    seconds_to_expiry: float | None
    trade_action: TradeAction
    up_edge: float | None
    down_edge: float | None
    up_entry_price: float | None
    down_entry_price: float | None
    data_quality: float
    reasons: list[str]
    snapshot: MarketSnapshot

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "label": self.label,
            "timeframe_minutes": self.timeframe_minutes,
            "timestamp": format_datetime(self.timestamp),
            "direction": self.direction,
            "confidence": round(self.confidence, 4),
            "long_probability": round(self.long_probability, 4),
            "short_probability": round(self.short_probability, 4),
            "neutral_probability": round(self.neutral_probability, 4),
            "score": round(self.score, 4),
            "model_up_probability": round(self.model_up_probability, 4),
            "market_probability": round(self.market_probability, 4) if self.market_probability is not None else None,
            "time_probability": round(self.time_probability, 4) if self.time_probability is not None else None,
            "seconds_to_expiry": round(self.seconds_to_expiry, 3) if self.seconds_to_expiry is not None else None,
            "trade_action": self.trade_action,
            "up_edge": round(self.up_edge, 4) if self.up_edge is not None else None,
            "down_edge": round(self.down_edge, 4) if self.down_edge is not None else None,
            "up_entry_price": round(self.up_entry_price, 6) if self.up_entry_price is not None else None,
            "down_entry_price": round(self.down_entry_price, 6) if self.down_entry_price is not None else None,
            "data_quality": round(self.data_quality, 4),
            "reasons": self.reasons,
            "snapshot": {
                "market_id": self.snapshot.market_id,
                "label": self.snapshot.label,
                "timeframe_minutes": self.snapshot.timeframe_minutes,
                "timestamp": format_datetime(self.snapshot.timestamp),
                "current_price": self.snapshot.current_price,
                "target_price": self.snapshot.target_price,
                "price_gap": self.snapshot.price_gap,
                "distance_to_target_pct": self.snapshot.distance_to_target_pct,
                "contract_price": self.snapshot.contract_price,
                "implied_probability": self.snapshot.implied_probability,
                "best_bid": self.snapshot.best_bid,
                "best_ask": self.snapshot.best_ask,
                "up_buy_price": self.snapshot.up_buy_price,
                "up_sell_price": self.snapshot.up_sell_price,
                "down_buy_price": self.snapshot.down_buy_price,
                "down_sell_price": self.snapshot.down_sell_price,
                "last_trade": self.snapshot.last_trade,
                "spot_price": self.snapshot.spot_price,
                "perp_price": self.snapshot.perp_price,
                "volume_1m": self.snapshot.volume_1m,
                "volume_5m": self.snapshot.volume_5m,
                "volume_15m": self.snapshot.volume_15m,
                "order_imbalance": self.snapshot.order_imbalance,
                "trade_imbalance": self.snapshot.trade_imbalance,
                "metadata": self.snapshot.metadata,
            },
        }
