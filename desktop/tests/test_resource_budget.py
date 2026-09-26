import asyncio
import base64
import copy
import io
import json
import threading
import time

import httpx
from PIL import Image
import pytest

from factory_monitor.config import default_config
from factory_monitor_desktop.resource_budget import (
    BudgetRefused, MODEL_DIGEST, MODEL_NAME, ResourceSampler, Vram8gbPolicy, apply_runtime_budget,
)


def sample(**changes):
    return {"monotonic": time.monotonic(), "total_mib": 8192, "used_mib": 4096,
            "free_mib": 4096, "ram_available_mib": 8192, "gpu_count": 1,
            "error": None, "latched": False, **changes}


class Sampler:
    def __init__(self, value=None):
        self.value = value or sample()

    def snapshot(self):
        return copy.deepcopy(self.value)


def loaded(**changes):
    return {"name": MODEL_NAME, "model": MODEL_NAME, "digest": MODEL_DIGEST,
            "details": {"quantization_level": "Q4_K_M"}, "context_length": 4096,
            "size_vram": 3 * 1024**3, **changes}


def image(width, height, color):
    stream = io.BytesIO()
    Image.new("RGB", (width, height), color).save(stream, "PNG")
    return base64.b64encode(stream.getvalue()).decode()


def admit(report=None, models=None, response=None):
    requests = []

    async def run():
        def handle(request):
            requests.append(request)
            return response if response is not None else httpx.Response(200, json={"models": models if models is not None else [loaded()]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False) as client:
            result = await Vram8gbPolicy(Sampler(report)).admit(client, "http://127.0.0.1:11435", MODEL_NAME)
            return result
    return asyncio.run(run()), requests


def test_runtime_budget_is_private_and_preserves_evidence_roi_thresholds():
    original = default_config()
    before = copy.deepcopy(original)
    changed = apply_runtime_budget(original)
    assert original == before
    expected = copy.deepcopy(before)
    expected["detection"].update(device="cpu", fps=2)
    assert changed == expected
    assert changed["evidence"]["pre_seconds"] == 30
    assert changed["evidence"]["post_seconds"] == 60
    original["detection"]["fps"] = 1
    assert apply_runtime_budget(original)["detection"]["fps"] == 1


def test_six_images_preserve_order_text_timestamps_and_do_not_enlarge():
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)] * 2
    messages = [{"role": "user", "content": "timestamps: 1,2,3,4,5,6", "timestamp": 9,
                 "images": [image(896, 448, color) for color in colors[:3]]},
                {"role": "user", "content": "second sequence", "images": [image(32, 16, color) for color in colors[3:]]}]
    original = copy.deepcopy(messages)
    output, metadata = Vram8gbPolicy(Sampler()).prepare_images(messages)
    assert messages == original
    assert output[0]["content"] == messages[0]["content"] and output[0]["timestamp"] == 9
    assert output[1]["content"] == messages[1]["content"]
    images = [encoded for message in output for encoded in message["images"]]
    assert len(images) == metadata["image_count"] == 6
    for index, encoded in enumerate(images):
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as decoded:
            assert decoded.format == "JPEG"
            assert decoded.size == ((448, 224) if index < 3 else (32, 16))
            assert all(abs(a-b) < 4 for a, b in zip(decoded.getpixel((0, 0)), colors[index]))
    assert metadata["images"][0]["original_size"] == [896, 448]
    assert metadata["images"][-1]["prepared_size"] == [32, 16]


@pytest.mark.parametrize("encoded", ["not base64!", base64.b64encode(b"broken png").decode()])
def test_corrupt_images_are_refused(encoded):
    with pytest.raises(BudgetRefused, match="invalid_image"):
        Vram8gbPolicy(Sampler()).prepare_images([{"images": [encoded]}])


def test_image_pixel_budget_checks_header_before_decoding(monkeypatch):
    encoded = image(3000, 3000, "red")
    monkeypatch.setattr(Image.Image, "load", lambda *_: pytest.fail("oversize image must not be decoded"))
    with pytest.raises(BudgetRefused, match="image_pixels_exceeded"):
        Vram8gbPolicy(Sampler()).prepare_images([{"images": [encoded]}])


def test_total_pixel_budget_refuses_six_large_frames():
    encoded = image(2500, 2000, "red")
    with pytest.raises(BudgetRefused, match="total_image_pixels_exceeded"):
        Vram8gbPolicy(Sampler()).prepare_images([{"images": [encoded] * 6}])


@pytest.mark.parametrize("change,reason", [
    ({"monotonic": time.monotonic() - 20}, "sensor_stale"),
    ({"gpu_count": 2}, "single_gpu_required"),
    ({"free_mib": 3071}, "vram_budget_unavailable"),
    ({"used_mib": 5121}, "vram_budget_unavailable"),
    ({"ram_available_mib": 4095}, "ram_budget_unavailable"),
    ({"latched": True}, "resource_pressure_latched"),
    ({"error": "probe_failed"}, "sensor_unavailable"),
])
def test_resource_rejection_happens_before_any_http(change, reason):
    async def run():
        def unexpected(_):
            pytest.fail("resource rejection must precede model HTTP")
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
            await Vram8gbPolicy(Sampler(sample(**change))).admit(client, "http://127.0.0.1", MODEL_NAME)
    with pytest.raises(BudgetRefused, match=reason):
        asyncio.run(run())


