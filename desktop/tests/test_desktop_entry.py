from __future__ import annotations

import importlib
from pathlib import Path


def _entry_helpers():
    module = importlib.import_module("factory_monitor_desktop.__main__")
    return (
        getattr(module, "default_detector_weights", None),
        getattr(module, "resolve_detector_weights", None),
    )


def test_checkout_default_detector_weight_uses_implementation_model_directory(tmp_path):
    default_path, _ = _entry_helpers()
    assert callable(default_path), "desktop entry default model-path helper is missing"
    repository = tmp_path / "repository"
    implementation = repository / "implementation"
    implementation.mkdir(parents=True)
    (implementation / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    config = repository / "runs" / "test" / "config.json"

    result = default_path(config, repository)

    assert result == implementation / "models" / "yolo11n.pt"
    assert result.is_absolute()


def test_installed_wheel_default_detector_weight_is_next_to_config(tmp_path):
    default_path, _ = _entry_helpers()
    assert callable(default_path), "desktop entry default model-path helper is missing"
    application_root = tmp_path / "installed-wheel"
    application_root.mkdir()
    config = tmp_path / "user-data" / "FactoryMonitorDesktop" / "config.json"

    result = default_path(config, application_root)

    assert result == config.parent / "models" / "yolo11n.pt"
    assert result.is_absolute()


def test_existing_relative_detector_weight_is_resolved_from_config_directory(tmp_path):
    _, resolve_path = _entry_helpers()
    assert callable(resolve_path), "desktop entry model-path resolver is missing"
    config = tmp_path / "settings" / "config.json"

    result = resolve_path(config, "models/yolo11n.pt", working_directory=tmp_path / "cwd")

    assert result == config.parent / "models" / "yolo11n.pt"


def test_explicit_detector_weight_overrides_existing_config_path_from_working_directory(tmp_path):
    _, resolve_path = _entry_helpers()
    assert callable(resolve_path), "desktop entry model-path resolver is missing"
    config = tmp_path / "settings" / "config.json"
    working_directory = tmp_path / "operator-working-directory"

    result = resolve_path(
        config,
        "old-relative.pt",
        explicit_path=Path("approved/weights.pt"),
        working_directory=working_directory,
    )

    assert result == working_directory / "approved" / "weights.pt"


def test_8gb_existing_enabled_config_cannot_bypass_gateway(tmp_path):
    import pytest
    from factory_monitor.config import default_config
    module = importlib.import_module('factory_monitor_desktop.__main__')
    prepare = getattr(module, 'prepare_runtime_config', None)
    assert callable(prepare)
    config = default_config()
    with pytest.raises(ValueError, match='routes'):
        prepare(config, tmp_path / 'config.json', resource_profile='vram8gb')
    assert config['detection']['device'] == 'auto'


def test_8gb_runtime_copy_bounds_cpu_fps_and_preserves_evidence(tmp_path):
    from factory_monitor.config import default_config
    module = importlib.import_module('factory_monitor_desktop.__main__')
    prepare = getattr(module, 'prepare_runtime_config', None)
    assert callable(prepare)
    config = default_config()
    actual = prepare(config, tmp_path / 'config.json', resource_profile='vram8gb',
                     gateway_endpoint='http://127.0.0.1:32123', model='pinned')
    assert actual['detection']['device'] == 'cpu'
    assert actual['detection']['fps'] == 2
    assert actual['review']['endpoint'] == 'http://127.0.0.1:32123'
    assert actual['evidence'] == config['evidence']
    assert actual['cameras'] == config['cameras']
    assert config['detection']['fps'] == 5


def test_existing_profile_explicitly_retains_configured_device(tmp_path):
    from factory_monitor.config import default_config
    module = importlib.import_module('factory_monitor_desktop.__main__')
    prepare = getattr(module, 'prepare_runtime_config', None)
    assert callable(prepare)
    config = default_config()
    actual = prepare(config, tmp_path / 'config.json', resource_profile='existing')
    assert actual['detection']['device'] == 'auto'
    assert actual['detection']['fps'] == 5
