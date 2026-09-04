#!/usr/bin/env python3
import argparse
import ipaddress
import struct
import subprocess
import tempfile
from pathlib import Path

TAG_CHECKCODE = 0x000A0007
ZERO_IV_HEX = "00" * 16

def derive_key(rand32: int) -> bytes:
    # Reconstructed from XM packet builder:
    # x0 = (rand % 65535) + 1
    # for i=0..7:
    #   x = (x*(x+1)+i) % 65535
    #   key[2*i]   = x % 255
    #   key[2*i+1] = (x // (i+1)) % 255
    x = (rand32 % 0xFFFF) + 1
    out = bytearray()
    for i in range(8):
        x = (x * (x + 1) + i) % 0xFFFF
        out.append(x % 0xFF)
        out.append((x // (i + 1)) % 0xFF)
    return bytes(out)

def aes128_cbc_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    # Use OpenSSL so the script stays dependency-free on Python packages.
    proc = subprocess.run(
        [
            "openssl", "enc", "-aes-128-cbc", "-d", "-nopad",
            "-K", key.hex(),
            "-iv", ZERO_IV_HEX,
        ],
        input=ciphertext,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace").strip())
    return proc.stdout

def iter_pcap_packets(path: Path):
    data = path.read_bytes()
    if len(data) < 24:
        raise ValueError("not a valid classic PCAP file")

    magic = data[:4]
    if magic == b"\xd4\xc3\xb2\xa1":
        endian = "<"
    elif magic == b"\xa1\xb2\xc3\xd4":
        endian = ">"
    elif magic in (b"\x0a\x0d\x0d\x0a",):
        raise ValueError("PCAPNG is not supported by this small script; save as classic .pcap")
    else:
        raise ValueError("unknown capture format")

    _, _, _, _, _, linktype = struct.unpack_from(endian + "HHIIII", data, 4)
    off = 24
    while off + 16 <= len(data):
        ts_sec, ts_frac, incl_len, orig_len = struct.unpack_from(endian + "IIII", data, off)
        off += 16
        pkt = data[off:off + incl_len]
        off += incl_len
        yield linktype, ts_sec, ts_frac, pkt

def udp_payload_from_packet(linktype: int, pkt: bytes):
    # DLT_EN10MB = 1
    if linktype == 1:
        if len(pkt) < 14:
            return None
        ethertype = struct.unpack("!H", pkt[12:14])[0]
        off = 14
        if ethertype == 0x8100 and len(pkt) >= 18:
            ethertype = struct.unpack("!H", pkt[16:18])[0]
            off = 18
        if ethertype != 0x0800:
            return None

    # DLT_LINUX_SLL2 = 276
    elif linktype == 276:
        if len(pkt) < 20:
            return None
        proto = struct.unpack("!H", pkt[0:2])[0]
        if proto != 0x0800:
            return None
        off = 20

    # DLT_RAW = 101
    elif linktype == 101:
        off = 0

    else:
        raise ValueError(f"unsupported PCAP link type {linktype}")

    if len(pkt) < off + 20 or (pkt[off] >> 4) != 4:
        return None

    ihl = (pkt[off] & 0x0F) * 4
    if len(pkt) < off + ihl + 8 or pkt[off + 9] != 17:
        return None

    src = str(ipaddress.IPv4Address(pkt[off + 12:off + 16]))
    dst = str(ipaddress.IPv4Address(pkt[off + 16:off + 20]))

    uoff = off + ihl
    sport, dport, ulen, _ = struct.unpack("!HHHH", pkt[uoff:uoff + 8])
    if ulen < 8 or len(pkt) < uoff + ulen:
        return None

    payload = pkt[uoff + 8:uoff + ulen]
    return src, dst, sport, dport, payload

def extract_checkcode(plain: bytes):
    # XM's payload includes 8-byte {u32 tag, u32 value} entries.
    hits = []
    for off in range(0, max(0, len(plain) - 7)):
        tag, value = struct.unpack_from("<II", plain, off)
        if tag == TAG_CHECKCODE:
            hits.append((off, value))
    return hits

def main():
    ap = argparse.ArgumentParser(description="Extract rotating XM NetIPTelnet CheckCode from UDP/30000 PCAP")
    ap.add_argument("pcap", type=Path)
    ap.add_argument("--camera-ip", help="Only inspect packets from this camera IP")
    args = ap.parse_args()

    seen = set()
    results = []

    for linktype, ts_sec, ts_frac, pkt in iter_pcap_packets(args.pcap):
        parsed = udp_payload_from_packet(linktype, pkt)
        if not parsed:
            continue
        src, dst, sport, dport, payload = parsed

        if dport != 30000:
            continue
        if args.camera_ip and src != args.camera_ip:
            continue
        if len(payload) < 24 or payload[:2] != b"\x4a\x01":
            continue
        if payload in seen:
            continue
        seen.add(payload)

        plain_len = struct.unpack_from("<H", payload, 2)[0]
        rand32 = struct.unpack_from("<I", payload, 4)[0]
        ciphertext = payload[8:]

        if len(ciphertext) % 16:
            continue

        key = derive_key(rand32)
        plain = aes128_cbc_decrypt(ciphertext, key)
        hits = extract_checkcode(plain[:plain_len])

        for off, code in hits:
            results.append((ts_sec, src, dst, rand32, code, off))

    if not results:
        print("No CheckCode records found.")
        raise SystemExit(1)

    for i, (_, src, dst, rand32, code, off) in enumerate(results, 1):
        print(f"{i}: {src} -> {dst}  rand=0x{rand32:08x}  CheckCode={code}  TLV_offset={off}")

    latest = results[-1][4]
    print(f"\nLatest CheckCode: {latest}")

if __name__ == "__main__":
    main()
