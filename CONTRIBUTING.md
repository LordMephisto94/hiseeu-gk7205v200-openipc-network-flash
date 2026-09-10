# Contributing

Thanks for your interest in improving this project.

This repository documents a **real, tested network-only OpenIPC conversion** for one specific Hiseeu/Xiongmai camera revision. Contributions are welcome, but because firmware flashing can permanently brick hardware, changes should be evidence-driven and conservative.

## What contributions are useful

Good contributions include:

- confirming the guide on another camera with the **same hardware ID**
- documenting another verified Hiseeu/XM hardware revision
- improving safety checks or validation
- fixing script bugs
- improving Fish/CachyOS instructions
- adding packet-capture or recovery notes
- documenting GPIO, sensor, audio, PTZ, or U-Boot behaviour that has been physically verified
- correcting typos or improving the README without changing technical meaning

## Hardware verification

If you are reporting compatibility with another camera, please include as much of the following as possible:

```text
Brand/model:
XM Hardware ID:
SoC:
Sensor:
SPI flash chip:
Flash size:
Stock firmware version:
Stock /proc/mtd:
OpenIPC build used:
Result:
```

Do **not** report a camera as compatible based only on enclosure appearance or a reseller listing.

## Flash-layout changes

Any pull request that changes flash offsets, partition sizes, U-Boot environment values, package burn order, or protected regions should include evidence showing why the change is correct.

For this tested revision, the following areas are particularly important:

```text
0x000000-0x030000  stock XM U-Boot during initial conversion
0x030000-0x040000  U-Boot environment
0x040000-0x050000  reserved during initial conversion
0x050000-0x250000  OpenIPC kernel
0x250000-0x750000  OpenIPC rootfs
0x750000-0x7B0000  OpenIPC rootfs_data
0x7B0000-0x800000  factory/persistent data — DO NOT OVERWRITE
```

The successful package intentionally burns:

```text
kernel
rootfs
rootfs_data
env
```

with the U-Boot environment **last**.

Please do not casually reorder this or widen any write range.

## Script changes

For Python changes:

- keep Python 3 compatibility
- avoid embedding passwords or CheckCodes
- keep destructive operations opt-in
- retain dry-run behaviour where provided
- validate inputs before writing flash
- keep the board identity and flash map in `scripts/board_profile.py`
- preserve the remote Hardware/DevID preflight before a flash starts
- prefer explicit failure over guessing
- preserve the final DVRIP firmware chunk `end_flag=1`
- do not remove package/ZIP validation without a strong reason

Before opening a pull request, run:

```sh
python -m py_compile scripts/*.py
python -m unittest discover -s tests -v
sha256sum -c SHA256SUMS
```

If you modify the XM package builder or CRC logic, include the dry-run output and explain what changed.

## XM CheckCode / NetIPTelnet research

The CheckCode is dynamic and is recovered from the camera's own encrypted UDP/30000 cloud traffic.

Please do not commit:

- camera passwords
- private network credentials
- live session IDs if they contain anything sensitive
- captures containing unrelated private network traffic

Sanitize packet captures before attaching them publicly when possible.

## Testing firmware changes

If a contribution involves actually flashing a camera, please state clearly whether it was:

- **offline-only / untested**
- **dry-run validated**
- **tested on hardware**
- **tested on multiple hardware revisions**

Do not describe reconstructed or theoretical steps as tested.

## Pull requests

A useful pull request should explain:

1. what changed
2. why it changed
3. what hardware/firmware it was tested on
4. whether any flash layout or destructive behaviour changed
5. how the result was verified

Small focused pull requests are preferred over large unrelated rewrites.

## Issues

When reporting a problem, include:

- the exact camera hardware ID if known
- stock firmware version
- OpenIPC build/version
- command used
- complete error output
- whether the camera still boots
- relevant `/proc/mtd`, `/proc/cmdline`, or `fw_printenv` output

Please redact passwords and other secrets.

## Licensing

By contributing to this repository, you agree that your contribution may be distributed under the repository's **GNU General Public License v3.0 or later** terms.

See [`LICENSE`](LICENSE).

When changing a file listed in `SHA256SUMS`, regenerate its hash. Include new Python helpers in the manifest. CI checks the manifest as well as offline safety tests. Do not run hardware operations in CI.
