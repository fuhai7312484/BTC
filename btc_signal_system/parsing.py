from __future__ import annotations

import re
from collections.abc import Mapping

from .models import MarketDefinition
from .utils import coerce_float, coerce_int


PRICE_TOKEN_RE = re.compile(r"(?<!\w)(\d+(?:,\d{3})*(?:\.\d+)?)([kKmMbB]?)")
TIMEFRAME_RE = re.compile(r"(?<!\d)(\d{1,3})\s*(?:m|min|minute|minutes)\b", re.IGNORECASE)


def _scale_number(text: str) -> float | None:
    match = PRICE_TOKEN_RE.search(text)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    suffix = match.group(2).lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    elif suffix == "b":
        number *= 1_000_000_000
    return number


def parse_target_price(text: str | None) -> float | None:
    if not text:
        return None
    priority_patterns = [
        r"(?:target|threshold|at\s*least|greater\s*than|more\s*than|above|over|>=)\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?\s*[kmbKMB]?)",
        r"(?:below|under|less\s*than|at\s*most|<=)\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?\s*[kmbKMB]?)",
        r"\$\s*([0-9][0-9,]*(?:\.\d+)?\s*[kmbKMB]?)",
    ]
    for pattern in priority_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = _scale_number(match.group(1).strip())
            if value is not None:
                return value
    return None


def infer_market_bias(text: str | None) -> str:
    if not text:
        return "unknown"
    lowered = text.lower()
    long_score = sum(
        1
        for keyword in ["above", "over", "higher than", "more than", "at least", "bull", "upside", "long"]
        if keyword in lowered
    )
    short_score = sum(
        1
        for keyword in ["below", "under", "lower than", "less than", "at most", "bear", "downside", "short"]
        if keyword in lowered
    )
    if long_score > short_score:
        return "long"
    if short_score > long_score:
        return "short"
    return "unknown"


def infer_timeframe_minutes(text: str | None, default: int) -> int:
    if not text:
        return default
    match = TIMEFRAME_RE.search(text)
    if match:
        value = coerce_int(match.group(1))
        if value is not None:
            return value
    return default


def parse_market_definition(raw: Mapping[str, object], default_market_id: str, default_timeframe_minutes: int) -> MarketDefinition:
    label = str(
        raw.get("label")
        or raw.get("question")
        or raw.get("title")
        or raw.get("name")
        or default_market_id
    )
    description = str(raw.get("description") or raw.get("rules") or "")
    text = f"{label} {description}"

    market_id = str(raw.get("market_id") or raw.get("id") or default_market_id)
    timeframe_minutes = coerce_int(raw.get("timeframe_minutes") or raw.get("timeframe") or infer_timeframe_minutes(text, default_timeframe_minutes))
    timeframe_minutes = timeframe_minutes if timeframe_minutes is not None else default_timeframe_minutes

    target_price = (
        coerce_float(raw.get("target_price"))
        or coerce_float(raw.get("target"))
        or parse_target_price(text)
    )
    contract_price = coerce_float(raw.get("contract_price") or raw.get("current_price") or raw.get("price"))
    best_bid = coerce_float(raw.get("best_bid") or raw.get("bid") or raw.get("bestBid"))
    best_ask = coerce_float(raw.get("best_ask") or raw.get("ask") or raw.get("bestAsk"))

    return MarketDefinition(
        market_id=market_id,
        label=label,
        timeframe_minutes=timeframe_minutes,
        target_price=target_price,
        contract_price=contract_price,
        best_bid=best_bid,
        best_ask=best_ask,
        market_bias=infer_market_bias(text),
        description=description,
        source=str(raw.get("source") or "config"),
        metadata=dict(raw),
    )

