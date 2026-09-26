"""Read-only machine inventory and conservative deployment recommendations."""

from __future__ import annotations

import platform
import re
import subprocess
from typing import Any

import psutil


def _nvidia_smi() -> dict[str, int] | None:
    """Read the largest single NVIDIA adapter with a bounded subprocess call."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    devices: list[tuple[int, int]] = []
    for line in result.stdout.splitlines()[:16]:
        match = re.fullmatch(r"\s*(\d+)\s*,\s*(\d+)\s*", line)
        if not match:
            continue
        total, free = (int(value) for value in match.groups())
        if total > 0 and 0 <= free <= total:
            devices.append((total, free))
    if not devices:
        return None
    total, free = max(devices, key=lambda item: item[0])
    return {"total_mb": total, "free_mb": free}


def probe_hardware(*, include_gpu: bool = True) -> dict[str, Any]:
    """Collect basic platform and memory facts without changing the machine."""
    try:
        memory = psutil.virtual_memory()
        ram_total = int(memory.total) if memory.total > 0 else None
        ram_available = int(memory.available) if memory.available >= 0 else None
    except (OSError, AttributeError, TypeError, ValueError):
        ram_total = None
        ram_available = None
    nvidia = _nvidia_smi() if include_gpu else None
    if (
        not isinstance(nvidia, dict)
        or isinstance(nvidia.get("total_mb"), bool)
        or isinstance(nvidia.get("free_mb"), bool)
        or not isinstance(nvidia.get("total_mb"), int)
        or not isinstance(nvidia.get("free_mb"), int)
        or nvidia["total_mb"] <= 0
        or not 0 <= nvidia["free_mb"] <= nvidia["total_mb"]
    ):
        nvidia = None
    system = platform.system() or "unknown"
    architecture = platform.machine() or "unknown"
    apple_silicon = system == "Darwin" and architecture.lower() in {"arm64", "aarch64"}
    return {
        "os": system,
        "architecture": architecture,
        "apple_silicon": apple_silicon,
        "ram_total_bytes": ram_total,
        "ram_available_bytes": ram_available,
        "nvidia_vram_total_mb": nvidia["total_mb"] if nvidia else None,
        "nvidia_vram_free_mb": nvidia["free_mb"] if nvidia else None,
        "capacity_status": "NOT_BENCHMARKED",
        "review_concurrency": 1,
        "gpu_probe_status": "attempted" if include_gpu else "not_requested",
    }


def _positive_int(hardware: dict[str, Any], key: str) -> int | None:
    value = hardware.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def recommend_profile(hardware: dict[str, Any]) -> dict[str, Any]:
    """Map observed hardware to an advice tier; never claim capacity from specs."""
    if not isinstance(hardware, dict):
        hardware = {}
    vram = _positive_int(hardware, "nvidia_vram_total_mb")
    apple_silicon = hardware.get("apple_silicon") is True
    explicit_invalid_vram = (
        "nvidia_vram_total_mb" in hardware
        and hardware.get("nvidia_vram_total_mb") is not None
        and vram is None
    )
    if explicit_invalid_vram:
        tier = "unknown"
        title = "Hardware report needs correction"
        limitations = ["One or more reported hardware fields are invalid; no capacity recommendation is available."]
        upgrade_trigger = "Correct the hardware report and rerun the read-only probe."
    elif apple_silicon:
        tier = "apple_silicon"
        title = "Apple Silicon development and capture tier"
        limitations = ["Model and capture throughput are not benchmarked on this device."]
        upgrade_trigger = "Move visual review to a Windows NVIDIA host only if measured local latency or memory requires it."
    elif vram is not None and vram >= 23_000:
        tier = "nvidia_24gb_plus"
        title = "NVIDIA 24 GB or larger single-GPU tier"
        limitations = ["24 GB-class capacity does not prove ten-camera throughput or accuracy."]
        upgrade_trigger = "Consider a second inference host only after a full-runtime single-host benchmark identifies GPU saturation."
    elif vram is not None and 10_500 <= vram <= 18_500:
        tier = "nvidia_12_16gb"
        title = "NVIDIA 12–16 GB validation tier"
        limitations = ["Model, detector, capture, and evidence writing still compete for measured resources."]
        upgrade_trigger = "Increase GPU memory only when measured peak VRAM leaves no safe operating margin."
    elif vram is not None and 6_000 <= vram < 10_500:
        tier = "nvidia_8gb"
        title = "NVIDIA 8 GB constrained validation tier"
        limitations = ["An 8 GB card may force smaller inputs or serial review; throughput is not benchmarked."]
        upgrade_trigger = "Consider 12–16 GB only after the representative local workload proves memory pressure."
    elif vram is not None:
        tier = "nvidia_other"
        title = "NVIDIA adapter outside listed memory tiers"
        limitations = ["This GPU memory tier has no task-specific benchmark."]
        upgrade_trigger = "Benchmark the exact device and model before recommending a hardware change."
    else:
        tier = "cpu_fallback"
        title = "CPU fallback and interface-validation tier"
        limitations = ["CPU fallback is for validation or degraded manual review; speed is not benchmarked."]
        upgrade_trigger = "Consider a GPU only after the real workload is measured on the current host."
    return {
        "device_tier": tier,
        "title": title,
        "capacity_status": "NOT_BENCHMARKED",
        "review_concurrency": 1,
        "limitations": limitations,
        "upgrade_trigger": upgrade_trigger,
        "observed_hardware": {
            "os": hardware.get("os") if isinstance(hardware.get("os"), str) else "unknown",
            "architecture": hardware.get("architecture") if isinstance(hardware.get("architecture"), str) else "unknown",
            "ram_total_bytes": _positive_int(hardware, "ram_total_bytes"),
            "nvidia_vram_total_mb": vram,
        },
    }
