from __future__ import annotations

import hashlib
import importlib
from pathlib import Path


def _bootstrap_function():
    try:
        module = importlib.import_module("factory_monitor_desktop.qt_bootstrap")
    except ModuleNotFoundError:
        return None
    return getattr(module, "configure_qt_platform_plugin_path", None)


def test_macos_bootstrap_copies_verified_plugin_to_versioned_cache_and_sets_env(tmp_path):
    bootstrap = _bootstrap_function()
    assert callable(bootstrap), "macOS Qt plugin bootstrap API is missing"
    plugin = tmp_path / "source" / "platforms" / "libqcocoa.dylib"
    plugin.parent.mkdir(parents=True)
    plugin_bytes = b"trusted cocoa plugin bytes"
    plugin.write_bytes(plugin_bytes)
    environment = {}
    cache = tmp_path / "user-cache"

    cached_path = bootstrap(
        platform_name="darwin",
        environ=environment,
        plugin_file=plugin,
        cache_root=cache,
        pyside_version="6.8.2",
    )

    digest = hashlib.sha256(plugin_bytes).hexdigest()
    expected = cache / f"6.8.2-{digest}" / "platforms" / "libqcocoa.dylib"
    assert cached_path == expected
    assert expected.read_bytes() == plugin_bytes
    assert hashlib.sha256(expected.read_bytes()).hexdigest() == digest
    assert environment["QT_QPA_PLATFORM_PLUGIN_PATH"] == str(expected.parent)


def test_macos_bootstrap_reuses_matching_cached_plugin_without_rewriting(tmp_path):
    bootstrap = _bootstrap_function()
    assert callable(bootstrap), "macOS Qt plugin bootstrap API is missing"
    plugin = tmp_path / "libqcocoa.dylib"
    plugin.write_bytes(b"same plugin")
    environment = {}
    cache = tmp_path / "cache"
    first = bootstrap(platform_name="darwin", environ=environment, plugin_file=plugin,
                      cache_root=cache, pyside_version="6.9.0")
    before = first.stat().st_mtime_ns
    environment.clear()

    second = bootstrap(platform_name="darwin", environ=environment, plugin_file=plugin,
                       cache_root=cache, pyside_version="6.9.0")

    assert second == first
    assert second.stat().st_mtime_ns == before
    assert environment["QT_QPA_PLATFORM_PLUGIN_PATH"] == str(first.parent)


def test_macos_bootstrap_repairs_corrupt_cached_plugin_atomically(tmp_path):
    bootstrap = _bootstrap_function()
    assert callable(bootstrap), "macOS Qt plugin bootstrap API is missing"
    plugin = tmp_path / "source.dylib"
    plugin_bytes = b"correct source plugin"
    plugin.write_bytes(plugin_bytes)
    digest = hashlib.sha256(plugin_bytes).hexdigest()
    cache = tmp_path / "cache"
    target = cache / f"6.9.1-{digest}" / "platforms" / "libqcocoa.dylib"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt cache")
    environment = {}

    result = bootstrap(platform_name="darwin", environ=environment, plugin_file=plugin,
                       cache_root=cache, pyside_version="6.9.1")

    assert result == target
    assert target.read_bytes() == plugin_bytes
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    assert list(target.parent.glob("*.tmp")) == []


def test_explicit_qpa_plugin_path_is_preserved_without_touching_cache(tmp_path):
    bootstrap = _bootstrap_function()
    assert callable(bootstrap), "macOS Qt plugin bootstrap API is missing"
    explicit_path = tmp_path / "operator-selected-plugins"
    environment = {"QT_QPA_PLATFORM_PLUGIN_PATH": str(explicit_path)}
    cache = tmp_path / "cache"

    result = bootstrap(platform_name="darwin", environ=environment,
                       plugin_file=tmp_path / "missing.dylib", cache_root=cache,
                       pyside_version="6.8.2")

    assert result is None
    assert environment["QT_QPA_PLATFORM_PLUGIN_PATH"] == str(explicit_path)
    assert not cache.exists()


def test_explicit_non_cocoa_qpa_platform_does_not_copy_plugin(tmp_path):
    bootstrap = _bootstrap_function()
    assert callable(bootstrap), "macOS Qt plugin bootstrap API is missing"
    plugin = tmp_path / "source.dylib"
    plugin.write_bytes(b"cocoa plugin must not be selected for offscreen")
    environment = {"QT_QPA_PLATFORM": "offscreen"}
    cache = tmp_path / "cache"

    result = bootstrap(platform_name="darwin", environ=environment, plugin_file=plugin,
                       cache_root=cache, pyside_version="6.8.2")

    assert result is None
    assert environment == {"QT_QPA_PLATFORM": "offscreen"}
    assert not cache.exists()


def test_non_macos_platform_does_not_copy_plugin_or_set_environment(tmp_path):
    bootstrap = _bootstrap_function()
    assert callable(bootstrap), "macOS Qt plugin bootstrap API is missing"
    plugin = tmp_path / "source.dylib"
    plugin.write_bytes(b"must not be copied on other operating systems")
    environment = {}
    cache = tmp_path / "cache"

    result = bootstrap(platform_name="win32", environ=environment, plugin_file=plugin,
                       cache_root=cache, pyside_version="6.8.2")

    assert result is None
    assert environment == {}
    assert not cache.exists()
