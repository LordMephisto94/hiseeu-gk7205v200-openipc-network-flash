#!/usr/bin/env python3
"""
build_xm_openipc.py

Offline builder for an XM GK7205V200 -> OpenIPC conversion package payloads.

Run this from the extracted XM firmware directory containing:
  u-boot.env.img
  romfs-x.squashfs.img
  user-x.squashfs.img

and point it at:
  uImage.gk7205v200
  rootfs.squashfs.gk7205v200

It DOES NOT communicate with the camera or flash anything.
"""

import argparse
import binascii
import os
import struct
import sys
import zlib
from pathlib import Path

UIMAGE_MAGIC = 0x27051956
UIMAGE_HDR_SIZE = 64

# Conservative layout for this exact 8 MiB XM camera:
ENV_START    = 0x00030000
ENV_END      = 0x00040000

# 0x40000-0x50000 intentionally left unused/reserved
KERNEL_START = 0x00050000
KERNEL_END   = 0x00250000

ROOTFS_START = 0x00250000
ROOTFS_END   = 0x00750000

ROOTFS_DATA_START = 0x00750000
ROOTFS_DATA_END   = 0x007B0000

# Preserve XM factory/config region.
XM_MTD5_START = 0x007B0000
FLASH_END     = 0x00800000


def die(msg):
    raise SystemExit(f"ERROR: {msg}")


def read_file(path):
    p = Path(path)
    if not p.is_file():
        die(f"missing file: {p}")
    return p.read_bytes()


def parse_uimage_header(blob):
    if len(blob) < 64:
        die("file too small for uImage header")

    fields = struct.unpack(">7I4B32s", blob[:64])
    hdr = {
        "magic": fields[0],
        "hcrc": fields[1],
        "time": fields[2],
        "size": fields[3],
        "load": fields[4],
        "entry": fields[5],
        "dcrc": fields[6],
        "os": fields[7],
        "arch": fields[8],
        "type": fields[9],
        "comp": fields[10],
        "name": fields[11].split(b"\0", 1)[0].decode("ascii", "replace"),
    }
    if hdr["magic"] != UIMAGE_MAGIC:
        die(f"not a legacy uImage (magic 0x{hdr['magic']:08x})")
    return hdr


def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def validate_uimage(blob, label):
    hdr = parse_uimage_header(blob)
    header = bytearray(blob[:64])
    struct.pack_into(">I", header, 4, 0)
    calc_hcrc = crc32(header)

    payload = blob[64:64 + hdr["size"]]
    calc_dcrc = crc32(payload)

    if len(payload) != hdr["size"]:
        die(f"{label}: truncated uImage payload")

    if calc_hcrc != hdr["hcrc"]:
        die(
            f"{label}: bad header CRC: stored=0x{hdr['hcrc']:08x} "
            f"calc=0x{calc_hcrc:08x}"
        )
    if calc_dcrc != hdr["dcrc"]:
        die(
            f"{label}: bad data CRC: stored=0x{hdr['dcrc']:08x} "
            f"calc=0x{calc_dcrc:08x}"
        )
    return hdr


def make_xm_wrapper(template_blob, payload, flash_start, flash_end, name):
    """
    Clone the XM stock uImage wrapper header, replacing only:
      timestamp: preserved
      payload size
      load address  -> XM flash start
      entry point   -> XM flash end
      data CRC
      name
      header CRC

    OS / architecture / image type / compression remain exactly as
    they were in the selected stock XM template.
    """
    if len(template_blob) < 64:
        die("XM template has no complete uImage header")
    template = parse_uimage_header(template_blob)

    if flash_end <= flash_start:
        die("invalid flash range")

    capacity = flash_end - flash_start
    if len(payload) > capacity:
        die(
            f"{name}: payload 0x{len(payload):x} bytes exceeds "
            f"range capacity 0x{capacity:x}"
        )

    name_bytes = name.encode("ascii")
    if len(name_bytes) > 31:
        die("uImage name must fit in 31 characters plus NUL")
    name_field = name_bytes + b"\0" * (32 - len(name_bytes))

    dcrc = crc32(payload)

    header = struct.pack(
        ">7I4B32s",
        UIMAGE_MAGIC,
        0,                    # header CRC, filled below
        template["time"],     # preserve XM timestamp
        len(payload),
        flash_start,
        flash_end,
        dcrc,
        template["os"],
        template["arch"],
        template["type"],
        template["comp"],
        name_field,
    )
    hcrc = crc32(header)

    header = struct.pack(
        ">7I4B32s",
        UIMAGE_MAGIC,
        hcrc,
        template["time"],
        len(payload),
        flash_start,
        flash_end,
        dcrc,
        template["os"],
        template["arch"],
        template["type"],
        template["comp"],
        name_field,
    )
    return header + payload


