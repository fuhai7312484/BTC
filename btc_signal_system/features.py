from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from .models import MarketSnapshot
from .utils import safe_stdev


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return ((new - old) / old) * 100.0


def latest_before(history: Sequence[MarketSnapshot], timestamp, delta_seconds: int) -> MarketSnapshot | None:
    target = timestamp - timedelta(seconds=delta_seconds)
    older: MarketSnapshot | None = None
    for snapshot in history:
        if snapshot.timestamp <= target:
            older = snapshot
        else:
            break
    return older


def recent_returns(history: Sequence[MarketSnapshot], attr: str, window_seconds: int) -> list[float]:
    if not history:
        return []
    latest = history[-1]
    window_start = latest.timestamp - timedelta(seconds=window_seconds)
    series = [getattr(item, attr) for item in history if item.timestamp >= window_start and getattr(item, attr) is not None]
    if len(series) < 2:
        return []
    returns: list[float] = []
    for index in range(1, len(series)):
        prev = series[index - 1]
        current = series[index]
        if prev in (None, 0):
            continue
        returns.append(((current - prev) / prev) * 100.0)
    return returns


@dataclass(slots=True)
class FeatureVector:
    spot_return_30s: float | None = None
    spot_return_1m: float | None = None
    spot_return_5m: float | None = None
    spot_return_15m: float | None = None
    contract_return_1m: float | None = None
    contract_return_5m: float | None = None
    contract_return_15m: float | None = None
    spread_pct: float | None = None
    order_imbalance: float | None = None
    distance_to_target_pct: float | None = None
    price_gap: float | None = None
    implied_probability: float | None = None
    spot_perp_basis_pct: float | None = None
    volatility_5m: float | None = None
    volatility_15m: float | None = None
    volume_trend: float | None = None
    momentum_score: float | None = None


class FeatureEngine:
    def build(self, snapshot: MarketSnapshot, history: Sequence[MarketSnapshot]) -> FeatureVector:
        spot_returns = self._build_series(snapshot, history, "current_price", [30, 60, 300, 900])
        contract_returns = self._build_series(snapshot, history, "contract_price", [60, 300, 900])

        spread_pct = None
        if snapshot.best_bid is not None and snapshot.best_ask is not None and snapshot.contract_price not in (None, 0):
            spread_pct = ((snapshot.best_ask - snapshot.best_bid) / max(snapshot.contract_price, 1.0)) * 100.0

        basis = None
        if snapshot.spot_price is not None and snapshot.perp_price is not None and snapshot.spot_price != 0:
            basis = ((snapshot.perp_price - snapshot.spot_price) / snapshot.spot_price) * 100.0

        volume_trend = None
        if snapshot.volume_1m is not None and snapshot.volume_5m is not None and snapshot.volume_1m != 0:
            volume_trend = snapshot.volume_5m / snapshot.volume_1m

        spot_returns_window = recent_returns(history, "current_price", 5 * 60)
        spot_returns_long = recent_returns(history, "current_price", 15 * 60)

        volatility_5m = safe_stdev(spot_returns_window)
        volatility_15m = safe_stdev(spot_returns_long)
        momentum_score = self._momentum_score(
            spot_returns.get(30),
            spot_returns.get(60),
            spot_returns.get(300),
            contract_returns.get(60),
            contract_returns.get(300),
        )

        return FeatureVector(
            spot_return_30s=spot_returns.get(30),
            spot_return_1m=spot_returns.get(60),
            spot_return_5m=spot_returns.get(300),
            spot_return_15m=spot_returns.get(900),
            contract_return_1m=contract_returns.get(60),
            contract_return_5m=contract_returns.get(300),
            contract_return_15m=contract_returns.get(900),
            spread_pct=spread_pct,
            order_imbalance=snapshot.order_imbalance,
            distance_to_target_pct=snapshot.distance_to_target_pct,
            price_gap=snapshot.price_gap,
            implied_probability=snapshot.implied_probability,
            spot_perp_basis_pct=basis,
            volatility_5m=volatility_5m,
            volatility_15m=volatility_15m,
            volume_trend=volume_trend,
            momentum_score=momentum_score,
        )

    def _build_series(self, snapshot: MarketSnapshot, history: Sequence[MarketSnapshot], attr: str, windows: list[int]) -> dict[int, float | None]:
        values: dict[int, float | None] = {}
        for seconds in windows:
            previous = latest_before(history, snapshot.timestamp, seconds)
            values[seconds] = pct_change(getattr(snapshot, attr), getattr(previous, attr) if previous else None)
        return values

    def _momentum_score(
        self,
        ret_30s: float | None,
        ret_1m: float | None,
        ret_5m: float | None,
        contract_1m: float | None,
        contract_5m: float | None,
    ) -> float:
        components = [
            (ret_30s or 0.0) * 0.35,
            (ret_1m or 0.0) * 0.75,
            (ret_5m or 0.0) * 1.10,
            (contract_1m or 0.0) * 0.18,
            (contract_5m or 0.0) * 0.22,
        ]
        return sum(components)
