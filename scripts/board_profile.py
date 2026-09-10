"""Verified board profile for the Hiseeu/XM conversion target.

Keep the hardware identity and physical flash map in one place.  The builder
and package validator both import this module so their safety checks cannot
silently drift apart.
"""

TARGET_HARDWARE = "IPC_GK7205V200_G5C-LQ_S38"
TARGET_DEVID = "000659O61001000000000200"
FLASH_SIZE = 0x00800000

ENV_START = 0x00030000
ENV_END = 0x00040000
KERNEL_START = 0x00050000
KERNEL_END = 0x00250000
ROOTFS_START = 0x00250000
ROOTFS_END = 0x00750000
ROOTFS_DATA_START = 0x00750000
ROOTFS_DATA_END = 0x007B0000
XM_MTD5_START = 0x007B0000

# Package order is deliberate: the environment is burned last.
RANGES = {
    "openipc-kernel.img": (KERNEL_START, KERNEL_END),
    "openipc-rootfs.img": (ROOTFS_START, ROOTFS_END),
    "openipc-rootfs-data.img": (ROOTFS_DATA_START, ROOTFS_DATA_END),
    "u-boot.env.img": (ENV_START, ENV_END),
}
