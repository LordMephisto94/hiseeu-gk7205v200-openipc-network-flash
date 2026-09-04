[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

# Flash OpenIPC to the Hiseeu 2MP PTZ PoE Camera Entirely Over Ethernet

**Tested target:** `IPC_GK7205V200_G5C-LQ_S38` — GK7205V200/GK7205V210 family, SC223A, 8 MiB SPI NOR.

This documents the **actual successful network-only conversion** of a Hiseeu/Xiongmai (XM) 2 MP PTZ PoE camera to OpenIPC. The camera was not opened and UART was not used for the installation.

The path that actually worked was:

```text
stock XM firmware
      ↓
enable Cloud and capture the camera's outbound UDP/30000 packet
      ↓
extract the rotating NetIPTelnet CheckCode
      ↓
DVRIP login + NetIPTelnet diagnostic shell
      ↓
verify exact hardware/flash layout
      ↓
build XM-wrapped OpenIPC kernel/rootfs/env/rootfs_data images
      ↓
recalculate XM's package CRC and build InstallDesc
      ↓
package as an XM .bin (ZIP)
      ↓
reorder burns: kernel → rootfs → rootfs_data → env
      ↓
upload over DVRIP/TCP 34567
      ↓
XM updater writes SPI NOR and reboots
      ↓
OpenIPC boots via the original XM U-Boot
```

> [!CAUTION]
> This is hardware-specific firmware flashing. A mistake can brick the camera. A 3.3 V UART adapter and/or SPI programmer is strongly recommended as a recovery option even though neither was needed in the successful install.

> [!IMPORTANT]
> Do **not** use this guide solely because your camera enclosure looks the same. Verify the hardware and stock firmware first.

---

## 1. Known-good hardware and stock firmware

The tested camera reported:

| Item | Tested value |
|---|---|
| XM hardware | `IPC_GK7205V200_G5C-LQ_S38` |
| SoC | Goke GK7205V200 / GK7205V210 family |
| CPU | ARMv7 Cortex-A7 |
| Sensor | SmartSens SC223A |
| Flash | 8 MiB SPI NOR |
| Flash chip | XT25F64B family on tested board |
| Stock flash erase size | 64 KiB |
| XM DVRIP | TCP 34567 |
| Stock firmware | `General_IPC_GK7205V200_G5C-LQ_S38.Nat.dss.OnvifS.HIK_V5.00.R02.20221108_all.bin` |
| Stock DevID | `000659O61001000000000200` |

The stock kernel exposed:

```text
mtd0: 00040000 "boot"
mtd1: 002e0000 "romfs"
mtd2: 00440000 "usr"
mtd3: 00020000 "web"
mtd4: 00030000 "custom"
mtd5: 00050000 "mtd"
```

The stock physical flash map we derived was:

```text
0x000000-0x030000  XM U-Boot
0x030000-0x040000  XM U-Boot environment
0x040000-0x320000  romfs allocation
0x320000-0x760000  usr allocation
0x760000-0x780000  web
0x780000-0x7B0000  custom
0x7B0000-0x800000  persistent/factory region — PRESERVE
```

The stock kernel explicitly identified an 8 MiB NOR device and registered the final `0x7B0000-0x800000` area separately. **Do not overwrite that region.**

---

## 2. Files and tools

This repository contains the helpers used or reconstructed from the successful session:

```text
scripts/
├── xm_checkcode.py
├── netip_shell_dynamic.py
├── build_xm_openipc.py
├── xm_crc_custom.py
├── reorder_xm_env_last.py
├── xm_upgrade.py
├── telnet_shell.py        # historical/diagnostic; not required by main path
└── telnet_opener.py       # historical/diagnostic; not required by main path
```

You also need:

- Python 3
- `openssl`
- `tcpdump` (or a PCAP captured by your router/SPAN port)
- `zip` / `unzip`
- a compatible `dvrip.py` / python-dvr checkout containing `DVRIPCam` and `send_custom(..., version=1)`
- the exact extracted stock XM firmware
- OpenIPC `uImage.gk7205v200`
- OpenIPC `rootfs.squashfs.gk7205v200`

The Python scripts that use DVRIP import:

```python
from dvrip import DVRIPCam
```

If `dvrip.py` is in a separate checkout, use `PYTHONPATH`, for example:

```fish
env PYTHONPATH=$HOME/Documents/python-dvr python scripts/netip_shell_dynamic.py --help
```

---

## 3. Capture the rotating NetIPTelnet CheckCode

### Why this is necessary

XM's `NetIPTelnet` diagnostic endpoint is gated by a rotating 32-bit **CheckCode**. A stale value produces `Ret=107`.

On this firmware the current value is included in encrypted XM cloud traffic sent to **UDP destination port 30000**. `xm_checkcode.py` reconstructs the per-packet AES-128-CBC key and extracts TLV tag:

```text
0x000A0007 = CheckCode
```

### Turn Cloud on first

On the tested stock firmware, the **Cloud** toggle had to be enabled or the UDP/30000 registration/heartbeat packets were not sent.

### Capture at the right point

A normal switched Ethernet PC generally **cannot see the camera's unicast Internet traffic**. Capture at one of:

- your gateway/router (recommended),
- a switch SPAN/mirror destination,
- a Linux bridge physically forwarding the camera's traffic.

Example on a gateway/router:

```sh
tcpdump -ni any -s0 \
  'host CAMERA_IP and udp port 30000' \
  -w /tmp/camera_checkcode.pcap
```

Start the capture, reboot the stock camera (or wait for its cloud registration), allow 30–60 seconds, then stop with `Ctrl+C`.

Sanity-check it:

```sh
tcpdump -nn -r /tmp/camera_checkcode.pcap
```

Copy the PCAP to your workstation if required.

### Extract the CheckCode

```fish
python scripts/xm_checkcode.py camera_checkcode.pcap --camera-ip CAMERA_IP
```

The useful final line is:

```text
Latest CheckCode: 1234567890
```

If no record is found, first confirm Cloud is enabled and that the capture point can actually see outbound camera traffic.

---

## 4. Open the XM NetIPTelnet shell

This is **not normal TCP Telnet**. It is an XM DVRIP diagnostic command facility using message ID `1020` (`NetIPTelnet`).

Run:

```fish
env PYTHONPATH=$HOME/Documents/python-dvr \
  python scripts/netip_shell_dynamic.py \
  --host CAMERA_IP \
  --checkcode CURRENT_CHECKCODE
```

The script prompts for the normal camera admin password.

On success:

```text
Connected to CAMERA_IP
Session: 0x........
CheckCode: ........
xmcam>
```

Start with harmless reconnaissance:

```text
xmcam> cat /proc/mtd
xmcam> cat /proc/cpuinfo
xmcam> dmesg
xmcam> cat /proc/cmdline
```

If `Ret=107` appears, capture a fresh UDP/30000 packet and update the live shell with:

```text
xmcam> :checkcode NEW_VALUE
```

### Stop if your hardware does not match

Do not continue unless you have independently established an 8 MiB GK7205V200-family NOR layout compatible with the offsets below.

---

## 5. Back up the stock firmware/flash

Keep the vendor firmware package and make whatever raw MTD backups your shell/transfer setup permits before flashing.

At an absolute minimum, preserve:

```text
0x000000-0x030000  original XM U-Boot
0x030000-0x040000  original U-Boot environment
0x7B0000-0x800000  factory/persistent data
```

A full 8 MiB raw flash backup should be exactly:

```text
8,388,608 bytes
```

Hash all backups and store copies elsewhere.

---

## 6. Extract the stock XM firmware

The tested `_all.bin` is itself a ZIP archive despite the `.bin` extension.

Extract it:

```fish
mkdir stock-firmware-extracted
unzip General_IPC_GK7205V200_G5C-LQ_S38.Nat.dss.OnvifS.HIK_V5.00.R02.20221108_all.bin \
  -d stock-firmware-extracted
```

The important inputs are:

```text
u-boot.env.img
romfs-x.squashfs.img
user-x.squashfs.img
InstallDesc
```

The builder uses the stock uImage wrappers as templates so XM's own updater receives image headers in the format it already understands.

---

## 7. Obtain the OpenIPC firmware

Use the appropriate current OpenIPC build for:

```text
gk7205v200
NOR
Lite
8 MiB flash
```

You need:

```text
uImage.gk7205v200
rootfs.squashfs.gk7205v200
```

The exact generated package CRC will change when the OpenIPC payloads change, so **do not copy the example CRC from the historical successful build**.

---

## 8. Build XM-compatible OpenIPC burn images

Run the reconstructed final builder:

```fish
python scripts/build_xm_openipc.py \
  --firmware-dir ./stock-firmware-extracted \
  --kernel ./uImage.gk7205v200 \
  --rootfs ./rootfs.squashfs.gk7205v200 \
  --out ./openipc-xm-build
```

The builder validates the OpenIPC kernel uImage, SquashFS magic, XM wrapper CRCs and destination capacities.

It generates:

```text
openipc-xm-build/
├── u-boot.env.img
├── openipc-kernel.img
├── openipc-rootfs.img
├── openipc-rootfs-data.img
├── u-boot.env.raw
└── InstallDesc.stub
```

`InstallDesc.stub` is intentionally incomplete. **Do not upload it.**

### The temporary/hybrid flash layout

The initial conversion deliberately preserves the stock bootloader:

```text
0x000000-0x030000  stock XM U-Boot        UNTOUCHED
0x030000-0x040000  replacement env
0x040000-0x050000  reserved               UNTOUCHED
0x050000-0x250000  OpenIPC kernel
0x250000-0x750000  OpenIPC rootfs
0x750000-0x7B0000  OpenIPC rootfs_data, initialised to 0xFF
0x7B0000-0x800000  factory/config         UNTOUCHED
```

The replacement environment uses:

```text
mem=${osmem}
root=/dev/mtdblock4
rootfstype=squashfs
init=/init
mtdparts=sfc:192k(boot),64k(env),64k(reserved),2048k(kernel),5120k(rootfs),384k(rootfs_data),320k(factory)
```

and boots the kernel with:

```text
sf probe 0
sf read 0x42000000 0x50000 0x200000
bootm 0x42000000
```

The tested stock environment supplied:

```text
osmem=39M
```

### Why `rootfs_data` is explicitly erased

The stock XM layout leaves old `usr/web/custom` bytes in the area OpenIPC will treat as writable `rootfs_data`. The final builder therefore creates a 384 KiB payload filled with `0xFF` and burns it to `0x750000-0x7B0000`.

---

## 9. Generate XM's custom package CRC and final InstallDesc

XM's `CRC` field is **not ZIP CRC32**. The vendor firmware uses its own 12-digit calculation over the `Burn` payloads.

Generate it from the actual OpenIPC images:

```fish
python scripts/xm_crc_custom.py \
  --build-dir ./openipc-xm-build \
  --stock-install-desc ./stock-firmware-extracted/InstallDesc
```

This creates:

```text
openipc-xm-build/InstallDesc.generated
```

The generator preserves the stock:

```text
Hardware
SupportFlashType
DevID
Vendor
CompatibleVersion
Mx8Q
```

and replaces the burn list and `CRC`.

### Important Mx8Q caveat

On the **tested 2022 firmware**, reverse engineering showed that Mx8Q cryptographic verification tries to open:

```text
/usr/bin/Squirrel/rs485/TransparentBase
```

and returns success when that public-key file is absent. The extracted tested firmware did not contain that file, so the original stock Base64 `Mx8Q` value only needed to remain syntactically present.

**Do not assume this bypass applies to another XM firmware revision.** If your firmware actually contains that public-key file, stop here; the copied stock Mx8Q signature will not necessarily validate modified payloads.

---

## 10. Build the XM `.bin` package

Copy the generated descriptor into the build directory:

```fish
cp ./openipc-xm-build/InstallDesc.generated ./openipc-xm-build/InstallDesc
```

Build the inner firmware ZIP (named `.bin` to match XM convention):

```fish
cd openipc-xm-build

zip -9 ../OpenIPC_GK7205V200_all.bin \
  u-boot.env.img \
  openipc-kernel.img \
  openipc-rootfs.img \
  openipc-rootfs-data.img \
  InstallDesc

cd ..
```

Check it:

```fish
zipinfo -1 OpenIPC_GK7205V200_all.bin
unzip -t OpenIPC_GK7205V200_all.bin
```

At this point the original generated descriptor has the environment first. We deliberately changed that before flashing.

---

## 11. Move the U-Boot environment burn to LAST

This is a safety measure. If the upgrade dies while writing the large payloads, the old boot environment remains active until the kernel/rootfs/rootfs_data are already written.

Run:

```fish
python scripts/reorder_xm_env_last.py OpenIPC_GK7205V200_all.bin
```

It creates:

```text
OpenIPC_GK7205V200_all_env-last.bin
```

The required burn order is:

```text
openipc-kernel.img
openipc-rootfs.img
openipc-rootfs-data.img
u-boot.env.img
```

The custom XM CRC does not change merely because those same burn entries are reordered.

---

## 12. Dry-run the exact package through the network uploader

The tested uploader reproduces the firmware-upgrade path observed from XM VideoPlayTool:

```text
0x05F0 / 1520  OPSystemUpgrade Start
0x05F1 / 1521  Start reply
0x05F2 / 1522  firmware data
0x05F3 / 1523  chunk ACK
0x05F4 / 1524  progress/status
```

Data is uploaded in 32,768-byte chunks, with `end_flag=1` on the final chunk. That final flag is critical to entering the burn/progress state.

**Dry-run first:**

```fish
env PYTHONPATH=$HOME/Documents/python-dvr \
  python scripts/xm_upgrade.py \
  ./OpenIPC_GK7205V200_all_env-last.bin
```

Nothing is sent without `--flash`.

Inspect the displayed:

- `Hardware`
- `DevID`
- calculated `CRC`
- ZIP integrity
- burn order

Do not continue if anything is unexpected.

---

## 13. Final pre-flash audit

Before adding `--flash`, reconfirm that the image wrappers target exactly:

```text
kernel      0x050000 → 0x250000
rootfs      0x250000 → 0x750000
rootfs_data 0x750000 → 0x7B0000
env         0x030000 → 0x040000
```

Therefore these two critical areas remain untouched:

```text
0x000000 → 0x030000  stock XM U-Boot
0x7B0000 → 0x800000  factory/config
```

Also keep another terminal pinging the camera and, ideally, capture the upgrade traffic for forensic/recovery purposes.

---

## 14. Flash OpenIPC over Ethernet

When all checks pass:

```fish
env PYTHONPATH=$HOME/Documents/python-dvr \
  python scripts/xm_upgrade.py \
  ./OpenIPC_GK7205V200_all_env-last.bin \
  --host CAMERA_IP \
  --user admin \
  --flash
```

The script prompts for the admin password and requires an explicit confirmation of the form:

```text
FLASH CAMERA_IP
```

Once data transfer begins:

> [!WARNING]
> **DO NOT POWER-CYCLE THE CAMERA.**

A normal successful flow is:

```text
DVRIP login
→ Start reply Ret=100
→ firmware chunks
→ chunk ACKs
→ flash progress
→ 100%
→ terminal status
→ camera reboots
```

On our successful XM implementation, the vendor's known-good sequence ended:

```text
... 98, 99, 100, 100, 100, 515
```

`Ret=515` **after 100%** was part of the successful vendor flow and preceded reboot; it was not treated as a flash failure.

The historical successful OpenIPC upload reached `100%`, emitted `Ret=515`, rebooted, and returned as OpenIPC.

---

## 15. Find OpenIPC after reboot

Do not assume it will retain the stock IP. The successful first boot obtained a new DHCP lease while retaining the camera's original MAC address.

Look in your DHCP/router client list or scan your subnet:

```fish
sudo nmap -sn YOUR_SUBNET/24
```

The tested camera appeared with hostname:

```text
openipc-gk7205v200
```

Then connect:

```fish
ssh root@NEW_CAMERA_IP
```

---

## 16. Verify the temporary successful layout

Immediately after the initial network conversion (while still using XM U-Boot), the tested camera reported:

```text
mtd0: 00030000 "boot"
mtd1: 00010000 "env"
mtd2: 00010000 "reserved"
mtd3: 00200000 "kernel"
mtd4: 00500000 "rootfs"
mtd5: 00060000 "rootfs_data"
mtd6: 00050000 "factory"
```

Check:

```sh
cat /proc/mtd
cat /proc/cmdline
fw_printenv
```

The initial OpenIPC root should be:

```text
root=/dev/mtdblock4
```

This is the proof that the **network-only stock-XM → OpenIPC conversion is complete**. Replacing U-Boot is optional and is a separate, higher-risk operation.

---

## 17. Sensor and day/night configuration for this board

The tested sensor is:

```text
SC223A
```

Majestic uses:

```text
/etc/sensors/sc223a_i2c_1080p.ini
```

Confirmed GPIO mapping:

```text
GPIO 8   IR-cut NIGHT pulse
GPIO 9   IR-cut DAY pulse
GPIO 15  light sensor (1 = dark)
GPIO 16  IR LEDs, active-high
```

A working Majestic night-mode configuration was:

```yaml
nightMode:
  irCutSingleInvert: false
  lightSensorInvert: true
  monitorDelay: 0
  irCutPin1: 8
  irCutPin2: 9
  lightSensorPin: 15
  backlightPin: 16
```

---

## 18. Known limitations on this exact camera

### PTZ

The stock XM firmware uses its own motor stack (`XmMotor`, `/dev/motor`). Standard OpenIPC on this board does not currently reproduce that device, so PTZ motor control was not restored by the basic conversion.

### Audio

The microphone works, but the stock firmware enables proprietary vendor VQE processing (including AGC/HPF behavior) that the tested OpenIPC setup did not fully reproduce.

---

## 19. Optional OpenIPC U-Boot migration

The successful initial installation **does not require replacing U-Boot**. Keeping XM U-Boot is deliberately safer.

We later migrated the same camera to the OpenIPC universal GK7205V200 U-Boot over SSH, producing the final layout:

```text
0x000000-0x040000  OpenIPC U-Boot
0x040000-0x050000  env
0x050000-0x250000  kernel
0x250000-0x750000  rootfs
0x750000-0x7B0000  rootfs_data
0x7B0000-0x800000  factory
```

That bootloader migration is substantially riskier and should be documented/performed separately after OpenIPC itself is proven stable.

---

## 20. Recovery notes

If OpenIPC Linux is damaged but U-Boot still starts, UART/TFTP recovery may still be possible.

If the bootloader itself is damaged, network recovery may be impossible and you may need:

- 3.3 V UART / Goke boot-ROM recovery,
- an external SPI NOR programmer,
- the original raw flash backup.

This is why the network-only nature of the successful install should **not** be interpreted as meaning recovery hardware is unnecessary.

---

## Historical known-good example (do not hard-code these values)

For the OpenIPC build used in the successful September 2026 conversion, the generated payload sizes were:

```text
u-boot.env.img             65,600 bytes (64-byte XM wrapper + 65,536 env)
openipc-kernel.img      1,824,256 bytes
openipc-rootfs.img      5,013,568 bytes
openipc-rootfs-data.img   393,280 bytes
```

The generated XM package CRC for those **specific files** was:

```text
575703033072
```

and the final package burn order was:

```text
kernel → rootfs → rootfs_data → env
```

The final package size was about 6.82 MB and the camera successfully booted OpenIPC afterward.

Again: **recalculate the CRC for your own OpenIPC images.**

---

---

## License

This project is licensed under the **GNU General Public License v3.0 or later**.

See [`LICENSE`](LICENSE) for the full license text. Contributions are welcome; see [`CONTRIBUTING.md`](CONTRIBUTING.md).


## Credits

- OpenIPC project and contributors
- OpenIPC GK7205V200 maintainers
- Xiongmai/XM reverse-engineering community

This guide documents one verified Hiseeu/XM hardware revision. Contributions for other revisions should include the exact hardware ID, SoC, sensor, flash chip/size and stock firmware version.
