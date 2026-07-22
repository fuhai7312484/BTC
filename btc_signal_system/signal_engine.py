from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .features import FeatureEngine, FeatureVector
from .models import MarketDefinition, MarketSnapshot, SignalResult
from .utils import clamp


@dataclass(slots=True)
class SignalWeights:
    spot_1m: float
    spot_5m: float
    spot_15m: float
    contract_1m: float
    contract_5m: float
    contract_15m: float
    momentum: float
    market_probability: float
    time_probability: float
    target_gap: float
    order_imbalance: float
    trade_imbalance: float
    basis: float
    volatility_penalty: float
    spread_penalty: float


WEIGHTS_5M = SignalWeights(
    spot_1m=1.8,
    spot_5m=1.1,
    spot_15m=0.4,
    contract_1m=1.4,
    contract_5m=1.0,
    contract_15m=0.3,
    momentum=0.15,
    market_probability=0.30,
    time_probability=0.45,
    target_gap=0.35,
    order_imbalance=0.06,
    trade_imbalance=0.04,
    basis=0.8,
    volatility_penalty=0.9,
    spread_penalty=1.1,
)

WEIGHTS_15M = SignalWeights(
    spot_1m=0.7,
    spot_5m=1.5,
    spot_15m=1.1,
    contract_1m=0.8,
    contract_5m=1.2,
    contract_15m=0.8,
    momentum=0.15,
    market_probability=0.25,
    time_probability=0.50,
    target_gap=0.35,
    order_imbalance=0.06,
    trade_imbalance=0.04,
    basis=1.1,
    volatility_penalty=0.8,
    spread_penalty=1.0,
)

MINIMUM_NET_EDGE_PERCENT = 2.0
DEFAULT_PRICE_TICK = 0.01


