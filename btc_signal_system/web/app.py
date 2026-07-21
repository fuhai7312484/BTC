from __future__ import annotations

import json
import mimetypes
import queue
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import AppConfig, load_config
from ..service import MarketService


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"


def _response(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class AppState:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.service = MarketService(config)
        self.service.start()


def create_handler(state: AppState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "BTCSignalHTTP/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/":
                self._serve_index()
                return
            if path == "/api/status":
                self._serve_json(state.service.status())
                return
            if path == "/api/markets":
                payload = {"markets": state.service.status()["markets"]}
                self._serve_json(payload)
                return
            if path.startswith("/api/market/"):
                market_id = path.split("/api/market/", 1)[1]
                payload = state.service.market_state(market_id)
                if payload is None:
                    self._serve_json({"error": "未找到市场"}, HTTPStatus.NOT_FOUND)
                    return
                self._serve_json(payload)
                return
            if path == "/api/stream":
                self._serve_stream()
                return
            if path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
                return
            self._serve_json({"error": "未找到请求的资源"}, HTTPStatus.NOT_FOUND)

        def _serve_json(self, payload, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            _response(self, status, body)

        def _serve_index(self) -> None:
            index_path = STATIC_DIR / "index.html"
            body = index_path.read_bytes()
            _response(self, HTTPStatus.OK, body, "text/html; charset=utf-8")

        def _serve_static(self, name: str) -> None:
            file_path = STATIC_DIR / name
            if not file_path.exists() or not file_path.is_file():
                self._serve_json({"error": "未找到静态资源"}, HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            _response(self, HTTPStatus.OK, file_path.read_bytes(), content_type)

        def _serve_stream(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            subscriber = state.service.subscribe()
            try:
                snapshot = json.dumps(state.service.status(), ensure_ascii=False)
                self.wfile.write(f"event: snapshot\ndata: {snapshot}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        message = subscriber.get(timeout=max(1.0, state.config.poll_interval_seconds))
                    except queue.Empty:
                        heartbeat = json.dumps(
                            {
                                "type": "heartbeat",
                                "timestamp": state.service.status()["updated_at"],
                                "realtime": state.service.realtime.status(),
                                "clob_realtime": state.service.clob_realtime.status(),
                            },
                            ensure_ascii=False,
                        )
                        self.wfile.write(f"event: heartbeat\ndata: {heartbeat}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        continue
                    self.wfile.write(f"data: {message}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                state.service.unsubscribe(subscriber)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/api/refresh":
                payload = state.service.refresh()
                self._serve_json(payload)
                return
            self._serve_json({"error": "未找到请求的资源"}, HTTPStatus.NOT_FOUND)

    return Handler


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        # Browser refreshes and short-lived curl checks can close an SSE socket normally.
        exception = sys.exc_info()[1]
        if isinstance(exception, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def run_server() -> None:
    config = load_config()
    state = AppState(config)
    handler = create_handler(state)
    server = QuietThreadingHTTPServer((config.host, config.port), handler)
    try:
        print(f"BTC 实时信号系统已启动：http://{config.host}:{config.port}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.service.stop()
        server.server_close()


if __name__ == "__main__":
    run_server()
