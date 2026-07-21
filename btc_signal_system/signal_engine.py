from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .features import FeatureEngine, FeatureVector
from .models import MarketDefinition, MarketSnapshot, SignalResult
from .utils import clamp, softmax


@dataclass(slots=True)
class SignalWeights:
    spot_1m: float
    spot_5m: float
    spot_15m: float
    contract_1m: float
    contract_5m: float
    contract_15m: float
    target_gap: float
    order_imbalance: float
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
    target_gap=1.4,
    order_imbalance=1.3,
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
    target_gap=1.6,
    order_imbalance=1.0,
    basis=1.1,
    volatility_penalty=0.8,
    spread_penalty=1.0,
)


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
        long_raw = 0.0
        short_raw = 0.0
        neutral_raw = 0.8
        reasons: list[str] = []

        def add_signed(value: float | None, weight: float, scale: float = 1.0) -> None:
            nonlocal long_raw, short_raw
            if value is None:
                return
            normalized = clamp(value / scale, -3.0, 3.0)
            if normalized > 0:
                long_raw += normalized * weight
            elif normalized < 0:
                short_raw += abs(normalized) * weight

        add_signed(features.spot_return_1m, weights.spot_1m, 1.0)
        add_signed(features.spot_return_5m, weights.spot_5m, 2.0)
        add_signed(features.spot_return_15m, weights.spot_15m, 3.0)
        add_signed(features.contract_return_1m, weights.contract_1m, 4.0)
        add_signed(features.contract_return_5m, weights.contract_5m, 8.0)
        add_signed(features.contract_return_15m, weights.contract_15m, 12.0)

        if features.distance_to_target_pct is not None:
            target_signal = clamp(features.distance_to_target_pct / 0.8, -3.0, 3.0)
            if definition.market_bias == "short":
                target_signal *= -1.0
            if target_signal > 0:
                long_raw += target_signal * weights.target_gap
                reasons.append("当前价格位于目标价上方或接近目标价，方向偏多")
            elif target_signal < 0:
                short_raw += abs(target_signal) * weights.target_gap
                reasons.append("当前价格正在远离目标价，方向偏空")

        if features.order_imbalance is not None:
            if features.order_imbalance > 0:
                long_raw += features.order_imbalance * weights.order_imbalance
                reasons.append("订单簿买盘占优")
            elif features.order_imbalance < 0:
                short_raw += abs(features.order_imbalance) * weights.order_imbalance
                reasons.append("订单簿卖盘占优")

        if features.spot_perp_basis_pct is not None:
            add_signed(features.spot_perp_basis_pct, weights.basis, 0.15)

        if features.volatility_5m is not None and features.volatility_5m > 0.35:
            penalty = clamp((features.volatility_5m - 0.35) / 0.25, 0.0, 2.5) * weights.volatility_penalty
            neutral_raw += penalty
            reasons.append("短期波动率处于较高水平")

        if features.spread_pct is not None and features.spread_pct > 4.0:
            penalty = clamp((features.spread_pct - 4.0) / 4.0, 0.0, 2.0) * weights.spread_penalty
            neutral_raw += penalty
            reasons.append("Polymarket 买卖价差较大")

        if features.spot_return_1m is not None and abs(features.spot_return_1m) > 0.15:
            side = "向上" if features.spot_return_1m > 0 else "向下"
            reasons.append(f"BTC 1 分钟动量{side}")
        if features.contract_return_1m is not None and abs(features.contract_return_1m) > 2.0:
            side = "上涨" if features.contract_return_1m > 0 else "下跌"
            reasons.append(f"Polymarket 合约价格{side}")
        if snapshot.contract_price is None:
            neutral_raw += 1.2
            reasons.append("缺少 Polymarket 合约价格")
        if snapshot.target_price is None:
            neutral_raw += 0.8
            reasons.append("尚未获取或解析目标价格")
        if snapshot.current_price is None:
            neutral_raw += 1.2
            reasons.append("缺少 BTC 当前价格")

        if not reasons:
            reasons.append("当前市场多空信号较为均衡")

        probabilities = softmax([long_raw, short_raw, neutral_raw])
        long_probability = probabilities[0] * 100.0
        short_probability = probabilities[1] * 100.0
        neutral_probability = probabilities[2] * 100.0
        best = max(long_probability, short_probability, neutral_probability)
        if best == neutral_probability or best < 45.0:
            direction = "neutral"
        elif best == long_probability:
            direction = "long"
        else:
            direction = "short"

        data_checks = [
            snapshot.current_price is not None,
            snapshot.target_price is not None,
            snapshot.contract_price is not None,
            snapshot.best_bid is not None and snapshot.best_ask is not None,
            snapshot.spot_price is not None or snapshot.perp_price is not None,
            snapshot.order_imbalance is not None,
        ]
        data_quality = sum(data_checks) / len(data_checks)
        confidence = clamp((best - 33.33) / 66.67, 0.0, 1.0) * data_quality
        score = clamp(long_probability - short_probability, -100.0, 100.0)

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
            reasons=reasons[:5],
            snapshot=snapshot,
        )