class SignalEngine:
    def __init__(self) -> None:
        self.features = FeatureEngine()

    def score(self, definition: MarketDefinition, snapshot: MarketSnapshot, history: Sequence[MarketSnapshot]) -> SignalResult:
        feature_vector = self.features.build(snapshot, history)
        weights = WEIGHTS_5M if definition.timeframe_minutes <= 5 else WEIGHTS_15M
        return self._score_from_features(definition, snapshot, feature_vector, weights)

    def _score_from_features(
        self,
        definition: MarketDefinition,
        snapshot: MarketSnapshot,
        features: FeatureVector,
        weights: SignalWeights,
    ) -> SignalResult:
        reasons: list[str] = []
        probability_components: list[tuple[float, float]] = []

        def add_component(probability: float | None, weight: float) -> None:
            if probability is not None and weight > 0:
                probability_components.append((clamp(probability, 1.0, 99.0), weight))

        add_component(features.market_probability, weights.market_probability)
        if features.market_probability is not None:
            if features.market_probability >= 55.0:
                reasons.append("Polymarket UP/DOWN 双边盘口整体偏多")
            elif features.market_probability <= 45.0:
                reasons.append("Polymarket UP/DOWN 双边盘口整体偏空")

        if features.time_probability is not None:
            time_probability = features.time_probability
            if definition.market_bias == "short":
                time_probability = 100.0 - time_probability
            add_component(time_probability, weights.time_probability)
            remaining = int(features.seconds_to_expiry or 0)
            if time_probability >= 55.0:
                reasons.append(f"结合剩余 {remaining} 秒与波动率，目标价结算概率偏多")
            elif time_probability <= 45.0:
                reasons.append(f"结合剩余 {remaining} 秒与波动率，目标价结算概率偏空")
        elif features.distance_to_target_pct is not None:
            target_signal = clamp(features.distance_to_target_pct / 0.8, -3.0, 3.0)
            if definition.market_bias == "short":
                target_signal *= -1.0
            target_probability = 50.0 + math.tanh(target_signal) * 40.0
            add_component(target_probability, weights.target_gap)
            if target_signal > 0:
                reasons.append("当前价格位于目标价上方或接近目标价，方向偏多")
            elif target_signal < 0:
                reasons.append("当前价格正在远离目标价，方向偏空")

        momentum_inputs = (
            (features.spot_return_1m, weights.spot_1m, 1.0),
            (features.spot_return_5m, weights.spot_5m, 2.0),
            (features.spot_return_15m, weights.spot_15m, 3.0),
            (features.contract_return_1m, weights.contract_1m, 4.0),
            (features.contract_return_5m, weights.contract_5m, 8.0),
            (features.contract_return_15m, weights.contract_15m, 12.0),
            (features.spot_perp_basis_pct, weights.basis, 0.15),
        )
        momentum_evidence = sum(
            clamp(value / scale, -3.0, 3.0) * weight
            for value, weight, scale in momentum_inputs
            if value is not None
        )
        if any(value is not None for value, _, _ in momentum_inputs):
            momentum_probability = 50.0 + math.tanh(momentum_evidence / 4.0) * 35.0
            add_component(momentum_probability, weights.momentum)

        if features.order_imbalance is not None:
            add_component(50.0 + features.order_imbalance * 25.0, weights.order_imbalance)
            if features.order_imbalance > 0:
                reasons.append("订单簿买盘占优")
            elif features.order_imbalance < 0:
                reasons.append("订单簿卖盘占优")

        if features.trade_imbalance is not None:
            add_component(50.0 + features.trade_imbalance * 25.0, weights.trade_imbalance)
            if features.trade_imbalance > 0:
                reasons.append("最近 60 秒主动成交偏向 UP")
            elif features.trade_imbalance < 0:
                reasons.append("最近 60 秒主动成交偏向 DOWN")
        neutral_penalty = 0.0
        if features.volatility_5m is not None and features.volatility_5m > 0.35:
            penalty = clamp((features.volatility_5m - 0.35) / 0.25, 0.0, 2.5) * weights.volatility_penalty
            neutral_penalty += penalty * 8.0
            reasons.append("短期波动率处于较高水平")

        if features.spread_pct is not None and features.spread_pct > 4.0:
            penalty = clamp((features.spread_pct - 4.0) / 4.0, 0.0, 2.0) * weights.spread_penalty
            neutral_penalty += penalty * 8.0
            reasons.append("Polymarket 买卖价差较大")

        disagreement = None
        if features.market_probability is not None and features.time_probability is not None:
            disagreement = abs(features.market_probability - features.time_probability)
            if disagreement > 25.0:
                neutral_penalty += clamp((disagreement - 25.0) * 0.8, 0.0, 25.0)
                reasons.append("盘口概率与价格结算概率分歧较大")

        if features.spot_return_1m is not None and abs(features.spot_return_1m) > 0.15:
            side = "向上" if features.spot_return_1m > 0 else "向下"
            reasons.append(f"BTC 1 分钟动量{side}")
        if features.contract_return_1m is not None and abs(features.contract_return_1m) > 2.0:
            side = "上涨" if features.contract_return_1m > 0 else "下跌"
            reasons.append(f"Polymarket 合约价格{side}")
        if snapshot.contract_price is None:
            neutral_penalty += 15.0
            reasons.append("缺少 Polymarket 合约价格")
        if snapshot.target_price is None:
            neutral_penalty += 15.0
            reasons.append("尚未获取或解析目标价格")
        if snapshot.current_price is None:
            neutral_penalty += 25.0
            reasons.append("缺少 BTC 当前价格")
        if features.time_probability is None:
            neutral_penalty += 10.0

        if not reasons:
            reasons.append("当前市场多空信号较为均衡")

        data_checks = [
            snapshot.current_price is not None,
            snapshot.target_price is not None,
            snapshot.contract_price is not None,
            snapshot.best_bid is not None and snapshot.best_ask is not None,
            snapshot.spot_price is not None or snapshot.perp_price is not None,
            snapshot.order_imbalance is not None,
            snapshot.trade_imbalance is not None,
            features.seconds_to_expiry is not None,
        ]
        data_quality = sum(data_checks) / len(data_checks)
        component_weight = sum(weight for _, weight in probability_components)
        model_up_probability = (
            sum(probability * weight for probability, weight in probability_components) / component_weight
            if component_weight > 0
            else 50.0
        )
        neutral_penalty += (1.0 - data_quality) * 15.0
        neutral_probability = clamp(
            45.0 - abs(model_up_probability - 50.0) * 1.5 + neutral_penalty,
            0.0,
            90.0,
        )
        directional_mass = 100.0 - neutral_probability
        long_probability = directional_mass * model_up_probability / 100.0
        short_probability = directional_mass - long_probability
        best = max(long_probability, short_probability, neutral_probability)
        if best == neutral_probability or best < 45.0:
            direction = "neutral"
        elif long_probability > short_probability:
            direction = "long"
        else:
            direction = "short"

        agreement_factor = 1.0 - min((disagreement or 0.0) / 100.0, 0.5)
        confidence = clamp(abs(model_up_probability - 50.0) / 50.0, 0.0, 1.0) * data_quality * agreement_factor
        score = clamp((model_up_probability - 50.0) * 2.0, -100.0, 100.0)
        up_edge = self._trade_edge(
            model_up_probability / 100.0,
            snapshot.up_buy_price,
            snapshot,
        )
        down_edge = self._trade_edge(
            1.0 - model_up_probability / 100.0,
            snapshot.down_buy_price,
            snapshot,
        )
        trade_ready = (
            features.time_probability is not None
            and features.market_probability is not None
            and data_quality >= 0.625
            and (disagreement is None or disagreement <= 25.0)
        )
        up_entry_price = (
            self._entry_price(model_up_probability / 100.0, snapshot, "up")
            if trade_ready
            else None
        )
        down_entry_price = (
            self._entry_price(1.0 - model_up_probability / 100.0, snapshot, "down")
            if trade_ready
            else None
        )
        if (
            trade_ready
            and up_edge is not None
            and up_edge >= MINIMUM_NET_EDGE_PERCENT
            and (down_edge is None or up_edge >= down_edge)
        ):
            trade_action = "buy_up"
        elif trade_ready and down_edge is not None and down_edge >= MINIMUM_NET_EDGE_PERCENT:
            trade_action = "buy_down"
        else:
            trade_action = "hold"

        return SignalResult(
            market_id=definition.market_id,
            label=definition.label,
            timeframe_minutes=definition.timeframe_minutes,
            timestamp=snapshot.timestamp,
            direction=direction,
            confidence=confidence,
            long_probability=long_probability,
            short_probability=short_probability,
            neutral_probability=neutral_probability,
            score=score,
            model_up_probability=model_up_probability,
            market_probability=features.market_probability,
            time_probability=features.time_probability,
            seconds_to_expiry=features.seconds_to_expiry,
            trade_action=trade_action,
            up_edge=up_edge,
            down_edge=down_edge,
            up_entry_price=up_entry_price,
            down_entry_price=down_entry_price,
            data_quality=data_quality,
            reasons=reasons[:5],
            snapshot=snapshot,
        )

    def _trade_edge(
        self,
        estimated_probability: float,
        executable_price: float | None,
        snapshot: MarketSnapshot,
    ) -> float | None:
        if executable_price is None or not 0.0 <= executable_price <= 1.0:
            return None
        fee_rate = self._fee_rate(snapshot)
        fee_per_share = fee_rate * executable_price * (1.0 - executable_price)
        return (estimated_probability - executable_price - fee_per_share) * 100.0

    def _entry_price(
        self,
        estimated_probability: float,
        snapshot: MarketSnapshot,
        outcome: str,
    ) -> float | None:
        edge_reserve = MINIMUM_NET_EDGE_PERCENT / 100.0
        maximum_total_cost = estimated_probability - edge_reserve
        if not 0.0 < maximum_total_cost <= 1.0:
            return None

        fee_rate = self._fee_rate(snapshot)
        low = 0.0
        high = maximum_total_cost
        for _ in range(48):
            candidate = (low + high) / 2.0
            total_cost = candidate + fee_rate * candidate * (1.0 - candidate)
            if total_cost <= maximum_total_cost:
                low = candidate
            else:
                high = candidate

        tick_size = self._tick_size(snapshot, outcome)
        entry_price = math.floor((low + 1e-12) / tick_size) * tick_size
        if entry_price < tick_size:
            return None
        return round(min(entry_price, 1.0 - tick_size), 6)

    @staticmethod
    def _fee_rate(snapshot: MarketSnapshot) -> float:
        metadata = snapshot.metadata or {}
        fee_rate = metadata.get("taker_fee_rate")
        if not isinstance(fee_rate, (int, float)):
            fee_rate = 0.07 if metadata.get("fees_enabled") else 0.0
        return max(0.0, float(fee_rate))

    @staticmethod
    def _tick_size(snapshot: MarketSnapshot, outcome: str) -> float:
        metadata = snapshot.metadata or {}
        for key in (
            f"{outcome}_tick_size",
            "tick_size",
            "minimum_tick_size",
            "order_price_min_tick_size",
        ):
            tick_size = metadata.get(key)
            if isinstance(tick_size, (int, float)) and 0.0 < float(tick_size) <= 1.0:
                return float(tick_size)
        return DEFAULT_PRICE_TICK
