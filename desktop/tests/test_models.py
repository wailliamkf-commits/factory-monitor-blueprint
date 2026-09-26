import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from factory_monitor_desktop.models import ModelConfigurationError, ModelRouter, ModelRouterError


VALID_RESULT = {
    "decision": "supported",
    "reason": "person carries a cable bundle",
    "visible_evidence": ["a coiled cable is visible in both frames"],
    "target_visible": True,
    "target_type": "bundle",
}


class _Handler(BaseHTTPRequestHandler):
    response_body = {}
    status_code = 200
    delay = 0
    delay_before_headers = 0
    delay_after_headers = 0
    requests = []

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).requests.append((self.path, request))
        if type(self).delay:
            time.sleep(type(self).delay)
        body = json.dumps(type(self).response_body).encode()
        if type(self).delay_before_headers:
            time.sleep(type(self).delay_before_headers)
        self.send_response(type(self).status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if type(self).delay_after_headers:
            time.sleep(type(self).delay_after_headers)
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, *_args):
        pass


@pytest.fixture
def model_server():
    _Handler.response_body = {
        "message": {"content": json.dumps(VALID_RESULT)},
        "model": "qwen3-vl:2b",
        "done": True,
    }
    _Handler.status_code = 200
    _Handler.delay = 0
    _Handler.delay_before_headers = 0
    _Handler.delay_after_headers = 0
    _Handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def _config(endpoint, *, protocol="ollama", backup=None, deadline=1.0, inflight=1):
    providers = [
        {
            "name": "primary",
            "protocol": protocol,
            "endpoint": endpoint,
            "model": "vision-local",
            "max_images": 2,
            "timeout_seconds": 0.8,
        }
    ]
    if backup is not None:
        providers.append(
            {
                "name": "backup",
                "protocol": "ollama",
                "endpoint": backup,
                "model": "backup-local",
                "max_images": 2,
                "timeout_seconds": 0.8,
            }
        )
    return {"providers": providers, "deadline_seconds": deadline, "max_inflight": inflight}


def _payload(kind="general"):
    prompt = "Review only visible facts; never infer intent."
    if kind == "material_candidate":
        prompt += " supported requires a visible cable, wire, bundle or coil."
    return {
        "model": "ignored-by-router",
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["supported", "dismissed", "uncertain"]},
                "reason": {"type": "string", "maxLength": 180},
                "visible_evidence": {"type": "array", "maxItems": 3},
                "target_visible": {"type": "boolean"},
                "target_type": {"type": "string", "enum": ["cable", "wire", "bundle", "coil", "none", "unclear"]},
            },
        },
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(b"jpeg-one").decode(), base64.b64encode(b"jpeg-two").decode()]}],
        "options": {"temperature": 0},
    }


def test_ollama_protocol_keeps_images_and_returns_trace_metadata(model_server):
    response, trace = ModelRouter(_config(model_server)).chat(_payload("material_candidate"))

    assert response["message"]["content"] == json.dumps(VALID_RESULT, separators=(",", ":"))
    assert response["model"] == "vision-local"
    path, request = _Handler.requests[0]
    assert path == "/api/chat"
    assert request["model"] == "vision-local"
    assert request["messages"][0]["images"] == _payload()["messages"][0]["images"]
    assert trace["attempts"] == 1
    assert trace["model"] == "vision-local"
    assert not any(key in trace for key in ("prompt", "images", "image_bytes"))


def test_openai_compatible_protocol_maps_images_and_structured_output(model_server):
    _Handler.response_body = {"choices": [{"message": {"content": json.dumps(VALID_RESULT)}}]}
    response, trace = ModelRouter(_config(model_server, protocol="openai")).chat(_payload("material_candidate"))

    request_path, request = _Handler.requests[0]
    assert request_path == "/v1/chat/completions"
    assert request["model"] == "vision-local"
    assert request["messages"][0]["content"][1]["type"] == "image_url"
    assert request["response_format"]["type"] == "json_schema"
    assert response["message"]["content"] == json.dumps(VALID_RESULT, separators=(",", ":"))
    assert trace["provider"] == "primary"


def test_router_rejects_public_or_invalid_provider_endpoint():
    with pytest.raises(ModelConfigurationError, match="loopback"):
        ModelRouter(_config("https://example.com"))
    with pytest.raises(ModelConfigurationError, match="loopback"):
        ModelRouter(_config("http://127.0.0.1.attacker.test:11434"))


def test_router_refuses_redirect_instead_of_following_it(model_server):
    _Handler.status_code = 302
    _Handler.response_body = {}
    router = ModelRouter(_config(model_server))

    with pytest.raises(ModelRouterError, match="failed"):
        router.chat(_payload())


def test_invalid_model_json_keeps_safe_failure_trace_only(model_server):
    _Handler.response_body = {"message": {"content": "not json"}}
    router = ModelRouter(_config(model_server))

    with pytest.raises(ModelRouterError, match="failed"):
        router.chat(_payload())

    assert router.last_trace["status"] == "error"
    assert router.last_trace["attempt_log"][0]["failure_reason"] == "local model returned invalid JSON"
    assert not any(key in json.dumps(router.last_trace) for key in ("Review only visible", "jpeg-one"))


