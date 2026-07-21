from __future__ import annotations

import math
import os
from statistics import mean as _mean
from typing import Any


def coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def coerce_int(value: Any) -> int | None:
    number = coerce_float(value)
    if number is None:
        return None
    return int(number)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def env_float(name: str, default: float) -> float:
    value = coerce_float(os.getenv(name))
    return default if value is None else value


def env_int(name: str, default: int) -> int:
    value = coerce_int(os.getenv(name))
    return default if value is None else value


def env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip() or default


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    max_value = max(values)
    exps = [math.exp(value - max_value) for value in values]
    total = sum(exps)
    if total == 0:
        return [1.0 / len(values) for _ in values]
    return [value / total for value in exps]


def safe_stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    avg = _mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))

