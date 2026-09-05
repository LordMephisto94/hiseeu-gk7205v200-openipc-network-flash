"""Offline, fail-closed checks for the one documented conversion target."""

import json
import tempfile
import zipfile
from pathlib import Path

from build_xm_openipc import validate_uimage
from xm_crc_custom import package_crc

RANGES = {
    "openipc-kernel.img": (0x050000, 0x250000),
    "openipc-rootfs.img": (0x250000, 0x750000),
    "openipc-rootfs-data.img": (0x750000, 0x7B0000),
    "u-boot.env.img": (0x030000, 0x040000),
}


def check_package(path):
    """Reject ambiguous, incomplete, corrupted, or out-of-scope packages."""
    def refuse(reason):
        raise ValueError(f"Refusing package: {reason}")

    with zipfile.ZipFile(path) as archive:
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
        if desc.get("Hardware") != "IPC_GK7205V200_G5C-LQ_S38":
            refuse("unsupported Hardware")
        if desc.get("DevID") != "000659O61001000000000200":
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
