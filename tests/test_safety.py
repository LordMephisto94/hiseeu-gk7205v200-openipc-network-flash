"""Offline rejection tests; fixtures are not flashable firmware."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package_safety


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "package.zip"
        self.desc = {
            "Hardware": "IPC_GK7205V200_G5C-LQ_S38",
            "DevID": "000659O61001000000000200",
            "CRC": "test-checksum",
            "UpgradeCommand": [
                {"Command": "Burn", "FileName": n} for n in package_safety.RANGES
            ],
        }

    def write_package(self, missing=None, duplicate=None):
        with zipfile.ZipFile(self.path, "w") as archive:
            for name in package_safety.RANGES:
                if name != missing:
                    archive.writestr(name, b"x" * 128)
            if missing != "InstallDesc":
                archive.writestr("InstallDesc", json.dumps(self.desc))
            if duplicate:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr(duplicate, b"duplicate")

    def test_missing_and_duplicate_entries(self):
        for missing in ["InstallDesc", *package_safety.RANGES]:
            with self.subTest(missing=missing):
                self.write_package(missing=missing)
                with self.assertRaises(ValueError):
                    package_safety.check_package(self.path)
        self.write_package(duplicate="InstallDesc")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            package_safety.check_package(self.path)

    def test_unsupported_target(self):
        self.desc["Hardware"] = "different-camera"
        self.write_package()
        with self.assertRaisesRegex(ValueError, "Hardware"):
            package_safety.check_package(self.path)

    def test_wrong_order(self):
        self.desc["UpgradeCommand"].reverse()
        self.write_package()
        with self.assertRaisesRegex(ValueError, "env last"):
            package_safety.check_package(self.path)

    def test_invalid_wrapper(self):
        self.write_package()
        with self.assertRaises(SystemExit):
            package_safety.check_package(self.path)

    def test_range_and_checksum_checks(self):
        self.write_package()

        def header(blob, name):
            start, end = package_safety.RANGES[name]
            return {"load": start, "entry": end, "size": 64}

        with patch.object(package_safety, "validate_uimage", side_effect=header):
            with patch.object(package_safety, "package_crc", return_value=("wrong",)):
                with self.assertRaisesRegex(ValueError, "CRC mismatch"):
                    package_safety.check_package(self.path)
            with patch.object(package_safety, "package_crc", return_value=("test-checksum",)):
                self.assertEqual(package_safety.check_package(self.path), self.desc)
        with patch.object(package_safety, "validate_uimage", return_value={
            "load": 0, "entry": 0x800000, "size": 64
        }):
            with self.assertRaisesRegex(ValueError, "write range"):
                package_safety.check_package(self.path)

    def test_same_output_preserves_input(self):
        self.write_package()
        original = self.path.read_bytes()
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts/reorder_xm_env_last.py"),
            str(self.path), str(self.path),
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.path.read_bytes(), original)

    def test_reorder_preserves_source_and_payloads(self):
        self.desc["UpgradeCommand"].reverse()
        self.write_package()
        original = self.path.read_bytes()
        output = self.path.with_name("reordered.zip")
        subprocess.run([
            sys.executable, str(ROOT / "scripts/reorder_xm_env_last.py"),
            str(self.path), str(output),
        ], check=True, capture_output=True)
        self.assertEqual(self.path.read_bytes(), original)
        with zipfile.ZipFile(output) as archive:
            desc = json.loads(archive.read("InstallDesc"))
            self.assertEqual([c["FileName"] for c in desc["UpgradeCommand"]],
                             list(package_safety.RANGES))
            for name in package_safety.RANGES:
                self.assertEqual(archive.read(name), b"x" * 128)

    def test_cli_help_and_explicit_target(self):
        script = str(ROOT / "scripts/xm_upgrade.py")
        result = subprocess.run([sys.executable, "-S", script, "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([
            sys.executable, "-S", script, "missing.bin", "--flash", "--yes"
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--host is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