def parse_env(raw):
    if len(raw) != 65536:
        die(f"expected 65536-byte U-Boot env, got {len(raw)}")

    stored = int.from_bytes(raw[:4], "little")
    calc = crc32(raw[4:])
    if stored != calc:
        die(
            f"stock U-Boot env CRC mismatch: stored=0x{stored:08x}, "
            f"calc=0x{calc:08x}"
        )

    body = raw[4:]
    env = {}
    order = []

    # Environment terminates with double NUL; split individual variables.
    for item in body.split(b"\0"):
        if not item:
            continue
        try:
            text = item.decode("ascii")
        except UnicodeDecodeError:
            continue
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key not in env:
            order.append(key)
        env[key] = value

    return env, order


def build_env(stock_env_img):
    stock_hdr = validate_uimage(stock_env_img, "stock u-boot.env.img")
    if stock_hdr["size"] != 65536:
        die(
            f"stock env wrapper payload should be 65536 bytes, "
            f"got {stock_hdr['size']}"
        )

    raw = stock_env_img[64:64 + stock_hdr["size"]]
    env, order = parse_env(raw)

    # OpenIPC's current documented 8 MiB NOR layout for Goke:
    # 256k boot, 64k env, 2048k kernel, 5120k rootfs, remainder rootfs_data.
    #
    # IMPORTANT: the physical U-Boot environment for this XM bootloader
    # remains at 0x30000-0x40000. The mtdparts string describes Linux-visible
    # partitions starting with a 256k "boot" region and a 64k "env" region.
    # We intentionally DO NOT use OpenIPC's generic
    #   256k(boot),64k(env),2048k(kernel),5120k(rootfs),-(rootfs_data)
    # map on this camera, because:
    #   * this XM U-Boot's real environment is physically at 0x30000;
    #   * 0x40000-0x50000 is kept as a 64 KiB gap;
    #   * 0x7B0000-0x800000 is XM factory/config data that must be preserved.
    #
    # Exact 8 MiB map:
    #   192k boot
    #    64k env
    #    64k reserved
    #  2048k kernel
    #  5120k rootfs
    #   384k rootfs_data
    #   320k factory
    #
    # Therefore rootfs is mtd4.
    env["bootargs"] = (
        "mem=${osmem} console=ttyAMA0,115200 panic=20 "
        "root=/dev/mtdblock4 rootfstype=squashfs init=/init "
        "mtdparts=sfc:192k(boot),64k(env),64k(reserved),"
        "2048k(kernel),5120k(rootfs),384k(rootfs_data),320k(factory)"
    )

    env["bootcmd"] = (
        "setenv setargs setenv bootargs ${bootargs}; "
        "run setargs; "
        "sf probe 0; "
        "sf read 0x42000000 0x50000 0x200000; "
        "bootm 0x42000000"
    )

    # Keep the camera's existing osmem if present. Stock dump showed 39M.
    if "osmem" not in env:
        env["osmem"] = "39M"
        order.append("osmem")

    for key in ("bootargs", "bootcmd"):
        if key not in order:
            order.append(key)

    # Preserve all stock variables and their original ordering where possible.
    encoded = b""
    for key in order:
        if key in env:
            encoded += f"{key}={env[key]}".encode("ascii") + b"\0"

    encoded += b"\0"

    # 65536 total including 4-byte CRC.
    max_body = 65536 - 4
    if len(encoded) > max_body:
        die("replacement environment is too large")

    body = encoded + b"\0" * (max_body - len(encoded))
    env_crc = crc32(body)
    new_raw = env_crc.to_bytes(4, "little") + body

    # Wrap with the XM env header and original flash range.
    wrapped = make_xm_wrapper(
        stock_env_img,
        new_raw,
        ENV_START,
        ENV_END,
        "env",
    )

    return wrapped, new_raw, env


def range_check(name, start, end):
    if start < 0 or end > FLASH_END or end <= start:
        die(f"{name}: invalid flash interval 0x{start:x}-0x{end:x}")
    if start < XM_MTD5_START < end or start >= XM_MTD5_START:
        die(
            f"{name}: would touch protected XM mtd5 "
            f"(0x{XM_MTD5_START:06x}-0x{FLASH_END:06x})"
        )


