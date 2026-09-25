#!/usr/bin/env python3
"""Rebuild and split a stored ZIP from verified seed files and HTTPS sources."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO

BUFFER_BYTES = 1_048_576
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PART_RE = re.compile(r"^part-(\d{6})-of-(\d{6})\.bin$")
_ZIP_LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")


class RebuildError(Exception):
    """Unsafe, malformed, or incomplete input to a release rebuild."""


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise RebuildError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    _regular_file(path, "JSON input")
    try:
        raw = path.read_bytes()
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    except RebuildError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RebuildError(f"cannot read JSON input {path.name}: {exc}") from exc
    if not isinstance(obj, dict):
        raise RebuildError(f"JSON input must contain an object: {path.name}")
    return obj, raw


def _regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise RebuildError(f"{label} does not exist: {path.name}") from None
    except OSError as exc:
        raise RebuildError(f"cannot inspect {label} {path.name}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RebuildError(f"{label} must be a regular non-symlink file: {path.name}")
    return info


def _safe_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise RebuildError(f"{label} must be a safe relative ZIP path")
    posix = PurePosixPath(value)
    if posix.is_absolute() or PureWindowsPath(value).is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise RebuildError(f"{label} must not contain an absolute path or traversal")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise RebuildError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def _decode_base64(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise RebuildError(f"{label} must be base64 text")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise RebuildError(f"invalid base64 in {label}") from exc


def _parse_parts(manifest: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    if set(manifest) != {"schema_version", "original_filename", "total_size", "total_sha256", "parts"}:
        raise RebuildError("parts manifest has an unsupported schema")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise RebuildError("unsupported parts manifest version")
    filename = manifest["original_filename"]
    if not isinstance(filename, str) or not filename or Path(filename).name != filename or PureWindowsPath(filename).name != filename or filename in {".", ".."}:
        raise RebuildError("parts manifest original_filename must be a basename")
    if type(manifest["total_size"]) is not int or manifest["total_size"] < 0:
        raise RebuildError("parts manifest total_size must be non-negative")
    total_sha = _digest(manifest["total_sha256"], "parts manifest total_sha256")
    raw_parts = manifest["parts"]
    if not isinstance(raw_parts, list) or not raw_parts:
        raise RebuildError("parts manifest must have a non-empty parts array")
    parts: list[dict[str, Any]] = []
    total_count = len(raw_parts)
    for part in raw_parts:
        if not isinstance(part, dict) or set(part) != {"name", "size", "sha256"}:
            raise RebuildError("each part must contain only name, size, sha256")
        name = part["name"]
        if not isinstance(name, str) or Path(name).name != name or PureWindowsPath(name).name != name:
            raise RebuildError("part name must be a safe basename")
        match = _PART_RE.fullmatch(name)
        if not match:
            raise RebuildError(f"invalid part name: {name}")
        sequence, advertised_total = map(int, match.groups())
        if advertised_total != total_count or sequence < 1 or sequence > total_count:
            raise RebuildError("part sequence count does not match manifest")
        if type(part["size"]) is not int or part["size"] < 0:
            raise RebuildError(f"invalid part size: {name}")
        parts.append({"name": name, "size": part["size"], "sha256": _digest(part["sha256"], f"part {name} sha256"), "sequence": sequence})
    parts.sort(key=lambda p: p["sequence"])
    if [p["sequence"] for p in parts] != list(range(1, total_count + 1)):
        raise RebuildError("part numbers must be unique and contiguous")
    if sum(p["size"] for p in parts) != manifest["total_size"]:
        raise RebuildError("part sizes do not sum to total_size")
    if len(parts) > 1 and any(p["size"] != parts[0]["size"] for p in parts[:-1]):
        raise RebuildError("all non-final part sizes must match")
    if parts[-1]["size"] > parts[0]["size"]:
        raise RebuildError("final part cannot exceed non-final part size")
    return filename, parts


def _parse_recipe(recipe: dict[str, Any]) -> tuple[list[dict[str, Any]], bytes, int, str]:
    if set(recipe) != {"schema_version", "entries", "footer_base64", "total_size", "total_sha256"}:
        raise RebuildError("recipe has an unsupported schema")
    if type(recipe["schema_version"]) is not int or recipe["schema_version"] != 1:
        raise RebuildError("unsupported recipe schema_version")
    if type(recipe["total_size"]) is not int or recipe["total_size"] < 0:
        raise RebuildError("recipe total_size must be non-negative")
    total_sha = _digest(recipe["total_sha256"], "recipe total_sha256")
    footer = _decode_base64(recipe["footer_base64"], "footer_base64")
    entries = recipe["entries"]
    if not isinstance(entries, list) or not entries:
        raise RebuildError("recipe entries must be a non-empty array")
    normalized = []
    seen_paths: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "header_base64", "size", "sha256", "source"}:
            raise RebuildError("each recipe entry must contain path, header_base64, size, sha256, source")
        name = _safe_relative_path(item["path"], "entry path")
        if name in seen_paths:
            raise RebuildError(f"duplicate ZIP entry path: {name}")
        seen_paths.add(name)
        if type(item["size"]) is not int or item["size"] < 0:
            raise RebuildError(f"invalid entry size: {name}")
        header = _decode_base64(item["header_base64"], f"header for {name}")
        if len(header) < _ZIP_LOCAL_HEADER.size:
            raise RebuildError(f"truncated local ZIP header for {name}")
        fields = _ZIP_LOCAL_HEADER.unpack_from(header)
        signature, _version, flags, method, _time, _date, crc, compressed, uncompressed, name_len, extra_len = fields
        if signature != 0x04034B50 or method != zipfile.ZIP_STORED or flags & 0x0001 or flags & 0x0008:
            raise RebuildError(f"entry is not a supported stored local ZIP record: {name}")
        if len(header) != _ZIP_LOCAL_HEADER.size + name_len + extra_len:
            raise RebuildError(f"local ZIP header length mismatch: {name}")
        if compressed == 0xFFFFFFFF or uncompressed == 0xFFFFFFFF:
            extra = header[_ZIP_LOCAL_HEADER.size + name_len :]
            cursor = 0
            zip64_sizes: tuple[int, int] | None = None
            while cursor + 4 <= len(extra):
                tag, field_size = struct.unpack_from("<HH", extra, cursor)
                cursor += 4
                field = extra[cursor : cursor + field_size]
                if len(field) != field_size:
                    raise RebuildError(f"truncated ZIP extra field: {name}")
                cursor += field_size
                if tag == 0x0001:
                    field_cursor = 0
                    expanded_uncompressed = uncompressed
                    expanded_compressed = compressed
                    if uncompressed == 0xFFFFFFFF:
                        if field_cursor + 8 > len(field):
                            raise RebuildError(f"missing ZIP64 uncompressed size: {name}")
                        expanded_uncompressed = struct.unpack_from("<Q", field, field_cursor)[0]
                        field_cursor += 8
                    if compressed == 0xFFFFFFFF:
                        if field_cursor + 8 > len(field):
                            raise RebuildError(f"missing ZIP64 compressed size: {name}")
                        expanded_compressed = struct.unpack_from("<Q", field, field_cursor)[0]
                    zip64_sizes = (expanded_compressed, expanded_uncompressed)
                    break
            if zip64_sizes is None:
                raise RebuildError(f"missing ZIP64 size extra field: {name}")
            compressed, uncompressed = zip64_sizes
        if compressed != item["size"] or uncompressed != item["size"]:
            raise RebuildError(f"local ZIP header size mismatch: {name}")
        raw_name = header[_ZIP_LOCAL_HEADER.size : _ZIP_LOCAL_HEADER.size + name_len]
        try:
            header_name = raw_name.decode("utf-8" if flags & 0x0800 else "cp437")
        except UnicodeError as exc:
            raise RebuildError(f"invalid filename encoding in local header: {name}") from exc
        if header_name != name:
            raise RebuildError(f"local ZIP header path mismatch: {name}")
        source = item["source"]
        if not isinstance(source, dict) or source.get("kind") not in {"seed", "url"}:
            raise RebuildError(f"invalid source for ZIP entry: {name}")
        if source["kind"] == "seed":
            if set(source) != {"kind", "name"}:
                raise RebuildError(f"seed source schema invalid for {name}")
            source = {"kind": "seed", "name": _safe_relative_path(source["name"], "seed entry name")}
        else:
            if set(source) != {"kind", "url"} or not isinstance(source.get("url"), str):
                raise RebuildError(f"URL source schema invalid for {name}")
            parsed = urllib.parse.urlsplit(source["url"])
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise RebuildError(f"source URL must be HTTPS without embedded credentials: {name}")
            source = {"kind": "url", "url": source["url"]}
        normalized.append({"path": name, "header": header, "size": item["size"], "sha256": _digest(item["sha256"], f"entry {name} sha256"), "crc32": crc, "source": source})
    return normalized, footer, recipe["total_size"], total_sha


def _stream_to_temp(source: BinaryIO, temp_path: Path, expected_size: int, expected_sha: str, entry_path: str) -> int:
    digest = hashlib.sha256()
    crc = 0
    size = 0
    with temp_path.open("xb") as output:
        while block := source.read(BUFFER_BYTES):
            size += len(block)
            if size > expected_size:
                raise RebuildError(f"payload larger than expected: {entry_path}")
            digest.update(block)
            crc = zlib.crc32(block, crc)
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    if size != expected_size or digest.hexdigest() != expected_sha:
        raise RebuildError(f"payload size/SHA-256 mismatch: {entry_path}")
    return crc & 0xFFFFFFFF


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RebuildError("HTTPS source attempted a non-HTTPS or credential-bearing redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_checked(url: str, path: Path, size: int, sha: str, entry_path: str) -> int:
    opener = urllib.request.build_opener(_HTTPSRedirectHandler())
    for attempt in range(1, 4):
        print(f"Downloading {entry_path} (attempt {attempt}/3)", flush=True)
        path.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "factory-monitor-release-rebuilder/1"})
            with opener.open(request, timeout=90) as response:
                crc = _stream_to_temp(response, path, size, sha, entry_path)
            return crc
        except Exception as exc:
            path.unlink(missing_ok=True)
            if attempt == 3:
                raise RebuildError(f"download failed after 3 attempts for {entry_path} ({type(exc).__name__})") from None
    raise AssertionError("unreachable")


class MultipartWriter:
    """Write one logical byte stream directly into verified manifest parts."""

    def __init__(self, directory: Path, parts: list[dict[str, Any]]) -> None:
        self.directory = directory
        self.parts = parts
        self.index = 0
        self.part_size = 0
        self.part_hash = hashlib.sha256()
        self.whole_hash = hashlib.sha256()
        self.total_size = 0
        self.stream: BinaryIO | None = None
        self.temp_paths: list[Path] = []

    def _open_part(self) -> None:
        part = self.parts[self.index]
        path = self.directory / f".{part['name']}.partial"
        self.stream = path.open("xb")
        self.temp_paths.append(path)

    def _close_part(self) -> None:
        if self.stream is not None:
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()
            self.stream = None

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            if self.index >= len(self.parts):
                raise RebuildError("reconstructed ZIP exceeds parts-manifest total size")
            part = self.parts[self.index]
            if self.stream is None:
                self._open_part()
            count = min(len(view) - offset, part["size"] - self.part_size)
            if count <= 0:
                self._finish_current()
                continue
            block = view[offset : offset + count]
            assert self.stream is not None
            self.stream.write(block)
            self.part_hash.update(block)
            self.whole_hash.update(block)
            self.part_size += count
            self.total_size += count
            offset += count
            if self.part_size == part["size"]:
                self._finish_current()

    def _finish_current(self) -> None:
        self._close_part()
        part = self.parts[self.index]
        temp_path = self.directory / f".{part['name']}.partial"
        actual_size = temp_path.stat().st_size
        if actual_size != part["size"] or self.part_hash.hexdigest() != part["sha256"]:
            raise RebuildError(f"part size/SHA-256 mismatch: {part['name']}")
        self.index += 1
        self.part_size = 0
        self.part_hash = hashlib.sha256()

    def finish(self, total_size: int, total_sha: str) -> None:
        if self.stream is not None or self.index < len(self.parts):
            if self.stream is None and self.index < len(self.parts) and self.parts[self.index]["size"] == 0:
                self._open_part()
            if self.index < len(self.parts):
                self._finish_current()
        if self.index != len(self.parts) or self.total_size != total_size or self.whole_hash.hexdigest() != total_sha:
            raise RebuildError("reconstructed ZIP total size/SHA-256 mismatch")
        for part in self.parts:
            temp_path = self.directory / f".{part['name']}.partial"
            os.rename(temp_path, self.directory / part["name"])
        self.temp_paths.clear()

    def cleanup(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        for path in self.temp_paths:
            path.unlink(missing_ok=True)


def rebuild_resources(recipe_path: Path, seed_path: Path, parts_manifest_path: Path, output_dir: Path) -> dict[str, Any]:
    recipe, _ = _read_json(recipe_path)
    manifest, manifest_bytes = _read_json(parts_manifest_path)
    entries, footer, recipe_size, recipe_sha = _parse_recipe(recipe)
    filename, parts = _parse_parts(manifest)
    if recipe_size != manifest["total_size"] or recipe_sha != manifest["total_sha256"]:
        raise RebuildError("recipe and parts manifest disagree on whole ZIP size/SHA-256")
    if output_dir.exists() or output_dir.is_symlink():
        raise RebuildError(f"refusing to replace existing output directory: {output_dir}")
    _regular_file(seed_path, "seed ZIP")

    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", suffix=".partial", dir=parent))
    writer = MultipartWriter(staging, parts)
    try:
        with zipfile.ZipFile(seed_path, "r") as seed:
            seed_map: dict[str, zipfile.ZipInfo] = {}
            for info in seed.infolist():
                _safe_relative_path(info.filename.rstrip("/") if info.is_dir() else info.filename, "seed ZIP path")
                if info.filename in seed_map or info.is_dir() or (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                    raise RebuildError(f"duplicate, directory, or symlink in seed ZIP: {info.filename}")
                seed_map[info.filename] = info
            for index, entry in enumerate(entries, start=1):
                writer.write(entry["header"])
                temp_path = staging / f".payload-{index:04d}.partial"
                try:
                    if entry["source"]["kind"] == "seed":
                        seed_name = entry["source"]["name"]
                        if seed_name not in seed_map:
                            raise RebuildError(f"seed entry missing for {entry['path']}")
                        with seed.open(seed_map[seed_name], "r") as source:
                            crc = _stream_to_temp(source, temp_path, entry["size"], entry["sha256"], entry["path"])
                    else:
                        crc = _download_checked(entry["source"]["url"], temp_path, entry["size"], entry["sha256"], entry["path"])
                    if crc != entry["crc32"]:
                        raise RebuildError(f"ZIP header CRC mismatch: {entry['path']}")
                    with temp_path.open("rb") as payload:
                        while block := payload.read(BUFFER_BYTES):
                            writer.write(block)
                    print(f"Verified {index}/{len(entries)} {entry['path']}", flush=True)
                finally:
                    temp_path.unlink(missing_ok=True)
        writer.write(footer)
        writer.finish(recipe_size, recipe_sha)
        manifest_name = f"{filename}.parts.json"
        (staging / manifest_name).write_bytes(manifest_bytes)
        if output_dir.exists() or output_dir.is_symlink():
            raise RebuildError(f"refusing to replace existing output directory: {output_dir}")
        os.rename(staging, output_dir)
        return {"status": "verified", "output_dir": str(output_dir), "part_count": len(parts), "total_size": recipe_size, "total_sha256": recipe_sha}
    except Exception:
        writer.cleanup()
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--parts-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = rebuild_resources(args.recipe, args.seed, args.parts_manifest, args.output_dir)
    except Exception as exc:
        print(f"rebuild_release_resources: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