def test_provider_429_does_not_trigger_fallback(model_server):
    _Handler.status_code = 429
    _Handler.response_body = {}
    router = ModelRouter(_config(model_server, backup=model_server))

    with pytest.raises(ModelRouterError) as caught:
        router.chat(_payload())

    assert caught.value.status_code == 429
    assert caught.value.trace["attempts"] == 1
    assert caught.value.trace["status"] == "busy"
    assert len(_Handler.requests) == 1


@pytest.mark.parametrize(
    "result",
    [
        {"decision": "supported", "reason": "yes", "visible_evidence": []},
        {"decision": "mystery", "reason": "x", "visible_evidence": ["fact"]},
        {"decision": "supported", "reason": "yes", "visible_evidence": ["bag"], "target_visible": False, "target_type": "none"},
    ],
)
def test_router_rejects_bad_json_schema_and_material_claim(model_server, result):
    _Handler.response_body = {"message": {"content": json.dumps(result)}}
    router = ModelRouter(_config(model_server))

    with pytest.raises(ModelRouterError, match="failed"):
        router.chat(_payload("material_candidate"))


def test_router_tries_at_most_one_fallback(model_server):
    def make_handler(status, content):
        class Handler(_Handler):
            status_code = status
            response_body = content
            requests = []
        return Handler

    first = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(503, {}))
    first_thread = threading.Thread(target=first.serve_forever, daemon=True)
    first_thread.start()
    second_handler = make_handler(200, {"message": {"content": json.dumps(VALID_RESULT)}})
    second = ThreadingHTTPServer(("127.0.0.1", 0), second_handler)
    second_thread = threading.Thread(target=second.serve_forever, daemon=True)
    second_thread.start()
    try:
        config = _config(f"http://127.0.0.1:{first.server_port}", backup=f"http://127.0.0.1:{second.server_port}")
        response, trace = ModelRouter(config).chat(_payload())
        assert response["model"] == "backup-local"
        assert trace["attempts"] == 2
        assert len(first.RequestHandlerClass.requests) == 1
        assert len(second_handler.requests) == 1
    finally:
        first.shutdown()
        second.shutdown()
        first_thread.join()
        second_thread.join()


def test_total_deadline_is_bounded_and_never_exceeds_core_budget(model_server):
    _Handler.delay = 0.3
    router = ModelRouter(_config(model_server, deadline=0.1))
    started = time.monotonic()

    with pytest.raises(ModelRouterError, match="deadline|timed out"):
        router.chat(_payload())
    assert time.monotonic() - started < 0.25


def test_total_deadline_cancels_blocked_body_and_marks_backend_uncertain(model_server):
    class FallbackHandler(_Handler):
        response_body = {"message": {"content": json.dumps(VALID_RESULT)}}
        status_code = 200
        delay = 0
        delay_before_headers = 0
        delay_after_headers = 0
        requests = []

    fallback = ThreadingHTTPServer(("127.0.0.1", 0), FallbackHandler)
    fallback_thread = threading.Thread(target=fallback.serve_forever, daemon=True)
    fallback_thread.start()
    _Handler.delay_before_headers = 0.38
    _Handler.delay_after_headers = 0.38
    router = ModelRouter(_config(model_server, backup=f"http://127.0.0.1:{fallback.server_port}", deadline=0.5))
    started = time.monotonic()

    try:
        with pytest.raises(ModelRouterError) as caught:
            router.chat(_payload())

        elapsed = time.monotonic() - started
        assert elapsed < 0.65
        assert caught.value.status_code == 504
        assert caught.value.trace["status"] == "uncertain"
        assert caught.value.trace["backend_state"] == "uncertain"
        assert FallbackHandler.requests == []
        assert router._inflight.acquire(blocking=False)
        router._inflight.release()

        with pytest.raises(ModelRouterError) as unavailable:
            router.chat(_payload())
        assert unavailable.value.status_code == 503
        assert unavailable.value.trace["status"] == "uncertain"
        assert unavailable.value.trace["backend_state"] == "uncertain"
        assert FallbackHandler.requests == []
    finally:
        fallback.shutdown()
        fallback.server_close()
        fallback_thread.join()


def test_router_rejects_unbounded_or_core_length_deadline(model_server):
    with pytest.raises(ModelConfigurationError, match="deadline"):
        ModelRouter(_config(model_server, deadline=15))
    with pytest.raises(ModelConfigurationError, match="deadline"):
        ModelRouter(_config(model_server, deadline=0))


def test_router_returns_429_backpressure_without_fallback(model_server):
    router = ModelRouter(_config(model_server, inflight=1))
    acquired = router._inflight.acquire(blocking=False)
    assert acquired
    try:
        with pytest.raises(ModelRouterError, match="busy|429"):
            router.chat(_payload())
    finally:
        router._inflight.release()


def test_router_rejects_too_many_or_oversized_images(model_server):
    payload = _payload()
    payload["messages"][0]["images"].append(base64.b64encode(b"third").decode())
    with pytest.raises(ModelRouterError, match="images"):
        ModelRouter(_config(model_server)).chat(payload)

    payload = _payload()
    payload["messages"][0]["images"] = [base64.b64encode(b"x" * 5_000_000).decode()]
    with pytest.raises(ModelRouterError, match="image|payload"):
        ModelRouter(_config(model_server)).chat(payload)
