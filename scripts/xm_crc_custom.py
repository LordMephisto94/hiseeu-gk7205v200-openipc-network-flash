#!/usr/bin/env python3
"""
xm_crc_custom.py

Calculate the XM package CRC for generated OpenIPC burn images and emit a
stock-shaped InstallDesc. OFFLINE ONLY.

This uses the stock-tested checksum behavior reconstructed from userfs/bin/App.
The only effective unknown from the two caller strings is (B+C) mod 8, which
the stock known-good firmware proves is 6.
"""
import argparse
import json
from pathlib import Path

EFFECTIVE_BC_MOD8 = 6
PRODUCT_MOD = 10_000_000


def bsum(x):
    if isinstance(x, str):
        x = x.encode("utf-8")
    return sum(x)


def per_file(path: Path, filename: str):
    data = path.read_bytes()
    if not data:
        return 0

    A = bsum(filename)
    bc = EFFECTIVE_BC_MOD8
    result = bsum(data[:28])
    size = len(data)

    position = (size // 10) * (((A * A + bc) % 8) + 2)
    step = (size // 50) * (((A + bc) % 8) + 2)

    if step <= 9:
        raise RuntimeError(
            f"{filename}: unexpected tiny step; literal C would be required"
        )

    position %= size
    for _ in range(36):
        result += data[position]
        position = (position + step) % size

    return result


def package_crc(build_dir: Path, filenames):
    total = 0
    product = 1
    vals = []

    for name in filenames:
        p = build_dir / name
        if not p.is_file():
            raise FileNotFoundError(p)
        n = per_file(p, name)
        vals.append((name, n))
        total += n
        if n > 0:
            product = (product * ((n % 98) + 2)) % PRODUCT_MOD

    crc = f"{total % 10000:04d}{product % 100_000_000:08d}"
    return crc, vals, total, product


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", default="openipc-xm-build")
    ap.add_argument("--stock-install-desc", default="InstallDesc")
    ap.add_argument("--out", default=None,
                    help="output InstallDesc path (default: BUILD/InstallDesc.generated)")
    args = ap.parse_args()

    build = Path(args.build_dir)
    stock = Path(args.stock_install_desc)
    if not stock.is_file():
        raise SystemExit(f"Missing stock InstallDesc: {stock}")

    desc = json.loads(stock.read_text())
    burn_files = [
        "u-boot.env.img",
        "openipc-kernel.img",
        "openipc-rootfs.img",
        "openipc-rootfs-data.img",
    ]

    crc, vals, total, product = package_crc(build, burn_files)

    print("=== CUSTOM OPENIPC PACKAGE CRC ===")
    for name, n in vals:
        print(f"{name:28s} n={n}")
    print(f"sum={total}")
    print(f"product={product}")
    print(f"CRC={crc}")
    print()

    new_desc = {
        "UpgradeCommand": [
            {"Command": "Burn", "FileName": x}
            for x in burn_files
        ],
        "Hardware": desc.get("Hardware"),
        "SupportFlashType": desc.get("SupportFlashType"),
        "DevID": desc.get("DevID"),
        "Vendor": desc.get("Vendor"),
        "CompatibleVersion": desc.get("CompatibleVersion"),
        "CRC": crc,
        # On the tested stock firmware the public-key file used by the Mx8Q
        # verifier is absent, so verification is skipped after the field parses.
        # Preserve the stock value syntactically; do not assume this applies to
        # other XM firmware revisions.
        "Mx8Q": desc.get("Mx8Q"),
    }

    out = Path(args.out) if args.out else build / "InstallDesc.generated"
    out.write_text(json.dumps(new_desc, indent=8) + "\n")
    print("Generated:", out)


if __name__ == "__main__":
    main()
