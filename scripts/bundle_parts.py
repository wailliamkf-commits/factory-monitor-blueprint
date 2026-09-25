#!/usr/bin/env python3
"""Stream large offline bundles into independently verifiable parts.

Manifest JSON (schema_version 1)::

    {
      "schema_version": 1,
      "original_filename": "Factory-Monitor-Windows.zip",
      "total_size": 1234,
      "total_sha256": "<64 lowercase hex characters>",
      "parts": [
        {"name": "part-000001-of-000002.bin", "size": 1000,
         "sha256": "<64 lowercase hex characters>"},
        {"name": "part-000002-of-000002.bin", "size": 234,
         "sha256": "<64 lowercase hex characters>"}
      ]
    }

Part sequence is derived from the validated numeric name, never list order.
All data is streamed with a 1 MiB working buffer. Split output is assembled in
a sibling ``.partial`` directory then renamed into place. Joined files are
written to a sibling ``.partial`` file and linked into place atomically so an
existing output is never replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any, BinaryIO

DEFAULT_CHUNK_BYTES = 1_073_741_824
BUFFER_BYTES = 1_048_576
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PART_RE = re.compile(r"^part-(\d{6})-of-(\d{6})\.bin$")


class BundleError(Exception):
    """Invalid input or an operation that cannot safely be completed."""


def _safe_basename(name: Any, field: str) -> str:
    if not isinstance(name, str) or not name or name in {".", ".."}:
        raise BundleError(f"{field} must be a non-empty safe filename")
    if "\x00" in name or "/" in name or "\\" in name:
        raise BundleError(f"{field} must be a basename without path separators")
    if Path(name).name != name or PureWindowsPath(name).name != name:
        raise BundleError(f"{field} must not contain a path")
    if field == "part name" and not _PART_RE.fullmatch(name):
        raise BundleError("part name must match part-NNNNNN-of-NNNNNN.bin")
    return name


def _reject_symlink(path: Path, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError:
        raise BundleError(f"{label} does not exist: {path}") from None
    except OSError as exc:
        raise BundleError(f"cannot inspect {label} {path}: {exc}") from exc
    if stat.S_ISLNK(details.st_mode):
        raise BundleError(f"{label} must not be a symbolic link: {path}")
    return details


def _regular_file(path: Path, label: str) -> os.stat_result:
    details = _reject_symlink(path, label)
    if not stat.S_ISREG(details.st_mode):
        raise BundleError(f"{label} must be a regular file: {path}")
    return details


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(BUFFER_BYTES):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _hash_stream(stream: BinaryIO, output: BinaryIO | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while block := stream.read(BUFFER_BYTES):
        size += len(block)
        digest.update(block)
        if output is not None:
            output.write(block)
    return size, digest.hexdigest()


def _manifest_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"manifest contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_manifest(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _regular_file(path, "manifest")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_manifest_object)
    except BundleError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"cannot read manifest JSON: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "original_filename", "total_size", "total_sha256", "parts"
    }:
        raise BundleError("manifest must contain exactly schema_version, original_filename, total_size, total_sha256, parts")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise BundleError("unsupported manifest schema_version")
    _safe_basename(value["original_filename"], "original_filename")
    if type(value["total_size"]) is not int or value["total_size"] < 0:
        raise BundleError("total_size must be a non-negative integer")
    if not isinstance(value["total_sha256"], str) or not _DIGEST_RE.fullmatch(value["total_sha256"]):
        raise BundleError("total_sha256 must be 64 lowercase hexadecimal characters")
    parts_value = value["parts"]
    if not isinstance(parts_value, list) or not parts_value:
        raise BundleError("parts must be a non-empty array")

    parts: list[dict[str, Any]] = []
    seen: set[str] = set()
    advertised_total: int | None = None
    for item in parts_value:
        if not isinstance(item, dict) or set(item) != {"name", "size", "sha256"}:
            raise BundleError("each part must contain exactly name, size, sha256")
        name = _safe_basename(item["name"], "part name")
        if name in seen:
            raise BundleError(f"duplicate part name: {name}")
        seen.add(name)
        match = _PART_RE.fullmatch(name)
        assert match is not None
        sequence, total = map(int, match.groups())
        if sequence < 1 or total < 1 or sequence > total:
            raise BundleError(f"invalid part sequence: {name}")
        if advertised_total is None:
            advertised_total = total
        elif total != advertised_total:
            raise BundleError("part names disagree on total part count")
        if type(item["size"]) is not int or item["size"] < 0:
            raise BundleError(f"invalid part size: {name}")
        if not isinstance(item["sha256"], str) or not _DIGEST_RE.fullmatch(item["sha256"]):
            raise BundleError(f"invalid part sha256: {name}")
        parts.append({**item, "sequence": sequence})

    assert advertised_total is not None
    parts.sort(key=lambda part: part["sequence"])
    if len(parts) != advertised_total or [p["sequence"] for p in parts] != list(range(1, advertised_total + 1)):
        raise BundleError("part sequence must be contiguous from 1 through the advertised total")
    if any(p["name"] != f"part-{p['sequence']:06d}-of-{advertised_total:06d}.bin" for p in parts):
        raise BundleError("part names are not canonical for their sequence")

    sizes = [part["size"] for part in parts]
    if value["total_size"] == 0:
        if len(parts) != 1 or sizes != [0]:
            raise BundleError("an empty input must have exactly one empty part")
    else:
        if any(size <= 0 for size in sizes):
            raise BundleError("non-empty bundles cannot contain empty parts")
        if len(sizes) > 1 and any(size != sizes[0] for size in sizes[:-1]):
            raise BundleError("all non-final parts must have equal size")
        if sizes[-1] > sizes[0] or sum(sizes) != value["total_size"]:
            raise BundleError("part sizes do not match total_size")
    return value, parts


def _verify_parts(manifest_path: Path, manifest: dict[str, Any], parts: list[dict[str, Any]]) -> dict[str, Any]:
    whole = hashlib.sha256()
    total_size = 0
    for part in parts:
        part_path = manifest_path.parent / part["name"]
        _regular_file(part_path, "part")
        digest = hashlib.sha256()
        part_size = 0
        try:
            with part_path.open("rb") as stream:
                while block := stream.read(BUFFER_BYTES):
                    part_size += len(block)
                    digest.update(block)
                    whole.update(block)
        except OSError as exc:
            raise BundleError(f"cannot read part {part['name']}: {exc}") from exc
        if part_size != part["size"]:
            raise BundleError(f"size mismatch for {part['name']}: expected {part['size']}, got {part_size}")
        if digest.hexdigest() != part["sha256"]:
            raise BundleError(f"SHA-256 mismatch for {part['name']}")
        total_size += part_size
    if total_size != manifest["total_size"]:
        raise BundleError(f"total size mismatch: expected {manifest['total_size']}, got {total_size}")
    if whole.hexdigest() != manifest["total_sha256"]:
        raise BundleError("total SHA-256 mismatch")
    return {
        "status": "verified",
        "part_count": len(parts),
        "total_size": total_size,
        "total_sha256": whole.hexdigest(),
    }


def verify_manifest(manifest_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Verify every part and the reconstructed whole-file digest."""
    path = Path(manifest_path)
    manifest, parts = _read_manifest(path)
    return _verify_parts(path, manifest, parts)


