"""Conservative admission controls; these are not an 8 GiB capacity guarantee."""

from __future__ import annotations

import base64
import binascii
import copy
import io
import json
import math
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse
import warnings

import httpx
from PIL import Image
import psutil


MODEL_NAME = "qwen3-vl:2b-instruct"
MODEL_DIGEST = "ea422f1e73652a95479954d8572d3c8c6022f628ce2d38a1a04aae1b7f2d5300"
MAX_SAMPLE_AGE_SECONDS = 10.0
MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 8_000_000
MAX_TOTAL_PIXELS = 24_000_000
MAX_STATUS_BYTES = 64 * 1024
_MIB = 1024 * 1024


class BudgetRefused(RuntimeError):
    """A safe reason code for an unknown/resource-blocked review, never a verdict."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def apply_runtime_budget(config: dict[str, Any]) -> dict[str, Any]:
    """Change only the detector device/rate in a private runtime configuration."""
    copied = copy.deepcopy(config)
    fps = copied["detection"]["fps"]
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError("detection fps must be a positive finite number")
    copied["detection"]["device"] = "cpu"
    copied["detection"]["fps"] = min(fps, 2)
    return copied


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _probe_resources() -> dict[str, Any]:
    """Read one physical NVIDIA adapter; never select a convenient larger GPU."""
    ram_available = psutil.virtual_memory().available / _MIB
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True, timeout=2,
    )
    rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    report: dict[str, Any] = {"gpu_count": len(rows), "ram_available_mib": ram_available}
    if len(rows) != 1:
        report["error"] = "single_gpu_required"
        return report
    values = rows[0].split(",")
    if len(values) != 3:
        raise ValueError("invalid gpu sample")
    report.update(zip(("total_mib", "used_mib", "free_mib"), (float(value.strip()) for value in values)))
    return report


class ResourceSampler:
    """Cache bounded read-only probes off the UI thread, retaining a pressure latch."""

    def __init__(self, log_path: str | Path | None = None,
                 probe: Callable[[], dict[str, Any]] | None = None, interval: float = 2) -> None:
        if not _number(interval) or interval <= 0:
            raise ValueError("sampling interval must be positive and finite")
        self.log_path = Path(log_path) if log_path is not None else None
        self.probe = probe or _probe_resources
        self.interval = float(interval)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._latched = False
        self._snapshot: dict[str, Any] = self._empty("not_sampled")

    @staticmethod
    def _empty(error: str) -> dict[str, Any]:
        return {"monotonic": None, "total_mib": None, "used_mib": None, "free_mib": None,
                "ram_available_mib": None, "gpu_count": 0, "error": error, "latched": False}

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("resource sampler is closed")
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="desktop-resource-sampler", daemon=True)
            self._thread.start()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stop.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)

    def _sample(self) -> dict[str, Any]:
        sample = self._empty("probe_failed")
        try:
            observed = self.probe()
            if not isinstance(observed, dict):
                raise ValueError("invalid resource sample")
            for key in ("total_mib", "used_mib", "free_mib", "ram_available_mib"):
                if _number(observed.get(key)):
                    sample[key] = observed[key]
            if type(observed.get("gpu_count")) is int and observed["gpu_count"] >= 0:
                sample["gpu_count"] = observed["gpu_count"]
            if sample["used_mib"] is not None and sample["used_mib"] > 6144:
                self._latched = True
            valid = (sample["gpu_count"] == 1
                     and all(sample[key] is not None for key in
                             ("total_mib", "used_mib", "free_mib", "ram_available_mib"))
                     and sample["total_mib"] > 0
                     and sample["used_mib"] <= sample["total_mib"]
                     and sample["free_mib"] <= sample["total_mib"]
                     and sample["used_mib"] + sample["free_mib"] <= sample["total_mib"] + 2)
            sample["error"] = (None if valid and not observed.get("error") else
                               "single_gpu_required" if sample["gpu_count"] != 1 else "invalid_sample")
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
            pass
        except Exception:
            # Probe implementations must never leak local command output into logs.
            pass
        sample["monotonic"] = time.monotonic()
        sample["latched"] = self._latched
        return sample

    def _write_log(self, sample: dict[str, Any]) -> None:
        if self.log_path is None:
            return
        line = (json.dumps(sample, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Retain the newest complete NDJSON records; a single writer owns this log.
        if self.log_path.exists() and self.log_path.stat().st_size + len(line) > MAX_LOG_BYTES:
            with self.log_path.open("rb") as stream:
                size = self.log_path.stat().st_size
                stream.seek(max(0, size - (MAX_LOG_BYTES - len(line))))
                if stream.tell():
                    stream.readline()
                tail = stream.read(MAX_LOG_BYTES - len(line))
            with self.log_path.open("wb") as stream:
                stream.write(tail)
                stream.write(line)
        else:
            with self.log_path.open("ab") as stream:
                stream.write(line)

    def _run(self) -> None:
        while not self._stop.is_set():
            sample = self._sample()
            try:
                self._write_log(sample)
            except (OSError, ValueError):
                sample["error"] = "sample_log_failed"
            with self._lock:
                self._snapshot = sample
            if self._stop.wait(self.interval):
                break


class Vram8gbPolicy:
    def __init__(self, sampler: ResourceSampler) -> None:
        self.sampler = sampler

    def prepare_images(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not isinstance(messages, list) or not messages:
            raise BudgetRefused("invalid_images")
        copied = copy.deepcopy(messages)
        dimensions: list[dict[str, Any]] = []
        total_pixels = 0
        for message_index, message in enumerate(copied):
            if not isinstance(message, dict) or not isinstance(message.get("images", []), list):
                raise BudgetRefused("invalid_images")
            for image_index, encoded in enumerate(message.get("images", [])):
                if len(dimensions) >= 6:
                    raise BudgetRefused("image_count_exceeded")
                if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                    raise BudgetRefused("image_bytes_exceeded")
                try:
                    raw = base64.b64decode(encoded, validate=True)
                    if not raw or len(raw) > MAX_IMAGE_BYTES:
                        raise BudgetRefused("image_bytes_exceeded")
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", Image.DecompressionBombWarning)
                        with Image.open(io.BytesIO(raw)) as header:
                            width, height = header.size
                            pixels = width * height
                            if pixels > MAX_IMAGE_PIXELS or width <= 0 or height <= 0:
                                raise BudgetRefused("image_pixels_exceeded")
                            total_pixels += pixels
                            if total_pixels > MAX_TOTAL_PIXELS:
                                raise BudgetRefused("total_image_pixels_exceeded")
                            if getattr(header, "n_frames", 1) != 1:
                                raise BudgetRefused("animated_image_unsupported")
                            header.verify()
                        with Image.open(io.BytesIO(raw)) as source:
                            source.load()
                            prepared = source.convert("RGB")
                            prepared.thumbnail((448, 448), Image.Resampling.LANCZOS)
                            output = io.BytesIO()
                            prepared.save(output, format="JPEG", quality=85)
                            actual_size = list(prepared.size)
                    message["images"][image_index] = base64.b64encode(output.getvalue()).decode("ascii")
                    dimensions.append({"message_index": message_index, "image_index": image_index,
                                       "original_size": [width, height], "prepared_size": actual_size})
                except BudgetRefused:
                    raise
                except (binascii.Error, ValueError, OSError, SyntaxError, EOFError,
                        Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                    raise BudgetRefused("invalid_image") from exc
        if not dimensions:
            raise BudgetRefused("images_required")
        return copied, {"image_count": len(dimensions), "max_edge": 448, "images": dimensions}

    def _admit_sample(self) -> dict[str, Any]:
        sample = self.sampler.snapshot()
        if not isinstance(sample, dict) or sample.get("error"):
            raise BudgetRefused("sensor_unavailable")
        if sample.get("latched") is not False:
            raise BudgetRefused("resource_pressure_latched")
        timestamp = sample.get("monotonic")
        if not _number(timestamp) or not 0 <= time.monotonic() - timestamp <= MAX_SAMPLE_AGE_SECONDS:
            raise BudgetRefused("sensor_stale")
        if sample.get("gpu_count") != 1 or type(sample.get("gpu_count")) is not int:
            raise BudgetRefused("single_gpu_required")
        if not all(_number(sample.get(key)) for key in ("total_mib", "used_mib", "free_mib", "ram_available_mib")):
            raise BudgetRefused("sensor_unavailable")
        if sample["total_mib"] < 7500 or sample["used_mib"] > 5120 or sample["free_mib"] < 3072:
            raise BudgetRefused("vram_budget_unavailable")
        if sample["ram_available_mib"] < 4096:
            raise BudgetRefused("ram_budget_unavailable")
        return sample

    async def admit(self, client: httpx.AsyncClient, endpoint: str, model: str) -> dict[str, Any]:
        self._admit_sample()
        if model != MODEL_NAME:
            raise BudgetRefused("model_not_approved")
        if not isinstance(endpoint, str):
            raise BudgetRefused("local_ollama_required")
        try:
            parsed = urlparse(endpoint)
            parsed.port  # Reject malformed ports before handing the URL to HTTPX.
        except ValueError as exc:
            raise BudgetRefused("local_ollama_required") from exc
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise BudgetRefused("local_ollama_required")
        raw = bytearray()
        try:
            async with client.stream("GET", endpoint.rstrip("/") + "/api/ps", follow_redirects=False) as response:
                if response.status_code != 200:
                    raise BudgetRefused("model_status_unavailable")
                async for chunk in response.aiter_bytes(chunk_size=MAX_STATUS_BYTES):
                    raw.extend(chunk)
                    if len(raw) > MAX_STATUS_BYTES:
                        raise BudgetRefused("model_status_too_large")
            payload = json.loads(raw)
        except BudgetRefused:
            raise
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise BudgetRefused("model_status_unavailable") from exc
        loaded = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(loaded, list) or len(loaded) != 1 or not isinstance(loaded[0], dict):
            raise BudgetRefused("single_warm_model_required")
        item = loaded[0]
        if item.get("name") != MODEL_NAME or item.get("model") != MODEL_NAME:
            raise BudgetRefused("loaded_model_mismatch")
        if item.get("digest") != MODEL_DIGEST:
            raise BudgetRefused("loaded_digest_mismatch")
        details = item.get("details")
        if not isinstance(details, dict) or details.get("quantization_level") != "Q4_K_M":
            raise BudgetRefused("loaded_quantization_mismatch")
        context = item.get("context_length")
        if type(context) is not int or not 0 < context <= 4096:
            raise BudgetRefused("loaded_context_unavailable")
        size_vram = item.get("size_vram")
        if type(size_vram) is not int or not 0 < size_vram <= 4096 * _MIB:
            raise BudgetRefused("loaded_vram_unavailable")
        sample = self._admit_sample()  # Also fail closed if the probe changed during HTTP I/O.
        return {"policy": "vram8gb", "model": MODEL_NAME, "digest": MODEL_DIGEST,
                "quantization": "Q4_K_M", "context_length": context, "size_vram_mib": size_vram / _MIB,
                "sample": sample}
