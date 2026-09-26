import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import factory_monitor_desktop.gateway as gateway_module
from factory_monitor_desktop.gateway import Gateway
from factory_monitor_desktop.models import ModelRouter


_RESULT = {"decision": "uncertain", "reason": "image is unclear", "visible_evidence": ["the region is blurry"]}


class _ProviderHandler(BaseHTTPRequestHandler):
    entered = threading.Event()
    release = threading.Event()
    delay_for_release = False

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        assert self.path == "/api/chat"
        assert body["model"] == "local-vlm"
        if type(self).delay_for_release:
            type(self).entered.set()
            type(self).release.wait(1)
        encoded = json.dumps({"message": {"content": json.dumps(_RESULT)}, "done": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except BrokenPipeError:
            pass

    def log_message(self, *_args):
        pass


@pytest.fixture
def provider_server():
    _ProviderHandler.entered.clear()
    _ProviderHandler.release.clear()
    _ProviderHandler.delay_for_release = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        _ProviderHandler.release.set()
        server.shutdown()
        thread.join()


def _router(endpoint, *, deadline=1.0, max_inflight=1):
    return ModelRouter(
        {
            "deadline_seconds": deadline,
            "max_inflight": max_inflight,
            "providers": [
                {
                    "name": "local",
                    "protocol": "ollama",
                    "endpoint": endpoint,
                    "model": "local-vlm",
                    "max_images": 2,
                    "timeout_seconds": 0.8,
                }
            ],
        }
    )


@pytest.fixture
def gateway(provider_server):
    instance = Gateway(_router(provider_server), host="127.0.0.1", port=0)
    instance.start()
    try:
        yield instance
    finally:
        instance.close()


def _payload(image=b"jpeg"):
    return {
        "model": "ignored",
        "stream": False,
        "format": "json",
        "messages": [{"role": "user", "content": "Summarize visible evidence only", "images": [base64.b64encode(image).decode()]}],
    }


def test_gateway_exposes_loopback_ollama_chat_and_keeps_trace_metadata_only(gateway):
    with httpx.Client(trust_env=False) as client:
        response = client.post(f"{gateway.endpoint}/api/chat", json=_payload(), timeout=2)

    assert response.status_code == 200
    assert response.json()["message"]["content"] == json.dumps(_RESULT, separators=(",", ":"))
    assert gateway.last_trace["model"] == "local-vlm"
    assert gateway.last_trace["status"] == "completed"
    assert "Summarize visible evidence" not in json.dumps(gateway.last_trace)
    assert base64.b64encode(b"jpeg").decode() not in json.dumps(gateway.last_trace)


def test_gateway_rejects_oversized_body_before_model_call(provider_server):
    instance = Gateway(_router(provider_server), host="127.0.0.1", port=0, max_body_bytes=1024)
    instance.start()
    try:
        with httpx.Client(trust_env=False) as client:
            response = client.post(f"{instance.endpoint}/api/chat", json=_payload(b"x" * 2000), timeout=2)
        assert response.status_code == 413
        assert instance.last_trace is None
    finally:
        instance.close()


def test_gateway_rejects_malformed_request_and_unknown_path(gateway):
    with httpx.Client(trust_env=False) as client:
        malformed = client.post(f"{gateway.endpoint}/api/chat", content=b"{bad", headers={"Content-Type": "application/json"}, timeout=2)
        missing = client.post(f"{gateway.endpoint}/v1/chat/completions", json=_payload(), timeout=2)

    assert malformed.status_code == 400
    assert missing.status_code == 404


def test_gateway_absolute_body_receive_deadline_releases_request_slot(provider_server, monkeypatch):
    monkeypatch.setattr(gateway_module, "REQUEST_RECEIVE_DEADLINE_SECONDS", 0.3)
    instance = Gateway(_router(provider_server), host="127.0.0.1", port=0)
    instance.start()
    port = int(instance.endpoint.rsplit(":", 1)[1])
    stalled = socket.create_connection(("127.0.0.1", port), timeout=1)
    started = time.monotonic()
    try:
        stalled.sendall(
            b"POST /api/chat HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 10\r\n"
            b"Connection: close\r\n\r\n{"
        )
        saturated = httpx.post(f"{instance.endpoint}/api/chat", json=_payload(), timeout=1, trust_env=False)
        assert saturated.status_code == 429

        time.sleep(0.1)
        try:
            stalled.sendall(b'"')
        except OSError:
            pass
        time.sleep(0.1)
        try:
            stalled.sendall(b"1")
        except OSError:
            pass
        stalled.settimeout(1)
        assert stalled.recv(1) == b""
        assert time.monotonic() - started < 0.55

        deadline = time.monotonic() + 1
        while True:
            response = httpx.post(f"{instance.endpoint}/api/chat", json=_payload(), timeout=1, trust_env=False)
            if response.status_code != 429 or time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert response.status_code == 200
    finally:
        stalled.close()
        instance.close()


def test_gateway_caps_total_concurrent_inference_and_returns_429(provider_server):
    _ProviderHandler.delay_for_release = True
    instance = Gateway(_router(provider_server, max_inflight=1), host="127.0.0.1", port=0)
    instance.start()
    first_result = []
    first_thread = threading.Thread(
        target=lambda: first_result.append(
            httpx.post(f"{instance.endpoint}/api/chat", json=_payload(), timeout=2, trust_env=False)
        ),
        daemon=True,
    )
    first_thread.start()
    try:
        assert _ProviderHandler.entered.wait(1)
        saturated = httpx.post(f"{instance.endpoint}/api/chat", json=_payload(), timeout=1, trust_env=False)
        assert saturated.status_code == 429
    finally:
        _ProviderHandler.release.set()
        first_thread.join(2)
        instance.close()
    assert first_result[0].status_code == 200


def test_gateway_surfaces_uncertain_provider_state_after_total_timeout(provider_server):
    _ProviderHandler.delay_for_release = True
    instance = Gateway(_router(provider_server, deadline=0.2), host="127.0.0.1", port=0)
    instance.start()
    try:
        response = httpx.post(f"{instance.endpoint}/api/chat", json=_payload(), timeout=1, trust_env=False)
        assert response.status_code == 504
        assert instance.last_trace["status"] == "uncertain"
        assert instance.last_trace["backend_state"] == "uncertain"

        unavailable = httpx.post(f"{instance.endpoint}/api/chat", json=_payload(), timeout=1, trust_env=False)
        assert unavailable.status_code == 503
        assert instance.last_trace["backend_state"] == "uncertain"
    finally:
        _ProviderHandler.release.set()
        instance.close()


def test_gateway_rejects_public_bind_host(provider_server):
    with pytest.raises(ValueError, match="loopback"):
        Gateway(_router(provider_server), host="0.0.0.0", port=0)


def test_gateway_close_is_safe_before_and_after_start(provider_server):
    instance = Gateway(_router(provider_server), host="127.0.0.1", port=0)
    instance.close()
    endpoint = instance.start()
    assert endpoint.startswith("http://127.0.0.1:")
    instance.close()