def split_file(
    input_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> Path:
    """Split a file into a new output directory and return its manifest path."""
    source = Path(input_path)
    destination = Path(output_dir)
    source_stat = _regular_file(source, "input")
    if type(chunk_bytes) is not int or chunk_bytes <= 0:
        raise BundleError("chunk_bytes must be a positive integer")
    if destination.exists() or destination.is_symlink():
        raise BundleError(f"refusing to use existing output directory: {destination}")
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        part_count = max(1, (source_stat.st_size + chunk_bytes - 1) // chunk_bytes)
        if part_count > 999_999:
            raise BundleError("part count exceeds the six-digit manifest limit")
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".partial", dir=parent))
    except BundleError:
        raise
    except OSError as exc:
        raise BundleError(f"cannot create staging directory: {exc}") from exc

    try:
        parts: list[dict[str, Any]] = []
        whole_hash = hashlib.sha256()
        total_size = 0
        with source.open("rb") as input_stream:
            for sequence in range(1, part_count + 1):
                part_name = f"part-{sequence:06d}-of-{part_count:06d}.bin"
                expected_bytes = min(chunk_bytes, source_stat.st_size - total_size)
                part_hash = hashlib.sha256()
                part_size = 0
                with (staging / part_name).open("xb") as part_stream:
                    while part_size < expected_bytes:
                        block = input_stream.read(min(BUFFER_BYTES, expected_bytes - part_size))
                        if not block:
                            raise BundleError("input ended before its recorded size")
                        part_stream.write(block)
                        part_size += len(block)
                        total_size += len(block)
                        part_hash.update(block)
                        whole_hash.update(block)
                parts.append({"name": part_name, "size": part_size, "sha256": part_hash.hexdigest()})
            if input_stream.read(1):
                raise BundleError("input grew while it was being split")
        current = source.stat()
        if current.st_size != source_stat.st_size or current.st_mtime_ns != source_stat.st_mtime_ns:
            raise BundleError("input changed while it was being split")

        manifest = {
            "schema_version": 1,
            "original_filename": _safe_basename(source.name, "original_filename"),
            "total_size": total_size,
            "total_sha256": whole_hash.hexdigest(),
            "parts": parts,
        }
        manifest_name = f"{manifest['original_filename']}.parts.json"
        manifest_temp = staging / f".{manifest_name}.partial"
        with manifest_temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        manifest_temp.rename(staging / manifest_name)
        if destination.exists() or destination.is_symlink():
            raise BundleError(f"refusing to replace output directory: {destination}")
        os.rename(staging, destination)
        return destination / manifest_name
    except BundleError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise BundleError(f"split failed: {exc}") from exc