def print_wrapper_summary(label, blob):
    hdr = validate_uimage(blob, label)
    print(
        f"{label:24s} "
        f"payload={hdr['size']:8d} "
        f"flash=0x{hdr['load']:06x}-0x{hdr['entry']:06x} "
        f"name={hdr['name']!r} "
        f"hcrc=0x{hdr['hcrc']:08x} dcrc=0x{hdr['dcrc']:08x}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--firmware-dir",
        default=".",
        help="directory containing extracted stock XM *.img files",
    )
    ap.add_argument("--kernel", required=True, help="OpenIPC uImage.gk7205v200")
    ap.add_argument("--rootfs", required=True, help="OpenIPC rootfs.squashfs.gk7205v200")
    ap.add_argument("--out", default="openipc-xm-build", help="output directory")
    args = ap.parse_args()

    fw = Path(args.firmware_dir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    stock_env = read_file(fw / "u-boot.env.img")
    stock_romfs = read_file(fw / "romfs-x.squashfs.img")
    stock_user = read_file(fw / "user-x.squashfs.img")

    kernel = read_file(args.kernel)
    rootfs = read_file(args.rootfs)

    # Validate the OpenIPC kernel's *inner* standard uImage before wrapping it.
    inner_kernel_hdr = validate_uimage(kernel, "OpenIPC kernel")

    if rootfs[:4] != b"hsqs":
        die(
            "OpenIPC rootfs does not start with little-endian SquashFS magic 'hsqs'"
        )

    range_check("env", ENV_START, ENV_END)
    range_check("kernel", KERNEL_START, KERNEL_END)
    range_check("rootfs", ROOTFS_START, ROOTFS_END)
    range_check("rootfs_data", ROOTFS_DATA_START, ROOTFS_DATA_END)

    new_env_img, new_env_raw, env = build_env(stock_env)

    # Clone a stock filesystem-wrapper header for both payloads.
    # This preserves XM-specific uImage type/compression fields.
    kernel_img = make_xm_wrapper(
        stock_romfs,
        kernel,
        KERNEL_START,
        KERNEL_END,
        "kernel",
    )
    rootfs_img = make_xm_wrapper(
        stock_user,
        rootfs,
        ROOTFS_START,
        ROOTFS_END,
        "rootfs",
    )

    # OpenIPC uses this area as writable rootfs_data. On the stock XM layout
    # it contains old usr/web/custom bytes, so initialise it to an erased-flash
    # state rather than exposing stale XM filesystem data to OpenIPC.
    rootfs_data_payload = b"\xff" * (ROOTFS_DATA_END - ROOTFS_DATA_START)
    rootfs_data_img = make_xm_wrapper(
        stock_user,
        rootfs_data_payload,
        ROOTFS_DATA_START,
        ROOTFS_DATA_END,
        "rootfs_data",
    )

    (out / "u-boot.env.img").write_bytes(new_env_img)
    (out / "openipc-kernel.img").write_bytes(kernel_img)
    (out / "openipc-rootfs.img").write_bytes(rootfs_img)
    (out / "openipc-rootfs-data.img").write_bytes(rootfs_data_img)
    (out / "u-boot.env.raw").write_bytes(new_env_raw)

    install_desc_stub = """{
    "UpgradeCommand": [
        {
            "Command": "Burn",
            "FileName": "u-boot.env.img"
        },
        {
            "Command": "Burn",
            "FileName": "openipc-kernel.img"
        },
        {
            "Command": "Burn",
            "FileName": "openipc-rootfs.img"
        },
        {
            "Command": "Burn",
            "FileName": "openipc-rootfs-data.img"
        }
    ]
}
"""
    (out / "InstallDesc.stub").write_text(install_desc_stub)

    print()
    print("=== INPUT ===")
    print(
        f"OpenIPC inner kernel: size={len(kernel)} "
        f"load=0x{inner_kernel_hdr['load']:08x} "
        f"entry=0x{inner_kernel_hdr['entry']:08x} "
        f"name={inner_kernel_hdr['name']!r}"
    )
    print(f"OpenIPC rootfs:       size={len(rootfs)} bytes, magic=hsqs")
    print()

    print("=== GENERATED XM BURN IMAGES ===")
    print_wrapper_summary("u-boot.env.img", new_env_img)
    print_wrapper_summary("openipc-kernel.img", kernel_img)
    print_wrapper_summary("openipc-rootfs.img", rootfs_img)
    print_wrapper_summary("openipc-rootfs-data.img", rootfs_data_img)
    print()

    print("=== FLASH MAP ===")
    print("0x000000-0x030000  stock XM U-Boot       UNTOUCHED")
    print("0x030000-0x040000  replacement env")
    print("0x040000-0x050000  reserved / untouched")
    print("0x050000-0x250000  OpenIPC kernel")
    print("0x250000-0x750000  OpenIPC rootfs")
    print("0x750000-0x7B0000  rootfs_data (initialised to erased state)")
    print("0x7B0000-0x800000  XM mtd5               UNTOUCHED")
    print()

    print("=== REPLACEMENT BOOT SETTINGS ===")
    print("bootargs =", env["bootargs"])
    print("bootcmd  =", env["bootcmd"])
    print("osmem    =", env.get("osmem", "<missing>"))
    print()

    print("Outputs written to:", out.resolve())
    print()
    print("NOTE: InstallDesc.stub intentionally has NO CRC/Mx8Q fields yet.")
    print("Do not upload it to the camera. This builder is offline-only.")


if __name__ == "__main__":
    main()
