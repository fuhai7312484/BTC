from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import MarketDefinition, format_unix_timestamp
from .utils import clamp


@dataclass(slots=True)
class SimulationState:
    base_price: float
    target_price: float
    contract_price: float
    started_at: float
    phase: float
    rng: random.Random = field(repr=False)


class SimulationFeed:
    def __init__(self) -> None:
        self._states: dict[str, SimulationState] = {}

    def sample(self, definition: MarketDefinition) -> dict[str, float | str | None]:
        now = datetime.now(timezone.utc)
        timestamp = now.timestamp()
        state = self._states.get(definition.market_id)
        if state is None:
            seed = abs(hash(definition.market_id)) % (2**32)
            rng = random.Random(seed)
            base_price = 65_000.0 + rng.uniform(-1_500.0, 1_500.0)
            target_price = definition.target_price or round(base_price * (1.004 if definition.timeframe_minutes <= 5 else 1.008), 2)
            contract_price = definition.contract_price or clamp(50.0 + rng.uniform(-8.0, 8.0), 5.0, 95.0)
            state = SimulationState(
                base_price=base_price,
                target_price=target_price,
                contract_price=contract_price,
                started_at=timestamp,
                phase=rng.uniform(0.0, math.pi),
                rng=rng,
            )
            self._states[definition.market_id] = state

        elapsed = timestamp - state.started_at
        timeframe_scale = 1.0 if definition.timeframe_minutes <= 5 else 1.35
        directional_bias = 1.0 if definition.market_bias != "short" else -1.0
        wave = (
            180.0 * math.sin(elapsed / (28.0 * timeframe_scale) + state.phase)
            + 90.0 * math.sin(elapsed / (9.0 * timeframe_scale) + state.phase * 0.7)
        )
        drift = directional_bias * elapsed * (0.35 if definition.timeframe_minutes <= 5 else 0.22)
        noise = state.rng.uniform(-18.0, 18.0)
        current_price = max(1_000.0, state.base_price + wave + drift + noise)

        target_price = definition.target_price or state.target_price
        if target_price is None:
            target_price = round(state.base_price * (1.004 if definition.timeframe_minutes <= 5 else 1.008), 2)

        distance_pct = ((current_price - target_price) / target_price) * 100.0
        contract_bias = 50.0 + directional_bias * 4.5 + math.tanh(distance_pct / 1.8) * 22.0
        contract_price = clamp(contract_bias + state.rng.uniform(-1.6, 1.6), 1.0, 99.0)
        spread = clamp(0.5 + abs(math.sin(elapsed / 13.0)) * 1.7, 0.2, 3.5)
        best_bid = clamp(contract_price - spread / 2.0, 1.0, 99.0)
        best_ask = clamp(contract_price + spread / 2.0, 1.0, 99.0)
        last_trade = clamp(contract_price + state.rng.uniform(-0.8, 0.8), 1.0, 99.0)
        perp_price = current_price * (1.0 + math.sin(elapsed / 21.0) * 0.0008)
        volume_1m = 12_000.0 + abs(math.sin(elapsed / 7.0)) * 18_000.0 + state.rng.uniform(-500.0, 500.0)
        volume_5m = volume_1m * (4.8 + abs(math.sin(elapsed / 17.0)))
        volume_15m = volume_5m * (2.6 + abs(math.sin(elapsed / 31.0)))
        order_imbalance = clamp(math.sin(elapsed / 11.0 + state.phase) * 0.78 + state.rng.uniform(-0.12, 0.12), -1.0, 1.0)
        timeframe_seconds = definition.timeframe_minutes * 60
        market_end_timestamp = (int(timestamp // timeframe_seconds) + 1) * timeframe_seconds
        market_start_timestamp = market_end_timestamp - timeframe_seconds
        up_buy_price = best_ask / 100.0
        up_sell_price = best_bid / 100.0
        down_buy_price = (100.0 - best_bid) / 100.0
        down_sell_price = (100.0 - best_ask) / 100.0

        return {
            "current_price": round(current_price, 2),
            "target_price": round(target_price, 2),
            "contract_price": round(contract_price / 100.0, 4),
            "best_bid": round(up_sell_price, 4),
            "best_ask": round(up_buy_price, 4),
            "up_buy_price": round(up_buy_price, 4),
            "up_sell_price": round(up_sell_price, 4),
            "down_buy_price": round(down_buy_price, 4),
            "down_sell_price": round(down_sell_price, 4),
            "last_trade": round(last_trade / 100.0, 4),
            "spot_price": round(current_price, 2),
            "perp_price": round(perp_price, 2),
            "volume_1m": round(volume_1m, 2),
            "volume_5m": round(volume_5m, 2),
            "volume_15m": round(volume_15m, 2),
            "order_imbalance": round(order_imbalance, 4),
            "market_start_time": format_unix_timestamp(market_start_timestamp),
            "market_end_time": format_unix_timestamp(market_end_timestamp),
            "market_end_timestamp": market_end_timestamp,
            "source": "simulation",
        }
