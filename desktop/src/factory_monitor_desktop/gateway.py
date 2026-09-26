"""A loopback-only Ollama-compatible HTTP adapter for desktop model routing."""

from __future__ import annotations

import ipaddress
import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .models import ModelBusyError, ModelRouterError


REQUEST_RECEIVE_DEADLINE_SECONDS = 2.0


class _DeadlineReader:
    """Socket reader with one deadline shared by the request line, headers, and body."""

    def __init__(self, connection: socket.socket, deadline: float) -> None:
        self._connection = connection
        self._deadline = deadline
        self._buffer = bytearray()
        self._eof = False
        self.closed = False

    def _fill(self) -> None:
        if self._eof:
            return
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("gateway request receive deadline exceeded")
        readable, _, _ = select.select([self._connection], [], [], remaining)
        if not readable:
            raise TimeoutError("gateway request receive deadline exceeded")
        chunk = self._connection.recv(8192)
        if not chunk:
            self._eof = True
        else:
            self._buffer.extend(chunk)

    def _take(self, count: int) -> bytes:
        result = bytes(self._buffer[:count])
        del self._buffer[:count]
        return result

    def readline(self, size: int = -1) -> bytes:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0 and (size < 0 or newline + 1 <= size):
                return self._take(newline + 1)
            if size >= 0 and len(self._buffer) >= size:
                return self._take(size)
            if self._eof:
                return self._take(len(self._buffer) if size < 0 else min(size, len(self._buffer)))
            self._fill()

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            while not self._eof:
                self._fill()
            size = len(self._buffer)
        while len(self._buffer) < size and not self._eof:
            self._fill()
        return self._take(min(size, len(self._buffer)))

    def close(self) -> None:
        self.closed = True


