from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .clients import BinanceClient, PolymarketClient
from .clob_realtime import PolymarketClobRouter
from .config import AppConfig, load_market_definitions
from .models import MarketDefinition, MarketSnapshot, SignalResult, format_datetime, utc_now
from .realtime import PolymarketRealtimeFeed
from .signal_engine import SignalEngine
from .simulation import SimulationFeed


_UNSET = object()


def _merge_numeric(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return float(value)
    return None


@dataclass(slots=True)
class MarketState:
    definition: MarketDefinition
    history: deque[MarketSnapshot]
    latest: SignalResult | None = None
    last_updated: datetime | None = None
    source_mode: str = "initializing"
    last_error: str | None = None


class MarketService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.market_definitions = load_market_definitions(config)
        self.signal_engine = SignalEngine()
        self.simulation = SimulationFeed()
        self.binance = BinanceClient(config)
        self.polymarket = PolymarketClient(config)
        self.realtime = PolymarketRealtimeFeed()
        self._lock = threading.RLock()
        self._clob_pending_lock = threading.Lock()
        self._clob_pending_markets: set[str] = set()
        self._clob_update_event = threading.Event()
        self.clob_realtime = PolymarketClobRouter(on_update=self._queue_clob_update)
        self.states: dict[str, MarketState] = {
            definition.market_id: MarketState(definition=definition, history=deque(maxlen=config.history_size))
            for definition in self.market_definitions
        }
        self._subscribers: list[queue.Queue[str]] = []
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._realtime_publisher: threading.Thread | None = None

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self.realtime.start()
        self.clob_realtime.start()
        self._realtime_publisher = threading.Thread(
            target=self._realtime_publish_loop,
            name="clob-sse-publisher",
            daemon=True,
        )
        self._realtime_publisher.start()
        self._worker = threading.Thread(target=self._run, name="market-service", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._clob_update_event.set()
        self.clob_realtime.stop()
        self.realtime.stop()
        if self._worker:
            self._worker.join(timeout=2.0)
        if self._realtime_publisher:
            self._realtime_publisher.join(timeout=2.0)

    def subscribe(self):
        q: queue.Queue[str] = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, subscriber: queue.Queue[str]) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def _notify(self, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(message)
            except Exception:
                continue

    def _queue_clob_update(self, market_ids: set[str]) -> None:
        with self._clob_pending_lock:
            self._clob_pending_markets.update(market_ids)
        self._clob_update_event.set()

    def _realtime_publish_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._clob_update_event.wait(0.5):
                continue
            if self._stop_event.wait(0.05):
                break
            with self._clob_pending_lock:
                market_ids = set(self._clob_pending_markets)
                self._clob_pending_markets.clear()
                self._clob_update_event.clear()
            if market_ids:
                self._publish_clob_updates(market_ids)

    def _publish_clob_updates(self, market_ids: set[str]) -> None:
        changed = False
        with self._lock:
            for market_id in market_ids:
                state = self.states.get(market_id)
                quote = self.clob_realtime.market_quote(market_id)
                if state is None or state.latest is None or quote is None:
                    continue
                snapshot = self._apply_clob_quote(state.latest.snapshot, quote)
                self._record_snapshot(state, snapshot)
                changed = True
        if changed:
            payload = self.status()
            self._notify(
                {
                    "type": "snapshot",
                    "timestamp": format_datetime(utc_now()),
                    **payload,
                }
            )

    def _record_snapshot(self, state: MarketState, snapshot: MarketSnapshot) -> SignalResult:
        previous = state.latest.snapshot if state.latest else None
        previous_slug = previous.metadata.get("slug") if previous and previous.metadata else None
        current_slug = snapshot.metadata.get("slug") if snapshot.metadata else None
        if previous_slug and current_slug and previous_slug != current_slug:
            state.history.clear()
        elif previous is not None and snapshot.timestamp < previous.timestamp:
            return state.latest

        if state.history and (snapshot.timestamp - state.history[-1].timestamp).total_seconds() < 1.0:
            state.history[-1] = snapshot
        else:
            state.history.append(snapshot)
        signal = self.signal_engine.score(state.definition, snapshot, list(state.history))
        state.latest = signal
        state.last_updated = snapshot.timestamp
        state.source_mode = snapshot.metadata.get("source", "live") if snapshot.metadata else "live"
        state.last_error = None
        return signal

    def _apply_clob_quote(self, snapshot: MarketSnapshot, quote: dict[str, Any]) -> MarketSnapshot:
        metadata = dict(snapshot.metadata or {})
        metadata.update(
            {
                "quote_source": (
                    "clob_websocket" if quote.get("complete") else "clob_websocket_partial"
                ),
                "clob_websocket": self.clob_realtime.status(),
                "clob_event_timestamp": quote.get("updated_at"),
                "clob_event_latency_ms": (
                    round(
                        max(0.0, float(quote["received_at"]) - float(quote["updated_at"])) * 1000.0,
                        2,
                    )
                    if isinstance(quote.get("updated_at"), (int, float))
                    and isinstance(quote.get("received_at"), (int, float))
                    else None
                ),
                "clob_event_age_ms": (
                    round(max(0.0, time.time() - float(quote["received_at"])) * 1000.0, 2)
                    if isinstance(quote.get("received_at"), (int, float))
                    else None
                ),
                "clob_quote_complete": bool(quote.get("complete")),
                "up_token_id": quote.get("up_token_id") or metadata.get("up_token_id"),
                "down_token_id": quote.get("down_token_id") or metadata.get("down_token_id"),
                "up_tick_size": quote.get("up_tick_size") or metadata.get("up_tick_size"),
                "down_tick_size": quote.get("down_tick_size") or metadata.get("down_tick_size"),
                "up_bid_depth": quote.get("up_bid_depth"),
                "up_ask_depth": quote.get("up_ask_depth"),
                "down_bid_depth": quote.get("down_bid_depth"),
                "down_ask_depth": quote.get("down_ask_depth"),
                "up_order_imbalance": quote.get("up_order_imbalance"),
                "down_order_imbalance": quote.get("down_order_imbalance"),
                "up_trade_imbalance": quote.get("up_trade_imbalance"),
                "down_trade_imbalance": quote.get("down_trade_imbalance"),
            }
        )
        chainlink_price = self.realtime.current_price()
        target_price = snapshot.target_price
        if target_price is None:
            market_start = metadata.get("market_start_timestamp")
            if isinstance(market_start, (int, float)):
                target_price = self.realtime.price_at(float(market_start))
                if target_price is not None:
                    metadata["target_source"] = "polymarket_chainlink_window_open"
        if chainlink_price is not None:
            metadata["current_price_source"] = "polymarket_chainlink_rtds"

        return replace(
            snapshot,
            timestamp=utc_now(),
            current_price=chainlink_price if chainlink_price is not None else snapshot.current_price,
            target_price=target_price,
            contract_price=_merge_numeric(quote.get("contract_price"), snapshot.contract_price),
            best_bid=_merge_numeric(quote.get("best_bid"), snapshot.best_bid),
            best_ask=_merge_numeric(quote.get("best_ask"), snapshot.best_ask),
            up_buy_price=_merge_numeric(quote.get("up_buy_price"), snapshot.up_buy_price),
            up_sell_price=_merge_numeric(quote.get("up_sell_price"), snapshot.up_sell_price),
            down_buy_price=_merge_numeric(quote.get("down_buy_price"), snapshot.down_buy_price),
            down_sell_price=_merge_numeric(quote.get("down_sell_price"), snapshot.down_sell_price),
            last_trade=_merge_numeric(quote.get("last_trade"), snapshot.last_trade),
            order_imbalance=_merge_numeric(quote.get("order_imbalance")),
            trade_imbalance=_merge_numeric(quote.get("trade_imbalance")),
            metadata=metadata,
        )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                snapshot = self.refresh()
                self._notify({"type": "snapshot", "timestamp": format_datetime(utc_now()), **snapshot})
            except Exception as exc:
                self._notify({"type": "error", "timestamp": format_datetime(utc_now()), "error": str(exc)})
            elapsed = time.monotonic() - cycle_started
            self._stop_event.wait(max(0.0, self.config.poll_interval_seconds - elapsed))

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            definitions = list(self.market_definitions)
        polymarket_snapshots: dict[str, Any] = {}
        global_error = None
        if self.config.use_simulation:
            binance_snapshot = None
        else:
            with ThreadPoolExecutor(max_workers=len(definitions) + 1) as executor:
                binance_future = executor.submit(self.binance.fetch)
                polymarket_futures = {
                    definition.market_id: executor.submit(self.polymarket.fetch, definition)
                    for definition in definitions
                }
                try:
                    binance_snapshot = binance_future.result()
                except Exception as exc:
                    binance_snapshot = None
                    global_error = str(exc)
                for market_id, future in polymarket_futures.items():
                    try:
                        polymarket_snapshots[market_id] = future.result()
                    except Exception:
                        polymarket_snapshots[market_id] = None

                clob_markets: dict[str, tuple[str, str]] = {}
                for market_id, snapshot in polymarket_snapshots.items():
                    metadata = snapshot.metadata if snapshot and snapshot.metadata else {}
                    up_token = metadata.get("up_token_id")
                    down_token = metadata.get("down_token_id")
                    if up_token and down_token:
                        clob_markets[market_id] = (str(up_token), str(down_token))
                self.clob_realtime.update_markets(clob_markets)

        results: list[dict[str, Any]] = []
        source_modes: set[str] = set()
        for definition in definitions:
            state = self.states[definition.market_id]
            try:
                prefetched = polymarket_snapshots.get(definition.market_id) if not self.config.use_simulation else None
                market_snapshot = self._collect_snapshot(definition, binance_snapshot, prefetched)
                with self._lock:
                    signal = self._record_snapshot(state, market_snapshot)
                    source_modes.add(state.source_mode)
                results.append(signal.as_dict())
            except Exception as exc:
                state.last_error = str(exc)
                source_modes.add("error")
                if state.latest is not None:
                    results.append({**state.latest.as_dict(), "error": str(exc)})
                else:
                    results.append({
                        "market_id": definition.market_id,
                        "label": definition.label,
                        "timeframe_minutes": definition.timeframe_minutes,
                        "error": str(exc),
                    })
        return {
            "markets": results,
            "global_error": global_error,
            "use_simulation": self.config.use_simulation,
            "overall_source_mode": overall_source_mode(self.config.use_simulation, source_modes),
            "updated_at": format_datetime(utc_now()),
        }

    def _collect_snapshot(
        self,
        definition: MarketDefinition,
        binance_snapshot,
        polymarket_snapshot: Any = _UNSET,
    ) -> MarketSnapshot:
        if self.config.use_simulation:
            feed = self.simulation.sample(definition)
            snapshot = MarketSnapshot(
                market_id=definition.market_id,
                label=definition.label,
                timeframe_minutes=definition.timeframe_minutes,
                timestamp=utc_now(),
                current_price=float(feed["current_price"]),
                target_price=float(feed["target_price"]),
                contract_price=float(feed["contract_price"]),
                best_bid=float(feed["best_bid"]),
                best_ask=float(feed["best_ask"]),
                up_buy_price=float(feed["up_buy_price"]),
                up_sell_price=float(feed["up_sell_price"]),
                down_buy_price=float(feed["down_buy_price"]),
                down_sell_price=float(feed["down_sell_price"]),
                last_trade=float(feed["last_trade"]),
                spot_price=float(feed["spot_price"]),
                perp_price=float(feed["perp_price"]),
                volume_1m=float(feed["volume_1m"]),
                volume_5m=float(feed["volume_5m"]),
                volume_15m=float(feed["volume_15m"]),
                order_imbalance=float(feed["order_imbalance"]),
                trade_imbalance=float(feed["trade_imbalance"]),
                metadata={
                    "source": feed.get("source", "simulation"),
                    "market_start_time": feed.get("market_start_time"),
                    "market_end_time": feed.get("market_end_time"),
                    "market_end_timestamp": feed.get("market_end_timestamp"),
                },
            )
            return snapshot

        if polymarket_snapshot is _UNSET:
            try:
                polymarket_snapshot = self.polymarket.fetch(definition)
            except Exception:
                polymarket_snapshot = None

        contract_price = _merge_numeric(
            polymarket_snapshot.contract_price if polymarket_snapshot else None,
            definition.contract_price,
            definition.best_bid and definition.best_ask and (definition.best_bid + definition.best_ask) / 2.0,
        )
        best_bid = _merge_numeric(polymarket_snapshot.best_bid if polymarket_snapshot else None, definition.best_bid)
        best_ask = _merge_numeric(polymarket_snapshot.best_ask if polymarket_snapshot else None, definition.best_ask)
        up_buy_price = _merge_numeric(polymarket_snapshot.up_buy_price if polymarket_snapshot else None, best_ask)
        up_sell_price = _merge_numeric(polymarket_snapshot.up_sell_price if polymarket_snapshot else None, best_bid)
        down_buy_price = _merge_numeric(polymarket_snapshot.down_buy_price if polymarket_snapshot else None)
        down_sell_price = _merge_numeric(polymarket_snapshot.down_sell_price if polymarket_snapshot else None)
        last_trade = _merge_numeric(polymarket_snapshot.last_trade if polymarket_snapshot else None)
        target_price = _merge_numeric(polymarket_snapshot.target_price if polymarket_snapshot else None, definition.target_price)
        market_bias = (polymarket_snapshot.market_bias if polymarket_snapshot else definition.market_bias) or definition.market_bias

        polymarket_metadata = polymarket_snapshot.metadata if polymarket_snapshot and polymarket_snapshot.metadata else {}
        market_start_timestamp = polymarket_metadata.get("market_start_timestamp")
        chainlink_price = self.realtime.current_price()
        target_from_rtds = False
        if target_price is None and isinstance(market_start_timestamp, (int, float)):
            target_price = self.realtime.price_at(float(market_start_timestamp))
            target_from_rtds = target_price is not None

        if binance_snapshot is not None:
            spot_price = binance_snapshot.spot_price
            perp_price = binance_snapshot.perp_price
        else:
            spot_price = None
            perp_price = None

        current_price = chainlink_price if chainlink_price is not None else spot_price
        if current_price is None:
            current_price = perp_price

        has_polymarket = polymarket_snapshot is not None
        has_reference_price = binance_snapshot is not None or chainlink_price is not None
        if has_polymarket and has_reference_price:
            source = "live"
        elif has_polymarket or has_reference_price:
            source = "partial-live"
        else:
            source = "fallback"
        metadata = {
            "source": source,
            "market_bias": market_bias,
            "polymarket_snapshot": bool(polymarket_snapshot),
            "binance_snapshot": binance_snapshot is not None,
        }
        if polymarket_snapshot and polymarket_snapshot.metadata:
            metadata.update(polymarket_snapshot.metadata)
        metadata.update(
            {
                "current_price_source": "polymarket_chainlink_rtds" if chainlink_price is not None else "binance_rest",
                "target_source": (
                    "polymarket_chainlink_window_open"
                    if target_from_rtds
                    else metadata.get("target_source")
                ),
                "rtds": self.realtime.status(),
            }
        )
        if binance_snapshot is not None:
            metadata.update(
                {
                    "spot_bid": binance_snapshot.spot_bid,
                    "spot_ask": binance_snapshot.spot_ask,
                    "perp_bid": binance_snapshot.perp_bid,
                    "perp_ask": binance_snapshot.perp_ask,
                    "spot_volume_24h": binance_snapshot.spot_volume_24h,
                    "perp_volume_24h": binance_snapshot.perp_volume_24h,
                }
            )
        metadata["source"] = source

        snapshot = MarketSnapshot(
            market_id=definition.market_id,
            label=definition.label,
            timeframe_minutes=definition.timeframe_minutes,
            timestamp=utc_now(),
            current_price=float(current_price) if current_price is not None else None,
            target_price=target_price,
            contract_price=contract_price,
            best_bid=best_bid,
            best_ask=best_ask,
            up_buy_price=up_buy_price,
            up_sell_price=up_sell_price,
            down_buy_price=down_buy_price,
            down_sell_price=down_sell_price,
            last_trade=last_trade,
            spot_price=spot_price,
            perp_price=perp_price,
            volume_1m=None,
            volume_5m=None,
            volume_15m=None,
            order_imbalance=None,
            trade_imbalance=None,
            metadata=metadata,
        )
        realtime_quote = self.clob_realtime.market_quote(definition.market_id)
        if realtime_quote is not None:
            snapshot = self._apply_clob_quote(snapshot, realtime_quote)
        return snapshot

    def status(self) -> dict[str, Any]:
        with self._lock:
            markets = []
            modes = set()
            for state in self.states.values():
                latest = state.latest.as_dict() if state.latest else None
                modes.add(state.source_mode)
                markets.append(
                    {
                        "market_id": state.definition.market_id,
                        "label": state.definition.label,
                        "timeframe_minutes": state.definition.timeframe_minutes,
                        "latest": latest,
                        "last_updated": format_datetime(state.last_updated),
                        "realtime_updated_at": (
                            latest.get("snapshot", {}).get("timestamp") if latest else None
                        ),
                        "source_mode": state.source_mode,
                        "last_error": state.last_error,
                    }
                )
        overall_mode = overall_source_mode(self.config.use_simulation, modes)
        return {
            "updated_at": format_datetime(utc_now()),
            "poll_interval_seconds": self.config.poll_interval_seconds,
            "use_simulation": self.config.use_simulation,
            "overall_source_mode": overall_mode,
            "realtime": self.realtime.status(),
            "clob_realtime": self.clob_realtime.status(),
            "markets": markets,
        }

    def market_state(self, market_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self.states.get(market_id)
            if state is None:
                return None
            latest = state.latest.as_dict() if state.latest else None
            history = [snapshot_to_dict(item) for item in state.history]
        return {
            "market_id": state.definition.market_id,
            "label": state.definition.label,
            "timeframe_minutes": state.definition.timeframe_minutes,
            "latest": latest,
            "history": history,
            "last_updated": format_datetime(state.last_updated),
            "source_mode": state.source_mode,
            "last_error": state.last_error,
        }


def overall_source_mode(use_simulation: bool, modes: set[str]) -> str:
    if use_simulation:
        return "simulation"
    if "initializing" in modes and len(modes) == 1:
        return "initializing"
    if modes == {"live"}:
        return "live"
    if "live" in modes and ("fallback" in modes or "partial-live" in modes or "error" in modes):
        return "partial-live"
    if "partial-live" in modes:
        return "partial-live"
    if "live" in modes:
        return "live"
    if "fallback" in modes:
        return "fallback"
    if "error" in modes:
        return "error"
    return "unknown"

def snapshot_to_dict(snapshot: MarketSnapshot) -> dict[str, Any]:
    return {
        "market_id": snapshot.market_id,
        "label": snapshot.label,
        "timeframe_minutes": snapshot.timeframe_minutes,
        "timestamp": format_datetime(snapshot.timestamp),
        "current_price": snapshot.current_price,
        "target_price": snapshot.target_price,
        "contract_price": snapshot.contract_price,
        "price_gap": snapshot.price_gap,
        "distance_to_target_pct": snapshot.distance_to_target_pct,
        "best_bid": snapshot.best_bid,
        "best_ask": snapshot.best_ask,
        "up_buy_price": snapshot.up_buy_price,
        "up_sell_price": snapshot.up_sell_price,
        "down_buy_price": snapshot.down_buy_price,
        "down_sell_price": snapshot.down_sell_price,
        "last_trade": snapshot.last_trade,
        "spot_price": snapshot.spot_price,
        "perp_price": snapshot.perp_price,
        "volume_1m": snapshot.volume_1m,
        "volume_5m": snapshot.volume_5m,
        "volume_15m": snapshot.volume_15m,
        "order_imbalance": snapshot.order_imbalance,
        "trade_imbalance": snapshot.trade_imbalance,
        "metadata": snapshot.metadata,
    }
