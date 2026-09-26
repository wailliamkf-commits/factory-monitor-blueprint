from types import SimpleNamespace

from factory_monitor_desktop.profiles import probe_hardware, recommend_profile


def test_probe_hardware_is_read_only_and_reports_unknown_nvidia_when_unavailable(monkeypatch):
    monkeypatch.setattr("factory_monitor_desktop.profiles.platform.system", lambda: "Darwin")
    monkeypatch.setattr("factory_monitor_desktop.profiles.platform.machine", lambda: "arm64")
    monkeypatch.setattr(
        "factory_monitor_desktop.profiles.psutil.virtual_memory",
        lambda: SimpleNamespace(total=32 * 1024**3, available=12 * 1024**3),
    )
    monkeypatch.setattr("factory_monitor_desktop.profiles._nvidia_smi", lambda: None)

    result = probe_hardware()

    assert result["os"] == "Darwin"
    assert result["architecture"] == "arm64"
    assert result["apple_silicon"] is True
    assert result["ram_total_bytes"] == 32 * 1024**3
    assert result["nvidia_vram_total_mb"] is None
    assert result["capacity_status"] == "NOT_BENCHMARKED"
    assert result["review_concurrency"] == 1


def test_probe_hardware_parses_bounded_nvidia_smi_report(monkeypatch):
    monkeypatch.setattr("factory_monitor_desktop.profiles.platform.system", lambda: "Windows")
    monkeypatch.setattr("factory_monitor_desktop.profiles.platform.machine", lambda: "AMD64")
    monkeypatch.setattr(
        "factory_monitor_desktop.profiles.psutil.virtual_memory",
        lambda: SimpleNamespace(total=64 * 1024**3, available=40 * 1024**3),
    )
    monkeypatch.setattr(
        "factory_monitor_desktop.profiles._nvidia_smi",
        lambda: {"total_mb": 8192, "free_mb": 4096},
    )

    result = probe_hardware()

    assert result["nvidia_vram_total_mb"] == 8192
    assert result["nvidia_vram_free_mb"] == 4096
    assert result["capacity_status"] == "NOT_BENCHMARKED"
    assert result["review_concurrency"] == 1


def test_probe_hardware_treats_invalid_nvidia_output_as_unknown(monkeypatch):
    monkeypatch.setattr("factory_monitor_desktop.profiles._nvidia_smi", lambda: {"total_mb": -1, "free_mb": 9})
    result = probe_hardware()
    assert result["nvidia_vram_total_mb"] is None
    assert result["nvidia_vram_free_mb"] is None


def test_recommend_profile_maps_hardware_to_advice_without_benchmark_claims():
    report = recommend_profile(
        {
            "os": "Windows",
            "architecture": "AMD64",
            "apple_silicon": False,
            "ram_total_bytes": 32 * 1024**3,
            "nvidia_vram_total_mb": 8192,
            "nvidia_vram_free_mb": 7000,
        }
    )

    assert report["device_tier"] == "nvidia_8gb"
    assert report["capacity_status"] == "NOT_BENCHMARKED"
    assert report["review_concurrency"] == 1
    assert "benchmark" in report["limitations"][0].lower()


def test_recommend_profile_handles_invalid_or_missing_hardware_conservatively():
    report = recommend_profile({"os": "Windows", "nvidia_vram_total_mb": "not-a-number"})

    assert report["device_tier"] == "unknown"
    assert report["capacity_status"] == "NOT_BENCHMARKED"
    assert report["review_concurrency"] == 1
