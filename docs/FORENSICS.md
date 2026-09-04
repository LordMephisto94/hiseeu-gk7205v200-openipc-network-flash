# Forensic notes

- CheckCode traffic: encrypted XM packet to UDP destination port 30000; Cloud had to be enabled.
- CheckCode TLV tag: `0x000A0007`.
- NetIPTelnet: DVRIP message ID 1020; stale CheckCode returned `Ret=107`.
- Initial OpenIPC conversion intentionally retained XM U-Boot at `0x000000-0x030000`.
- A 64 KiB reserved hole at `0x040000-0x050000` allowed kernel to remain at OpenIPC's normal `0x50000` offset.
- rootfs_data `0x750000-0x7B0000` was explicitly filled with `0xFF` to avoid stale XM filesystem data.
- The custom XM package CRC is not CRC32; `xm_crc_custom.py` reproduces the vendor calculation.
- Mx8Q verification was effectively disabled on the tested stock build because `/usr/bin/Squirrel/rs485/TransparentBase` was absent; do not generalise this to other builds.
- Environment burn was deliberately reordered last.
- DVRIP uploader uses 32 KiB chunks and sets `end_flag=1` on the last `0x05F2` frame.
- Known-good vendor progress sequence ended `100,100,100,515`; 515 after 100% preceded reboot.
- Successful first OpenIPC boot used DHCP and retained the original MAC.