def join_file(
    manifest_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
) -> Path:
    """Verify and join a manifest's parts into a new output file."""
    manifest_file = Path(manifest_path)
    destination = Path(output_path)
    if destination.exists() or destination.is_symlink():
        raise BundleError(f"refusing to overwrite output: {destination}")
    parent = destination.parent
    if not parent.is_dir():
        raise BundleError(f"output parent directory does not exist: {parent}")
    manifest, parts = _read_manifest(manifest_file)
    temp_fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".partial", dir=parent)
    temp_path = Path(temp_name)
    whole = hashlib.sha256()
    total_size = 0
    try:
        with os.fdopen(temp_fd, "wb") as output:
            for part in parts:
                part_path = manifest_file.parent / part["name"]
                _regular_file(part_path, "part")
                part_hash = hashlib.sha256()
                part_size = 0
                try:
                    with part_path.open("rb") as source:
                        while block := source.read(BUFFER_BYTES):
                            output.write(block)
                            part_hash.update(block)
                            whole.update(block)
                            part_size += len(block)
                            total_size += len(block)
                except OSError as exc:
                    raise BundleError(f"cannot read part {part['name']}: {exc}") from exc
                if part_size != part["size"]:
                    raise BundleError(f"size mismatch for {part['name']}: expected {part['size']}, got {part_size}")
                if part_hash.hexdigest() != part["sha256"]:
                    raise BundleError(f"SHA-256 mismatch for {part['name']}")
            output.flush()
            os.fsync(output.fileno())
        if total_size != manifest["total_size"]:
            raise BundleError(f"total size mismatch: expected {manifest['total_size']}, got {total_size}")
        if whole.hexdigest() != manifest["total_sha256"]:
            raise BundleError("total SHA-256 mismatch")
        if destination.exists() or destination.is_symlink():
            raise BundleError(f"refusing to overwrite output: {destination}")
        try:
            os.link(temp_path, destination)
        except FileExistsError:
            raise BundleError(f"refusing to overwrite output: {destination}") from None
        temp_path.unlink()
        return destination
    except BundleError:
        temp_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise BundleError(f"join failed: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split, verify, or join a large offline bundle using streaming SHA-256 checks.",
        epilog=(
            "Manifest JSON schema: {schema_version: 1, original_filename: basename, total_size: integer, "
            "total_sha256: lowercase SHA-256, parts: [{name: part-NNNNNN-of-NNNNNN.bin, size: integer, "
            "sha256: lowercase SHA-256}]}. Each operation refuses existing destinations."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    split = commands.add_parser("split", help="split a file into a new directory and write <input>.parts.json")
    split.add_argument("--input", required=True, type=Path)
    split.add_argument("--output-dir", required=True, type=Path)
    split.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    join = commands.add_parser("join", help="verify and join parts into a new file")
    join.add_argument("--manifest", required=True, type=Path)
    join.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify", help="verify every part and whole-file SHA-256")
    verify.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "split":
            manifest_path = split_file(args.input, args.output_dir, args.chunk_bytes)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            print(json.dumps({"status": "split", "manifest": str(manifest_path), "part_count": len(manifest["parts"]), "total_size": manifest["total_size"], "total_sha256": manifest["total_sha256"]}, ensure_ascii=False))
        elif args.command == "join":
            output = join_file(args.manifest, args.output)
            print(json.dumps({"status": "joined", "output": str(output)}, ensure_ascii=False))
        else:
            print(json.dumps(verify_manifest(args.manifest), ensure_ascii=False))
        return 0
    except (BundleError, OSError) as exc:
        print(f"bundle_parts: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