def test_valid_single_warm_pinned_model_is_admitted():
    result, requests = admit()
    assert result["digest"] == MODEL_DIGEST
    assert result["size_vram_mib"] == 3072
    assert len(requests) == 1 and requests[0].method == "GET"
    assert requests[0].url.path == "/api/ps"


@pytest.mark.parametrize("models,reason", [
    ([], "single_warm_model_required"),
    ([loaded(), loaded()], "single_warm_model_required"),
    ([loaded(digest="wrong")], "loaded_digest_mismatch"),
    ([loaded(model="different")], "loaded_model_mismatch"),
    ([loaded(details={"quantization_level": "Q8_0"})], "loaded_quantization_mismatch"),
    ([loaded(context_length=8192)], "loaded_context_unavailable"),
    ([loaded(size_vram=0)], "loaded_vram_unavailable"),
    ([loaded(size_vram=4096 * 1024**2 + 1)], "loaded_vram_unavailable"),
])
def test_cold_or_unapproved_model_is_refused(models, reason):
    with pytest.raises(BudgetRefused, match=reason):
        admit(models=models)


def test_model_status_response_has_a_byte_limit():
    with pytest.raises(BudgetRefused, match="model_status_too_large"):
        admit(response=httpx.Response(200, content=b" " * (64 * 1024 + 1)))


def test_sampler_is_background_and_pressure_latches_across_recovery(tmp_path):
    entered = threading.Event()
    proceed = threading.Event()
    samples = [sample(used_mib=6500, free_mib=1692), sample()]

    def probe():
        entered.set()
        proceed.wait(1)
        return samples.pop(0) if samples else sample()

    sampler = ResourceSampler(tmp_path / "resources.ndjson", probe=probe, interval=.01)
    started = time.monotonic()
    sampler.start()
    try:
        assert time.monotonic() - started < .2
        assert entered.wait(.5)
        assert sampler.snapshot()["error"] == "not_sampled"
        proceed.set()
        until = time.monotonic() + 2
        while time.monotonic() < until:
            current = sampler.snapshot()
            if current["used_mib"] == 4096 and current["latched"]:
                break
            time.sleep(.01)
        assert current["latched"] and current["used_mib"] == 4096
    finally:
        proceed.set()
        sampler.close()
    records = [json.loads(line) for line in (tmp_path / "resources.ndjson").read_text().splitlines()]
    assert any(row["latched"] for row in records)
    assert set(records[0]) == set(sample())
    assert ResourceSampler(probe=lambda: sample()).snapshot()["latched"] is False


def test_sampler_probe_failure_is_safe_and_log_is_bounded(tmp_path, monkeypatch):
    import factory_monitor_desktop.resource_budget as module
    monkeypatch.setattr(module, "MAX_LOG_BYTES", 900)
    path = tmp_path / "resources.ndjson"
    sampler = ResourceSampler(path, probe=lambda: (_ for _ in ()).throw(RuntimeError("secret command data")))
    row = sampler._sample()
    assert row["error"] == "probe_failed"
    assert "secret" not in json.dumps(row)
    for _ in range(30):
        sampler._write_log(row)
    assert path.stat().st_size <= 900
    assert all(json.loads(line)["error"] == "probe_failed" for line in path.read_text().splitlines())


def test_sample_is_rechecked_after_loaded_model_probe():
    sampler = Sampler()

    async def run():
        def handle(_):
            sampler.value["latched"] = True
            return httpx.Response(200, json={"models": [loaded()]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await Vram8gbPolicy(sampler).admit(client, "http://127.0.0.1", MODEL_NAME)
    with pytest.raises(BudgetRefused, match="resource_pressure_latched"):
        asyncio.run(run())


def test_default_probe_has_timeout_and_refuses_multiple_gpus(monkeypatch):
    from types import SimpleNamespace
    import factory_monitor_desktop.resource_budget as module
    calls = []

    def command(arguments, **options):
        calls.append((arguments, options))
        return SimpleNamespace(stdout="8192, 4096, 4096\n")

    monkeypatch.setattr(module.subprocess, "run", command)
    monkeypatch.setattr(module.psutil, "virtual_memory", lambda: SimpleNamespace(available=8 * 1024**3))
    current = ResourceSampler()._sample()
    assert current["gpu_count"] == 1 and current["error"] is None
    assert current["used_mib"] == 4096 and current["ram_available_mib"] == 8192
    assert calls[0][1]["timeout"] == 2
    assert calls[0][0] == ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"]
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs:
                        SimpleNamespace(stdout="8192, 4096, 4096\n24576, 1000, 23576\n"))
    multiple = ResourceSampler()._sample()
    assert multiple["gpu_count"] == 2 and multiple["error"] == "single_gpu_required"
    assert multiple["total_mib"] is None
