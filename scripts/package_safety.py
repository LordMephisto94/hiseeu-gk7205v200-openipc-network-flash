"""Offline, fail-closed checks for the one documented conversion target."""

import json
import tempfile
import zipfile
from pathlib import Path

from board_profile import RANGES, TARGET_DEVID, TARGET_HARDWARE
from build_xm_openipc import validate_uimage
from xm_crc_custom import package_crc

MAX_PACKAGE_BYTES = 16 * 1024 * 1024


def check_package(path):
    """Reject ambiguous, incomplete, corrupted, or out-of-scope packages."""
    def refuse(reason):
        raise ValueError(f"Refusing package: {reason}")

    package_path = Path(path)
    if package_path.stat().st_size > MAX_PACKAGE_BYTES:
        refuse(f"package is larger than {MAX_PACKAGE_BYTES} bytes")

    with zipfile.ZipFile(package_path) as archive:
        try:
            bad = archive.testzip()
        except zipfile.BadZipFile as exc:
            refuse(f"invalid ZIP data: {exc}")
        if bad:
            refuse(f"ZIP CRC failure: {bad}")
        names = archive.namelist()
        if len(names) != len(set(names)):
            refuse("duplicate ZIP entries")
        if set(names) != set(RANGES) | {"InstallDesc"}:
            refuse("expected exactly four payloads and InstallDesc")
        for info in archive.infolist():
            limit = 65536 if info.filename == "InstallDesc" else (
                RANGES[info.filename][1] - RANGES[info.filename][0] + 64
            )
            if info.file_size > limit:
                refuse(f"oversized entry: {info.filename}")
        desc = json.loads(archive.read("InstallDesc"))
        if not isinstance(desc, dict):
            refuse("InstallDesc must be an object")
        if desc.get("Hardware") != TARGET_HARDWARE:
            refuse("unsupported Hardware")
        if desc.get("DevID") != TARGET_DEVID:
            refuse("unsupported DevID")
        expected = [{"Command": "Burn", "FileName": name} for name in RANGES]
        if desc.get("UpgradeCommand") != expected:
            refuse("commands must contain exactly the documented burns, env last")
        with tempfile.TemporaryDirectory(prefix="xm-validate-") as directory:
            build = Path(directory)
            for name, (start, end) in RANGES.items():
                blob = archive.read(name)
                header = validate_uimage(blob, name)
                if (header["load"], header["entry"]) != (start, end):
                    refuse(f"unexpected write range: {name}")
                if not 0 < header["size"] <= end - start:
                    refuse(f"invalid payload size: {name}")
                if len(blob) != header["size"] + 64:
                    refuse(f"unexpected trailing bytes: {name}")
                (build / name).write_bytes(blob)
            calculated, *_ = package_crc(build, RANGES)
        if desc.get("CRC") != calculated:
            refuse("custom package CRC mismatch")
        return desc