class _BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], *, max_active: int) -> None:
        self._request_slots = threading.BoundedSemaphore(max_active)
        self._reject_slots = threading.BoundedSemaphore(4)
        super().__init__(address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            # A separate bounded pool drains rejected uploads. Closing a socket
            # with unread data can reset TCP and discard the 429 on Windows.
            if not self._reject_slots.acquire(blocking=False):
                request.close()  # Extreme transport overload, not an inference slot.
                return
            try:
                threading.Thread(target=self._reject_request, args=(request,),
                                 name='gateway-busy-drain', daemon=True).start()
            except BaseException:
                self._reject_slots.release()
                request.close()
                raise
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def _reject_request(self, request: socket.socket) -> None:
        deadline = time.monotonic() + 2.0
        limit = getattr(getattr(self, 'owner', None), 'max_body_bytes', 24 * 1024**2) + 65536
        try:
            request.settimeout(max(.001, deadline - time.monotonic()))
            body = b'{"error":"local model gateway busy"}'
            request.sendall(
                b'HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\n'
                + f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode('ascii') + body)
            request.shutdown(socket.SHUT_WR)
            received = 0
            while received < limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                request.settimeout(remaining)
                block = request.recv(min(65536, limit - received))
                if not block:
                    break
                received += len(block)
        except OSError:
            pass
        finally:
            try:
                request.close()
            finally:
                self._reject_slots.release()

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def _loopback_bind(host: str) -> tuple[str, int]:
    if not isinstance(host, str) or not host:
        raise ValueError("gateway bind host must be loopback")
    normalized = "127.0.0.1" if host.lower() == "localhost" else host
    try:
        if not ipaddress.ip_address(normalized).is_loopback:
            raise ValueError("gateway bind host must be loopback")
    except ValueError as exc:
        if str(exc) == "gateway bind host must be loopback":
            raise
        raise ValueError("gateway bind host must be loopback") from exc
    return normalized, socket.AF_INET6 if normalized == "::1" else socket.AF_INET


class Gateway:
    """Expose ModelRouter over a bounded local `/api/chat` endpoint."""

    def __init__(
        self,
        router: Any,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        max_body_bytes: int = 24 * 1024 * 1024,
    ) -> None:
        normalized, family = _loopback_bind(host)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("gateway port must be an integer from 0 to 65535")
        if isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int) or not 1024 <= max_body_bytes <= 64 * 1024 * 1024:
            raise ValueError("max_body_bytes must be between 1 KiB and 64 MiB")
        self.router = router
        self.host = normalized
        self.port = port
        self.max_body_bytes = max_body_bytes
        self.request_timeout_seconds = REQUEST_RECEIVE_DEADLINE_SECONDS
        self._server: _BoundedServer | None = None
        self._thread: threading.Thread | None = None
        self._trace_lock = threading.Lock()
        self.last_trace: dict[str, Any] | None = None
        max_active = getattr(router, "max_inflight", 1)
        if isinstance(max_active, bool) or not isinstance(max_active, int) or max_active != 1:
            raise ValueError("gateway concurrency is fixed at 1 until separately benchmarked")

        class LocalServer(_BoundedServer):
            address_family = family

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                self.connection = self.request
                deadline = time.monotonic() + self.server.owner.request_timeout_seconds
                self.rfile = _DeadlineReader(self.connection, deadline)
                self.wfile = self.connection.makefile("wb", self.wbufsize)

            def handle_one_request(self) -> None:
                try:
                    self.raw_requestline = self.rfile.readline(65537)
                    if len(self.raw_requestline) > 65536:
                        self.requestline = ""
                        self.request_version = ""
                        self.command = ""
                        self.send_error(414)
                        return
                    if not self.raw_requestline:
                        self.close_connection = True
                        return
                    if not self.parse_request():
                        return
                    method = getattr(self, "do_" + self.command, None)
                    if method is None:
                        self.send_error(501, "Unsupported method (%r)" % self.command)
                        return
                    method()
                    self.wfile.flush()
                except TimeoutError:
                    self.close_connection = True

            def do_POST(self) -> None:
                if self.path != "/api/chat":
                    self._send_json(404, {"error": "not found"})
                    return
                if self.headers.get("Transfer-Encoding"):
                    self._send_json(400, {"error": "content length is required"})
                    self.close_connection = True
                    return
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    length = -1
                if length < 0:
                    self._send_json(411, {"error": "content length is required"})
                    self.close_connection = True
                    return
                if length > self.server.owner.max_body_bytes:
                    self._send_json(413, {"error": "request body is too large"})
                    self.close_connection = True
                    return
                if self.headers.get_content_type() != "application/json":
                    self._send_json(415, {"error": "application/json is required"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send_json(400, {"error": "request JSON is invalid"})
                    return
                try:
                    response, trace = self.server.owner.router.chat(payload)
                    self.server.owner._set_trace(trace)
                    self._send_json(200, response)
                except ModelRouterError as exc:
                    trace = exc.trace or {
                        "provider": None,
                        "model": None,
                        "status": "busy" if exc.status_code == 429 else "error",
                        "duration_ms": 0,
                        "attempts": 0,
                    }
                    self.server.owner._set_trace(trace)
                    status = exc.status_code if exc.status_code in {400, 413, 429, 502, 503, 504} else 502
                    message = "local model gateway busy" if status == 429 else "local model review could not be completed"
                    self._send_json(status, {"error": message})
                except Exception:
                    self._set_failure_trace()
                    self._send_json(502, {"error": "local model review could not be completed"})

            def do_GET(self) -> None:
                self._send_json(404, {"error": "not found"})

            def _set_failure_trace(self) -> None:
                self.server.owner._set_trace(
                    {"provider": None, "model": None, "status": "error", "duration_ms": 0, "attempts": 0}
                )

            def _send_json(self, status: int, value: dict[str, Any]) -> None:
                body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *_args: Any) -> None:
                return

        self._handler = Handler
        self._server_class = LocalServer

    @property
    def endpoint(self) -> str:
        server = self._server
        if server is None:
            raise RuntimeError("gateway has not been started")
        host = server.server_address[0]
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{server.server_address[1]}"

    def start(self) -> str:
        if self._server is not None:
            return self.endpoint
        server = self._server_class((self.host, self.port), self._handler, max_active=1)
        server.owner = self
        thread = threading.Thread(target=server.serve_forever, name="factory-monitor-review-gateway", daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        return self.endpoint

    def close(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)

    def _set_trace(self, trace: dict[str, Any]) -> None:
        safe = {
            key: trace[key]
            for key in ("provider", "model", "status", "backend_state", "duration_ms", "attempts", "attempt_log",
                        "resource_profile", "resource_reason", "resource_admission", "image_preparation")
            if key in trace
        }
        with self._trace_lock:
            self.last_trace = safe
