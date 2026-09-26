import asyncio
import importlib.util
import json
from pathlib import Path
import time

import httpx
import pytest

from factory_monitor_desktop.resource_budget import MODEL_DIGEST, MODEL_NAME


SPEC = importlib.util.spec_from_file_location("prepare_8gb_model", Path(__file__).parents[1] / "scripts/prepare_8gb_model.py")
preparation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preparation)


def sample(**changes):
    return {"monotonic": time.monotonic(), "total_mib": 8192, "used_mib": 1024,
            "free_mib": 7168, "ram_available_mib": 8192, "gpu_count": 1,
            "error": None, "latched": False, **changes}


class Sampler:
    def __init__(self, value=None):
        self.value = value or sample()
        self.started = False
        self.closed = False
        self.auto_refresh = False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def snapshot(self):
        if self.auto_refresh:
            self.value["monotonic"] = time.monotonic()
        return dict(self.value)


def model(**changes):
    return {"name": MODEL_NAME, "model": MODEL_NAME, "digest": MODEL_DIGEST,
            "details": {"quantization_level": "Q4_K_M"}, "context_length": 4096,
            "size_vram": 3 * 1024**3, **changes}


def run(tmp_path, handler, *, sampler=None, timeout=120):
    sampler = sampler or Sampler()
    report = tmp_path / "prepare.json"
    calls = []

    async def execute():
        async def handle(request):
            calls.append((request.method, request.url.path))
            return await handler(request, sampler)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False) as client:
            code = await preparation.prepare_model(11435, report, sampler=sampler, client=client,
                                                   timeout_seconds=timeout)
        return code
    code = asyncio.run(execute())
    return code, json.loads(report.read_text()), calls, sampler


def test_no_nvidia_refuses_without_any_http(tmp_path):
    async def fail_http(*_):
        pytest.fail("no hardware must mean no HTTP")
    sampler = Sampler(sample(gpu_count=0, error="probe_failed"))
    code, report, calls, sampler = run(tmp_path, fail_http, sampler=sampler)
    assert code != 0 and report["reason"] == "sensor_unavailable"
    assert calls == [] and report["model_load_requested"] is False
    assert sampler.started and sampler.closed


def test_wrong_installed_digest_never_posts(tmp_path):
    async def handle(request, _sampler):
        assert request.method == "GET"
        return httpx.Response(200, json={"models": [] if request.url.path == "/api/ps" else [model(digest="wrong")]})
    code, report, calls, _ = run(tmp_path, handle)
    assert code == 1 and report["reason"] == "installed_model_digest_unverified"
    assert all(method == "GET" for method, _ in calls)
    assert report["model_load_requested"] is False


def test_correct_warm_model_is_only_verified(tmp_path):
    async def handle(request, _sampler):
        assert request.method == "GET" and request.url.path == "/api/ps"
        return httpx.Response(200, json={"models": [model()]})
    code, report, calls, _ = run(tmp_path, handle)
    assert code == 0 and report["gate"] == "PASS"
    assert report["path"] == "verify_existing_warm_model"
    assert report["model_load_requested"] is False
    assert report["admission"]["digest"] == MODEL_DIGEST
    assert all(method == "GET" for method, _ in calls)


def test_cold_load_uses_fixed_parameters_then_independent_readback(tmp_path):
    loaded = False

    async def handle(request, sampler):
        nonlocal loaded
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [model()]})
        if request.method == "POST":
            assert request.url.path == "/api/generate"
            assert json.loads(request.content) == {"model": MODEL_NAME, "prompt": "", "stream": False,
                                                    "options": {"num_ctx": 4096}, "keep_alive": -1}
            loaded = True
            sampler.value = sample(used_mib=4096, free_mib=4096)
            sampler.auto_refresh = True
            return httpx.Response(200, json={"done": True})
        return httpx.Response(200, json={"models": [model()] if loaded else []})

    code, report, calls, _ = run(tmp_path, handle)
    assert code == 0 and report["model_load_requested"] is True
    assert calls == [("GET", "/api/ps"), ("GET", "/api/tags"), ("GET", "/api/ps"),
                     ("POST", "/api/generate"), ("GET", "/api/ps")]
    assert report["admission"]["sample"]["used_mib"] == 4096
    assert report["field_gate"] == "NOT_TESTED"


