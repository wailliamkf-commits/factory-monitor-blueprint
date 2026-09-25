"""Small deterministic tests for stored-ZIP release reconstruction."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import rebuild_release_resources as rebuild


class RebuildReleaseResourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.seed_path = self.root / "seed.zip"
        self.recipe_path = self.root / "recipe.json"
        self.parts_path = self.root / "parts.json"
        self.output_dir = self.root / "restored"
        self.payloads = {"a.txt": b"small payload A", "folder/b.bin": bytes(range(64))}
        with zipfile.ZipFile(self.seed_path, "w", compression=zipfile.ZIP_STORED) as seed:
            for name, content in self.payloads.items():
                seed.writestr(name, content)
        self.expected_zip = self._build_recipe()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _build_recipe(self) -> bytes:
        original_path = self.root / "original.zip"
        with zipfile.ZipFile(original_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, (name, content) in enumerate(self.payloads.items()):
                info = zipfile.ZipInfo(f"FactoryMonitor-Windows-20260925/{name}", date_time=(2026, 9, 25, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                if index == 1:
                    with archive.open(info, "w", force_zip64=True) as output:
                        output.write(content)
                else:
                    archive.writestr(info, content)
        raw = original_path.read_bytes()
        entries = []
        with zipfile.ZipFile(original_path) as archive:
            for info in archive.infolist():
                with original_path.open("rb") as stream:
                    stream.seek(info.header_offset)
                    fixed = stream.read(30)
                    name_len, extra_len = struct.unpack_from("<HH", fixed, 26)
                    header = fixed + stream.read(name_len + extra_len)
                entries.append({
                    "path": info.filename,
                    "header_base64": base64.b64encode(header).decode("ascii"),
                    "size": len(self.payloads[info.filename.split("/", 1)[1]]),
                    "sha256": hashlib.sha256(self.payloads[info.filename.split("/", 1)[1]]).hexdigest(),
                    "source": {"kind": "seed", "name": info.filename.split("/", 1)[1]},
                })
            footer = raw[archive.start_dir:]
        recipe = {
            "schema_version": 1,
            "entries": entries,
            "footer_base64": base64.b64encode(footer).decode("ascii"),
            "total_size": len(raw),
            "total_sha256": hashlib.sha256(raw).hexdigest(),
        }
        self.recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
        chunk_bytes = 97
        parts = []
        for start in range(0, len(raw), chunk_bytes):
            data = raw[start : start + chunk_bytes]
            seq = len(parts) + 1
            parts.append({
                "name": f"part-{seq:06d}-of-{(len(raw) + chunk_bytes - 1) // chunk_bytes:06d}.bin",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
        self.parts_manifest = {
            "schema_version": 1,
            "original_filename": "FactoryMonitor-Windows-20260925.zip",
            "total_size": len(raw),
            "total_sha256": hashlib.sha256(raw).hexdigest(),
            "parts": parts,
        }
        self.parts_path.write_text(json.dumps(self.parts_manifest), encoding="utf-8")
        return raw

    def test_rebuilds_exact_zip_from_seed_and_splits(self) -> None:
        result = rebuild.rebuild_resources(self.recipe_path, self.seed_path, self.parts_path, self.output_dir)
        self.assertEqual(result["status"], "verified")
        self.assertEqual((self.output_dir / "FactoryMonitor-Windows-20260925.zip.parts.json").read_bytes(), self.parts_path.read_bytes())
        manifest = self.parts_manifest
        rebuilt = b"".join((self.output_dir / part["name"]).read_bytes() for part in manifest["parts"])
        self.assertEqual(rebuilt, self.expected_zip)
        rebuilt_path = self.root / "rebuilt.zip"
        rebuilt_path.write_bytes(rebuilt)
        with zipfile.ZipFile(rebuilt_path) as archive:
            self.assertEqual(archive.read("FactoryMonitor-Windows-20260925/a.txt"), self.payloads["a.txt"])
            self.assertEqual(archive.read("FactoryMonitor-Windows-20260925/folder/b.bin"), self.payloads["folder/b.bin"])
        self.assertEqual(hashlib.sha256(rebuilt).hexdigest(), manifest["total_sha256"])

    def test_zip64_local_header_extra_resolves_forced_sizes(self) -> None:
        recipe, _raw = rebuild._read_json(self.recipe_path)
        item = next(entry for entry in recipe["entries"] if entry["path"].endswith("/folder/b.bin"))
        header = bytearray(base64.b64decode(item["header_base64"]))
        # Mirror large ZIP64 entries, whose 32-bit local sizes are sentinels.
        struct.pack_into("<II", header, 18, 0xFFFFFFFF, 0xFFFFFFFF)
        item["header_base64"] = base64.b64encode(header).decode("ascii")
        entries, _footer, _total_size, _total_sha = rebuild._parse_recipe(recipe)
        forced = next(entry for entry in entries if entry["path"].endswith("/folder/b.bin"))
        fields = rebuild._ZIP_LOCAL_HEADER.unpack_from(forced["header"])
        self.assertEqual(fields[7], 0xFFFFFFFF)
        self.assertEqual(fields[8], 0xFFFFFFFF)
        self.assertEqual(forced["size"], len(self.payloads["folder/b.bin"]))

    def test_bad_seed_payload_is_rejected_and_output_removed(self) -> None:
        with zipfile.ZipFile(self.seed_path, "w", compression=zipfile.ZIP_STORED) as seed:
            for name, content in self.payloads.items():
                seed.writestr(name, content + b"!")
        with self.assertRaises(rebuild.RebuildError):
            rebuild.rebuild_resources(self.recipe_path, self.seed_path, self.parts_path, self.output_dir)
        self.assertFalse(self.output_dir.exists())
        self.assertFalse(list(self.root.glob(".restored.*.partial")))

    def test_bad_part_digest_is_rejected_and_output_removed(self) -> None:
        self.parts_manifest["parts"][0]["sha256"] = "0" * 64
        self.parts_path.write_text(json.dumps(self.parts_manifest), encoding="utf-8")
        with self.assertRaises(rebuild.RebuildError):
            rebuild.rebuild_resources(self.recipe_path, self.seed_path, self.parts_path, self.output_dir)
        self.assertFalse(self.output_dir.exists())
        self.assertFalse(list(self.root.glob(".restored.*.partial")))


if __name__ == "__main__":
    unittest.main()
