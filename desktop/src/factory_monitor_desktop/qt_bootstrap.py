"""Prepare a verified macOS Qt Cocoa platform plugin for hidden environments."""

from __future__ import annotations

import hashlib
from importlib import metadata
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import MutableMapping


_PLUGIN_ENV = "QT_QPA_PLATFORM_PLUGIN_PATH"
_PLUGIN_NAME = "libqcocoa.dylib"


def _installed_cocoa_plugin() -> tuple[Path, str]:
    from PySide6.QtCore import QLibraryInfo

    import PySide6

    plugin_dir = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    try:
        version = metadata.version("PySide6")
    except metadata.PackageNotFoundError:
        version = str(getattr(PySide6, "__version__", "unknown"))
    return plugin_dir / "platforms" / _PLUGIN_NAME, version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configure_qt_platform_plugin_path(
    *,
    platform_name: str | None = None,
    environ: MutableMapping[str, str] | None = None,
    plugin_file: Path | None = None,
    cache_root: Path | None = None,
    pyside_version: str | None = None,
) -> Path | None:
    """Copy the current PySide Cocoa plugin to a verified user cache and set its search path.

    Explicit Qt plugin configuration always wins. The function is a no-op on
    other operating systems and does not modify the PySide installation.
    ``plugin_file``, ``cache_root`` and ``pyside_version`` allow filesystem
    behavior to be tested without importing Qt or touching a user's cache.
    """
    current_platform = sys.platform if platform_name is None else platform_name
    if current_platform != "darwin":
        return None

    target_env = os.environ if environ is None else environ
    if _PLUGIN_ENV in target_env:
        return None
    selected_qpa = target_env.get("QT_QPA_PLATFORM", "").partition(":")[0].strip().lower()
    if selected_qpa and selected_qpa != "cocoa":
        return None

    if plugin_file is None or pyside_version is None:
        discovered_file, discovered_version = _installed_cocoa_plugin()
        plugin_file = plugin_file or discovered_file
        pyside_version = pyside_version or discovered_version

    source = Path(plugin_file)
    if not source.is_file():
        return None
    source = source.resolve(strict=True)
    digest = _sha256(source)
    version = re.sub(r"[^A-Za-z0-9._+-]", "_", str(pyside_version)) or "unknown"

    if cache_root is None:
        cache_root = Path.home() / "Library" / "Caches" / "FactoryMonitorDesktop" / "qt-platforms"
    destination_dir = Path(cache_root) / f"{version}-{digest}" / "platforms"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / _PLUGIN_NAME

    if not destination.is_file() or _sha256(destination) != digest:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{_PLUGIN_NAME}.", suffix=".tmp", dir=destination_dir
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            os.chmod(temporary, stat.S_IMODE(source.stat().st_mode))
            if _sha256(temporary) != digest:
                raise OSError("copied Qt Cocoa plugin failed SHA-256 verification")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    if _sha256(destination) != digest:
        raise OSError("cached Qt Cocoa plugin failed SHA-256 verification")
    target_env[_PLUGIN_ENV] = str(destination_dir)
    return destination