def test_cold_load_timeout_reports_backend_unknown_without_retry(tmp_path):
    async def handle(request, _sampler):
        if request.method == "POST":
            await asyncio.sleep(.5)
            return httpx.Response(200, json={"done": True})
        return httpx.Response(200, json={"models": [model()] if request.url.path == "/api/tags" else []})
    code, report, calls, _ = run(tmp_path, handle, timeout=.05)
    assert code != 0 and report["reason"] == "preparation_timeout"
    assert report["backend_may_still_be_running"] is True and report["backend_stop_verified"] is False
    assert report["model_load_requested"] is True
    assert sum(method == "POST" for method, _ in calls) == 1
    assert not report["automatic_retry"] and not report["kill_requested"] and not report["unload_requested"]


def test_cold_load_requires_extra_free_vram_before_tags_or_post(tmp_path):
    async def handle(request, _sampler):
        assert request.method == "GET" and request.url.path == "/api/ps"
        return httpx.Response(200, json={"models": []})
    code, report, calls, _ = run(tmp_path, handle, sampler=Sampler(sample(used_mib=4096, free_mib=4096)))
    assert code == 1 and report["reason"] == "cold_load_vram_unavailable"
    assert calls == [("GET", "/api/ps")]


def test_new_resident_model_prevents_cold_load(tmp_path):
    ps_calls = 0

    async def handle(request, _sampler):
        nonlocal ps_calls
        assert request.method == "GET"
        if request.url.path == "/api/ps":
            ps_calls += 1
            return httpx.Response(200, json={"models": [] if ps_calls == 1 else [model()]})
        return httpx.Response(200, json={"models": [model()]})
    code, report, calls, _ = run(tmp_path, handle)
    assert code == 1 and report["reason"] == "cold_load_requires_no_resident_model"
    assert all(method == "GET" for method, _ in calls)


def test_final_bad_readback_never_becomes_success(tmp_path):
    loaded = False

    async def handle(request, sampler):
        nonlocal loaded
        if request.method == "POST":
            loaded = True
            sampler.value = sample(used_mib=4096, free_mib=4096)
            sampler.auto_refresh = True
            return httpx.Response(200, json={"done": True, "response": "private server content"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [model()]})
        return httpx.Response(200, json={"models": [model(digest="wrong")] if loaded else []})
    code, report, _, _ = run(tmp_path, handle)
    assert code == 1 and report["reason"] == "loaded_digest_mismatch"
    assert "private server content" not in json.dumps(report)


def test_existing_report_never_overwrites_or_starts_sampler(tmp_path):
    path = tmp_path / "prepare.json"
    path.write_text("prior evidence")
    sampler = Sampler()
    with pytest.raises(FileExistsError):
        asyncio.run(preparation.prepare_model(11435, path, sampler=sampler))
    assert path.read_text() == "prior evidence" and not sampler.started


def test_successful_load_without_new_sensor_sample_cannot_pass(tmp_path):
    async def handle(request, _sampler):
        if request.method == "POST":
            return httpx.Response(200, json={"done": True})
        return httpx.Response(200, json={"models": [model()] if request.url.path == "/api/tags" else []})
    code, report, calls, _ = run(tmp_path, handle, timeout=.05)
    assert code == 1 and report["reason"] == "preparation_timeout"
    assert report["model_load_requested"] is True
    assert "admission" not in report
    assert calls[-1] == ("POST", "/api/generate")


def test_existing_resource_log_is_preserved_without_http(tmp_path):
    log = tmp_path / "prepare.json.resources.ndjson"
    log.write_text("prior samples")

    async def fail_http(*_):
        pytest.fail("existing log must reject before HTTP")
    code, report, calls, sampler = run(tmp_path, fail_http)
    assert code == 1 and report["reason"] == "resource_log_exists"
    assert log.read_text() == "prior samples" and calls == [] and not sampler.started
