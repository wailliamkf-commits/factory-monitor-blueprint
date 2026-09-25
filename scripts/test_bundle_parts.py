"""Small-file tests for the offline bundle part utility."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import bundle_parts


class BundlePartsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "model bundle.zip"
        self.payload = bytes(range(251)) * 3
        self.source.write_bytes(self.payload)
        self.output_dir = self.root / "parts"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_bundle(self, *, chunk_bytes: int = 100) -> tuple[Path, dict[str, object]]:
        manifest_path = bundle_parts.split_file(self.source, self.output_dir, chunk_bytes)
        return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_split_verify_join_round_trip(self) -> None:
        manifest_path, manifest = self.make_bundle(chunk_bytes=100)
        self.assertEqual(bundle_parts.verify_manifest(manifest_path)["total_sha256"], manifest["total_sha256"])
        output = self.root / "joined.zip"
        bundle_parts.join_file(manifest_path, output)
        self.assertEqual(output.read_bytes(), self.payload)

    def test_empty_file_round_trips_as_one_empty_part(self) -> None:
        self.source.write_bytes(b"")
        manifest_path, manifest = self.make_bundle()
        self.assertEqual(len(manifest["parts"]), 1)
        self.assertEqual(manifest["parts"][0]["size"], 0)
        output = self.root / "joined-empty.zip"
        bundle_parts.join_file(manifest_path, output)
        self.assertEqual(output.read_bytes(), b"")

    def test_corrupt_part_is_rejected(self) -> None:
        manifest_path, manifest = self.make_bundle()
        part_path = manifest_path.parent / manifest["parts"][0]["name"]
        part_path.write_bytes(b"corrupt")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.verify_manifest(manifest_path)

    def test_missing_part_is_rejected(self) -> None:
        manifest_path, manifest = self.make_bundle()
        (manifest_path.parent / manifest["parts"][-1]["name"]).unlink()
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.verify_manifest(manifest_path)

    def test_manifest_list_order_does_not_change_part_order(self) -> None:
        manifest_path, manifest = self.make_bundle(chunk_bytes=80)
        manifest["parts"].reverse()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output = self.root / "reordered-join.zip"
        bundle_parts.join_file(manifest_path, output)
        self.assertEqual(output.read_bytes(), self.payload)

    def test_unsafe_part_path_is_rejected(self) -> None:
        manifest_path, manifest = self.make_bundle()
        manifest["parts"][0]["name"] = "../escape.bin"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.verify_manifest(manifest_path)

    def test_duplicate_part_names_are_rejected(self) -> None:
        manifest_path, manifest = self.make_bundle()
        manifest["parts"][1]["name"] = manifest["parts"][0]["name"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.verify_manifest(manifest_path)

    def test_missing_sequence_number_is_rejected(self) -> None:
        manifest_path, manifest = self.make_bundle()
        manifest["parts"][0]["name"] = "part-000002-of-000008.bin"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.verify_manifest(manifest_path)

    def test_existing_split_directory_is_not_modified(self) -> None:
        self.output_dir.mkdir()
        sentinel = self.output_dir / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.split_file(self.source, self.output_dir, 10)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_existing_join_output_is_not_overwritten(self) -> None:
        manifest_path, _ = self.make_bundle()
        output = self.root / "joined.zip"
        output.write_bytes(b"keep")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.join_file(manifest_path, output)
        self.assertEqual(output.read_bytes(), b"keep")

    def test_symlink_input_is_rejected(self) -> None:
        alias = self.root / "alias.zip"
        try:
            alias.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.split_file(alias, self.output_dir, 10)

    def test_symlink_part_is_rejected(self) -> None:
        manifest_path, manifest = self.make_bundle()
        part = manifest_path.parent / manifest["parts"][0]["name"]
        part.unlink()
        try:
            part.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.verify_manifest(manifest_path)

    def test_invalid_manifest_schema_is_rejected(self) -> None:
        manifest_path, manifest = self.make_bundle()
        manifest["unexpected"] = "value"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.verify_manifest(manifest_path)

    def test_invalid_digest_is_rejected(self) -> None:
        manifest_path, manifest = self.make_bundle()
        manifest["total_sha256"] = "not-a-digest"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.verify_manifest(manifest_path)

    def test_failed_join_cleans_partial_output(self) -> None:
        manifest_path, manifest = self.make_bundle()
        part = manifest_path.parent / manifest["parts"][0]["name"]
        part.write_bytes(b"changed")
        output = self.root / "failed-join.zip"
        with self.assertRaises(bundle_parts.BundleError):
            bundle_parts.join_file(manifest_path, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob("*.partial")), [])

    def test_cli_split_verify_join(self) -> None:
        # Exercise the public command shape through the module's parser.
        args = ["split", "--input", str(self.source), "--output-dir", str(self.output_dir), "--chunk-bytes", "128"]
        self.assertEqual(bundle_parts.main(args), 0)
        manifest_path = self.output_dir / f"{self.source.name}.parts.json"
        self.assertEqual(bundle_parts.main(["verify", "--manifest", str(manifest_path)]), 0)
        output = self.root / "cli-joined.zip"
        self.assertEqual(bundle_parts.main(["join", "--manifest", str(manifest_path), "--output", str(output)]), 0)
        self.assertEqual(output.read_bytes(), self.payload)


@unittest.skipUnless(shutil.which("powershell.exe") or shutil.which("powershell"), "Windows PowerShell unavailable")
class PowerShellRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "bootstrap.zip"
        self.payload = bytes(range(241)) * 5
        self.source.write_bytes(self.payload)
        self.parts_dir = self.root / "parts"
        self.manifest = bundle_parts.split_file(self.source, self.parts_dir, 173)
        self.powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        self.script = Path(__file__).with_name("Restore-OfflineBundle.ps1")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def restore(self, output: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                self.powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script),
                "-ManifestPath",
                str(self.manifest),
                "-OutputFile",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_powershell_restores_valid_bundle(self) -> None:
        output = self.root / "restored.zip"
        result = self.restore(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), self.payload)

    def test_powershell_rejects_corrupt_part_and_cleans_partial(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        part = self.parts_dir / manifest["parts"][0]["name"]
        part.write_bytes(b"corrupt")
        output = self.root / "bad.zip"
        result = self.restore(output)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".*.partial")), [])

    def test_powershell_rejects_missing_part(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        (self.parts_dir / manifest["parts"][-1]["name"]).unlink()
        result = self.restore(self.root / "missing.zip")
        self.assertNotEqual(result.returncode, 0)

    def test_powershell_refuses_existing_output(self) -> None:
        output = self.root / "existing.zip"
        output.write_bytes(b"keep")
        result = self.restore(output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
