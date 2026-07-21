from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import truststore
    import websocket
except ImportError:  # The REST collector remains available without optional dependencies.
    truststore = None
    websocket = None


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: float
    value: float


class PolymarketRealtimeFeed:
    endpoint = "wss://ws-live-data.polymarket.com"

    def __init__(self, cache_path: str | Path | None = Path(__file__).resolve().parents[1] / ".runtime" / "rtds-btc-usd.json") -> None:
        self._points: deque[PricePoint] = deque(maxlen=7200)
        self._cache_path = Path(cache_path) if cache_path else None
        self._last_persisted_at = 0.0
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: Any = None
        self._connected = False
        self._last_error: str | None = None
        self._load_cache()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if websocket is None or truststore is None:
            self._last_error = "未安装 truststore 或 websocket-client，RTDS 实时源未启动"
            return
        truststore.inject_into_ssl()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="polymarket-rtds", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def current_price(self, max_age_seconds: float = 10.0) -> float | None:
        with self._lock:
            if not self._points:
                return None
            point = self._points[-1]
        if time.time() - point.timestamp > max_age_seconds:
            return None
        return point.value

    def price_at(self, timestamp: float, tolerance_seconds: float = 5.0) -> float | None:
        with self._lock:
            points = list(self._points)
        if not points:
            return None
        candidate = min(points, key=lambda point: abs(point.timestamp - timestamp))
        if abs(candidate.timestamp - timestamp) > tolerance_seconds:
            return None
        return candidate.value

    def status(self) -> dict[str, Any]:
        with self._lock:
            latest = self._points[-1] if self._points else None
            point_count = len(self._points)
            connected = self._connected
            last_error = self._last_error
        return {
            "connected": connected,
            "point_count": point_count,
            "latest_timestamp": latest.timestamp if latest else None,
            "latest_price": latest.value if latest else None,
            "last_error": last_error,
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._connect_and_receive()
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._last_error = str(exc)
            if not self._stop_event.wait(2.0):
                continue

    def _connect_and_receive(self) -> None:
        proxy = urllib.parse.urlparse(urllib.request.getproxies().get("https", ""))
        options: dict[str, Any] = {"timeout": 12}
        if proxy.hostname:
            options.update(
                {
                    "http_proxy_host": proxy.hostname,
                    "http_proxy_port": proxy.port,
                    "proxy_type": "http",
                }
            )
        socket = websocket.create_connection(self.endpoint, **options)
        socket.settimeout(6.0)
        self._socket = socket
        with self._lock:
            self._connected = True
            self._last_error = None
        subscription = {
            "action": "subscribe",
            "subscriptions": [
                {
                    "topic": "crypto_prices_chainlink",
                    "type": "*",
                    "filters": "",
                },
            ],
        }
        socket.send(json.dumps(subscription))
        last_ping = time.monotonic()
        try:
            while not self._stop_event.is_set():
                if time.monotonic() - last_ping >= 5.0:
                    socket.send("PING")
                    last_ping = time.monotonic()
                try:
                    message = socket.recv()
                except (TimeoutError, websocket.WebSocketTimeoutException):
                    continue
                if message:
                    self._handle_message(message)
        finally:
            with self._lock:
                self._connected = False
            self._socket = None
            socket.close()

    def _handle_message(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        try:
            event = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(event, dict):
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "").lower()
        if symbol not in {"btc/usd", "btcusd"}:
            return

        raw_points = payload.get("data") if isinstance(payload.get("data"), list) else [payload]
        parsed: list[PricePoint] = []
        for raw in raw_points:
            if not isinstance(raw, dict):
                continue
            try:
                timestamp = float(raw["timestamp"])
                value = float(raw["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            if timestamp > 0 and value > 0:
                parsed.append(PricePoint(timestamp=timestamp, value=value))
        if not parsed:
            return
        with self._lock:
            last_timestamp = self._points[-1].timestamp if self._points else 0.0
            changed = False
            for point in sorted(parsed, key=lambda item: item.timestamp):
                if point.timestamp > last_timestamp:
                    self._points.append(point)
                    last_timestamp = point.timestamp
                    changed = True
        if changed and time.monotonic() - self._last_persisted_at >= 5.0:
            self._persist_cache()

    def _load_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_points = payload.get("points") if isinstance(payload, dict) else None
        if not isinstance(raw_points, list):
            return
        for raw in raw_points:
            if not isinstance(raw, dict):
                continue
            try:
                point = PricePoint(timestamp=float(raw["timestamp"]), value=float(raw["value"]))
            except (KeyError, TypeError, ValueError):
                continue
            if point.timestamp > 0 and point.value > 0:
                self._points.append(point)

    def _persist_cache(self) -> None:
        if self._cache_path is None:
            return
        with self._lock:
            points = list(self._points)
        payload = {
            "points": [
                {"timestamp": point.timestamp, "value": point.value}
                for point in points
            ]
        }
        temporary_path = self._cache_path.with_suffix(".tmp")
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            temporary_path.replace(self._cache_path)
            self._last_persisted_at = time.monotonic()
        except OSError:
            return
