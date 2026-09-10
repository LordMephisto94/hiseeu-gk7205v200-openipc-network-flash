"""Offline rejection tests; fixtures are not flashable firmware."""

import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package_safety
import xm_crc_custom
import xm_upgrade


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

    @staticmethod
    def make_uimage(payload, start, end, name):
        crc = lambda data: zlib.crc32(data) & 0xFFFFFFFF
        name_field = name.encode() + b"\0" * (32 - len(name))
        values = (0x27051956, 0, 0, len(payload), start, end,
                  crc(payload), 5, 2, 2, 0, name_field)
        header = struct.pack(">7I4B32s", *values)
        values = (0x27051956, crc(header), 0, len(payload), start, end,
                  crc(payload), 5, 2, 2, 0, name_field)
        return struct.pack(">7I4B32s", *values) + payload

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

    def test_zip_crc_is_checked_before_payload_validation(self):
        self.write_package()
        with zipfile.ZipFile(self.path) as archive:
            info = archive.getinfo("openipc-kernel.img")
            data_offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        data = bytearray(self.path.read_bytes())
        data[data_offset] ^= 0x01
        self.path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "ZIP CRC failure"):
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

    def test_crc_golden_vector(self):
        build = Path(self.temp.name) / "build"
        build.mkdir()
        for i, name in enumerate(package_safety.RANGES):
            (build / name).write_bytes(
                bytes((j + i) % 256 for j in range(257 + i * 11))
            )
        crc, values, total, product = xm_crc_custom.package_crc(
            build, package_safety.RANGES
        )
        self.assertEqual(crc, "935000522144")
        self.assertEqual(total, 19350)
        self.assertEqual(product, 522144)
        self.assertEqual(len(values), 4)

    def test_valid_package_passes_real_checks(self):
        build = Path(self.temp.name) / "build"
        build.mkdir()
        images = {}
        for i, (name, (start, end)) in enumerate(package_safety.RANGES.items()):
            images[name] = self.make_uimage(
                bytes([i]) * 10000, start, end, name
            )
            (build / name).write_bytes(images[name])
        self.desc["CRC"] = xm_crc_custom.package_crc(
            build, package_safety.RANGES
        )[0]
        with zipfile.ZipFile(self.path, "w") as archive:
            for name, blob in images.items():
                archive.writestr(name, blob)
            archive.writestr("InstallDesc", json.dumps(self.desc))
        self.assertEqual(
            package_safety.check_package(self.path)["CRC"],
            "540201550400",
        )

    def test_remote_identity_must_match(self):
        class Camera:
            def __init__(self, info):
                self.info = info

            def get_upgrade_info(self):
                return self.info

        descriptor = {
            "Hardware": "IPC_GK7205V200_G5C-LQ_S38",
            "DevID": "000659O61001000000000200",
        }
        xm_upgrade.verify_target(Camera(descriptor), descriptor)
        xm_upgrade.verify_target(
            Camera({"Hardware": descriptor["Hardware"]}), descriptor
        )
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            xm_upgrade.verify_target(
                Camera({"Hardware": "other", "DevID": descriptor["DevID"]}),
                descriptor,
            )

    def test_progress_accepts_unframed_stock_json(self):
        class Socket:
            def __init__(self, data):
                self.data = bytearray(data)
                self.timeout = None

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.timeout = value

            def recv(self, count):
                if not self.data:
                    return b""
                result = bytes(self.data[:count])
                del self.data[:count]
                return result

        payload = b'{"Name":"OPSystemUpgrade","Ret":53}' + b"\x00"
        frame = xm_upgrade.recv_frame(
            Socket(payload), timeout=1.0, allow_raw_json=True
        )
        self.assertEqual(frame["msgid"], xm_upgrade.MSG_UPGRADE_PROGRESS)
        self.assertTrue(frame["raw_json"])
        self.assertEqual(xm_upgrade.decode_json(frame["body"])["Ret"], 53)


if __name__ == "__main__":
    unittest.main()
