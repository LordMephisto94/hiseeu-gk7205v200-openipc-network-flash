#!/usr/bin/env python3
import argparse
import json
import zipfile
from pathlib import Path

PREFERRED = [
    "openipc-kernel.img",
    "openipc-rootfs.img",
    "openipc-rootfs-data.img",
    "u-boot.env.img",
]


def main():
    ap = argparse.ArgumentParser(
        description="Rewrite an XM firmware ZIP so OpenIPC payloads burn first and U-Boot env burns last."
    )
    ap.add_argument("input", type=Path)
    ap.add_argument(
        "output", nargs="?", type=Path,
        help="Output file (default: <input stem>_env-last.bin)",
    )
    args = ap.parse_args()

    src = args.input
    if args.output is None:
        args.output = src.with_name(src.stem + "_env-last" + src.suffix)
    dst = args.output

    with zipfile.ZipFile(src, "r") as zin:
        names = zin.namelist()
        if "InstallDesc" not in names:
            raise SystemExit("InstallDesc not found")

        desc = json.loads(zin.read("InstallDesc").decode("utf-8"))
        cmds = desc.get("UpgradeCommand")
        if not isinstance(cmds, list):
            raise SystemExit("InstallDesc.UpgradeCommand is not a list")

        burn_by_name = {}
        other = []
        for cmd in cmds:
            if isinstance(cmd, dict) and cmd.get("Command") in ("Burn", "BurnAll"):
                burn_by_name[cmd.get("FileName")] = cmd
            else:
                other.append(cmd)

        missing = [name for name in PREFERRED if name not in burn_by_name]
        if missing:
            raise SystemExit(f"Missing expected Burn entries: {missing}")

        extra_burn = [
            cmd for name, cmd in burn_by_name.items() if name not in PREFERRED
        ]
        if extra_burn:
            raise SystemExit(
                "Unexpected Burn entries present; refusing to guess order: "
                + ", ".join(str(x.get("FileName")) for x in extra_burn)
            )

        desc["UpgradeCommand"] = [burn_by_name[name] for name in PREFERRED] + other
        new_desc = json.dumps(desc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        with zipfile.ZipFile(dst, "w") as zout:
            for info in zin.infolist():
                data = new_desc if info.filename == "InstallDesc" else zin.read(info.filename)
                new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.comment = info.comment
                new_info.extra = info.extra
                new_info.internal_attr = info.internal_attr
                new_info.external_attr = info.external_attr
                new_info.create_system = info.create_system
                new_info.flag_bits = info.flag_bits
                zout.writestr(new_info, data)

    with zipfile.ZipFile(dst, "r") as z:
        bad = z.testzip()
        if bad:
            raise SystemExit(f"ZIP validation failed at {bad}")
        check = json.loads(z.read("InstallDesc").decode("utf-8"))
        print(f"Output: {dst}")
        print(f"CRC: {check.get('CRC')}")
        print("UpgradeCommand:")
        for cmd in check["UpgradeCommand"]:
            print(f"  {cmd.get('Command','?'):7s} {cmd.get('FileName','')}")
        print("ZIP: OK")


if __name__ == "__main__":
    main()
