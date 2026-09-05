#!/usr/bin/env python3
"""
XM / Xiongmai DVRIP firmware uploader.

Protocol reproduced from a real VideoPlayTool upgrade capture:
  0x05F0 (1520) OPSystemUpgrade Start
  0x05F1 (1521) Start reply
  0x05F2 (1522) binary firmware chunks, 32768 bytes
  0x05F3 (1523) chunk acknowledgements
  0x05F4 (1524) flash progress/status (Ret = 0..100, then terminal status)

Requires the same `dvrip` Python module used by the existing camera scripts.

SAFETY:
  The default mode validates and describes the package only.
  Nothing is sent to the camera unless --flash is supplied.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import struct
import sys
import time
import zipfile
from pathlib import Path

from package_safety import check_package


WIRE_HEADER = struct.Struct("<BB2xIIBBHI")  # head,ver,pad,session,seq,channel,end,msgid,len

MSG_UPGRADE_START = 1520  # 0x05F0
MSG_UPGRADE_START_REPLY = 1521  # 0x05F1
MSG_UPGRADE_DATA = 1522  # 0x05F2
MSG_UPGRADE_ACK = 1523  # 0x05F3
MSG_UPGRADE_PROGRESS = 1524  # 0x05F4
MSG_KEEPALIVE = 1006
MSG_KEEPALIVE_REPLY = 1007

CHUNK_SIZE = 32768


def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        try:
            part = sock.recv(n - len(buf))
        except socket.timeout:
            return None
        if not part:
            raise ConnectionError("camera closed the TCP connection")
        buf.extend(part)
    return bytes(buf)


def recv_frame(sock: socket.socket, timeout: float = 1.0):
    old_timeout = sock.gettimeout()
    sock.settimeout(timeout)
    try:
        hdr = recv_exact(sock, WIRE_HEADER.size)
        if hdr is None:
            return None

        (
            head,
            version,
            session,
            seq,
            channel,
            end_flag,
            msgid,
            length,
        ) = WIRE_HEADER.unpack(hdr)

        if head != 0xFF:
            raise RuntimeError(f"bad DVRIP header byte: 0x{head:02x}")

        body = recv_exact(sock, length)
        if body is None:
            raise TimeoutError(
                f"timed out while reading {length} byte DVRIP body"
            )

        return {
            "head": head,
            "version": version,
            "session": session,
            "seq": seq,
            "channel": channel,
            "end_flag": end_flag,
            "msgid": msgid,
            "length": length,
            "body": body,
        }
    finally:
        try:
            sock.settimeout(old_timeout)
        except OSError:
            # The camera/python-dvr may close the descriptor during reboot.
            pass


def decode_json(body: bytes):
    cleaned = body.rstrip(b"\x00\r\n")
    try:
        return json.loads(cleaned.decode("utf-8"))
    except Exception:
        return None


def send_raw_frame(
    sock: socket.socket,
    session: int,
    seq: int,
    msgid: int,
    body: bytes,
    version: int = 1,
    channel: int = 0,
    end_flag: int = 0,
):
    # DVRIP bytes 12 and 13 are channel and end_flag.  This matters for
    # firmware upload: VideoPlayTool sets end_flag=1 on the LAST 0x05F2
    # chunk.  Without it, the camera ACKs the final chunk but never starts
    # the burn/progress state machine.
    pkt = WIRE_HEADER.pack(
        0xFF,
        version,
        session,
        seq & 0xFFFFFFFF,
        channel & 0xFF,
        end_flag & 0xFF,
        msgid,
        len(body),
    ) + body
    sock.sendall(pkt)


def send_keepalive(sock: socket.socket, session: int, seq: int):
    payload = {
        "Name": "KeepAlive",
        "SessionID": f"{session:#0{12}x}",
    }
    body = json.dumps(payload, separators=(",", ":")).encode() + b"\x00"
    send_raw_frame(sock, session, seq, MSG_KEEPALIVE, body, version=1)


def validate_package(path: Path):
    check_package(path)
    size = path.stat().st_size
    print(f"Package : {path}")
    print(f"Size    : {size:,} bytes")

    if not zipfile.is_zipfile(path):
        raise SystemExit(
            "Refusing: file is not a valid ZIP-based XM firmware package."
        )

    with zipfile.ZipFile(path, "r") as zf:
        bad = zf.testzip()
        if bad:
            raise SystemExit(f"Refusing: ZIP CRC failure in {bad!r}")

        names = zf.namelist()
        print("ZIP     : OK")
        print("Entries :")
        for n in names:
            info = zf.getinfo(n)
            print(f"  {n:<32} {info.file_size:>10,} bytes")

        if "InstallDesc" in names:
            try:
                desc = json.loads(zf.read("InstallDesc").decode("utf-8"))
            except Exception as exc:
                raise SystemExit(f"Refusing: cannot parse InstallDesc: {exc}")

            print("\nInstallDesc:")
            for key in ("Hardware", "DevID", "Vendor", "CompatibleVersion", "CRC"):
                if key in desc:
                    print(f"  {key:<18}: {desc[key]}")

            cmds = desc.get("UpgradeCommand", [])
            if isinstance(cmds, list):
                print("  UpgradeCommand:")
                for c in cmds:
                    if isinstance(c, dict):
                        print(
                            f"    {c.get('Command', '?'):<8} "
                            f"{c.get('FileName', '?')}"
                        )
        else:
            print("\nWARNING: package has no InstallDesc entry.")

    return size


def make_cam(host: str, port: int, user: str, password: str):
    from dvrip import DVRIPCam

    # Different python-dvr revisions use slightly different constructor
    # signatures, so handle both.
    try:
        return DVRIPCam(host, port=port, user=user, password=password)
    except TypeError:
        if port != 34567:
            raise SystemExit(
                "This DVRIPCam build does not accept a port= argument; "
                "use port 34567."
            )
        return DVRIPCam(host, user=user, password=password)


def start_upgrade(cam: DVRIPCam, file_size: int):
    payload = {
        "Name": "OPSystemUpgrade",
        "OPSystemUpgrade": {
            "Action": "Start",
            "Type": "System",
            "FileLength": file_size,
            "FileSize": file_size,
        },
    }

    if hasattr(cam, "send_custom"):
        reply = cam.send_custom(
            MSG_UPGRADE_START,
            payload,
            wait_response=True,
            version=1,
        )
    else:
        # Older python-dvr builds have only send(), which emits version 0.
        # The camera usually accepts it, but the captured web upgrader used
        # DVRIP version 1, so fail rather than silently changing the protocol.
        raise SystemExit(
            "Your dvrip.py has no send_custom(..., version=1). "
            "Use the python-dvr copy we used for NetIPTelnet."
        )

    # Some python-dvr revisions return the version-1 response body as
    # bytearray/bytes instead of decoding JSON for us.
    if isinstance(reply, (bytes, bytearray)):
        raw = bytes(reply).rstrip(b"\x00\r\n ")
        try:
            reply = json.loads(raw.decode("utf-8"))
        except Exception:
            pass

    print(f"Start reply: {reply}")

    if not isinstance(reply, dict) or reply.get("Ret") != 100:
        raise RuntimeError(f"camera rejected upgrade start: {reply!r}")


def upload_file(cam: DVRIPCam, path: Path, file_size: int):
    sock = cam.socket
    if sock is None:
        raise RuntimeError("DVRIP socket disappeared after login")

    total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
    session = int(cam.session)

    print(
        f"\nUploading {file_size:,} bytes in {total_chunks} chunks "
        f"of up to {CHUNK_SIZE} bytes..."
    )
    print("Using ACK-driven transfer to match VideoPlayTool.")

    sent = 0
    acked = set()
    started = time.monotonic()

    def handle_frame(frame):
        if frame is None:
            return ("timeout", None)

        msgid = frame["msgid"]
        seq = frame["seq"]
        obj = decode_json(frame["body"])

        if msgid == MSG_UPGRADE_ACK:
            if isinstance(obj, dict) and obj.get("Ret") == 100:
                acked.add(seq)
                return ("ack", seq)
            return ("ack_bad", (seq, obj))

        if msgid == MSG_UPGRADE_PROGRESS:
            return ("progress", obj)

        if msgid == MSG_KEEPALIVE_REPLY:
            return ("keepalive", obj)

        return ("other", (msgid, seq, obj, frame["body"]))

    with path.open("rb") as fh:
        for seq in range(total_chunks):
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                raise RuntimeError("unexpected EOF while reading firmware")

            is_last = (seq == total_chunks - 1)
            send_raw_frame(
                sock,
                session=session,
                seq=seq,
                msgid=MSG_UPGRADE_DATA,
                body=chunk,
                version=1,
                channel=0,
                end_flag=1 if is_last else 0,
            )

            sent += len(chunk)
            pct = sent * 100.0 / file_size
            print(
                f"\rTransfer: {pct:6.2f}%  "
                f"{sent:,}/{file_size:,} bytes  "
                f"chunk {seq + 1}/{total_chunks}",
                end="",
                flush=True,
            )

            # The captured vendor session ACKed chunks 0..235. It then sent
            # chunks 236 and 237 back-to-back, with end_flag=1 on chunk 237,
            # and the camera transitioned straight into 0x05F4 progress.
            if seq >= total_chunks - 2:
                continue

            deadline = time.monotonic() + 10.0
            got_our_ack = False

            while time.monotonic() < deadline:
                frame = recv_frame(sock, timeout=1.0)
                kind, value = handle_frame(frame)

                if kind == "timeout":
                    continue

                if kind == "ack":
                    if value == seq:
                        got_our_ack = True
                        break
                    # A delayed ACK for an earlier chunk is harmless.
                    continue

                if kind == "ack_bad":
                    raise RuntimeError(
                        f"camera returned a bad ACK for chunk {value[0]}: "
                        f"{value[1]!r}"
                    )

                if kind == "progress":
                    # Progress before the final chunks would be unexpected,
                    # but preserve the evidence rather than discarding it.
                    raise RuntimeError(
                        f"camera entered flash progress early at chunk {seq}: "
                        f"{value!r}"
                    )

                if kind == "other":
                    msgid, rseq, obj, raw = value
                    if obj is not None:
                        print(
                            f"\nOther DVRIP frame during transfer: "
                            f"msgid={msgid} seq={rseq} JSON={obj}"
                        )

            if not got_our_ack:
                raise TimeoutError(
                    f"no 0x05F3 Ret:100 ACK for chunk {seq} within 10 seconds"
                )

    print()
    elapsed = time.monotonic() - started
    rate = file_size / max(elapsed, 0.001) / (1024 * 1024)
    print(
        f"Transfer completed in {elapsed:.2f}s "
        f"({rate:.2f} MiB/s)."
    )
    print(
        f"Chunk ACKs observed: {len(acked)}/{total_chunks} "
        "(expected: vendor capture ACKed 0..235, then entered progress)."
    )
    print("Waiting for camera flash progress/status...")

    max_progress = -1
    last_progress_print = None
    progress_values = []
    terminal_values = []
    first_100_at = None
    last_activity = time.monotonic()
    hard_deadline = time.monotonic() + 300

    # The real VideoPlayTool trace continues after multiple Ret=100 frames and
    # then emits Ret=515 immediately before the reboot.  Do not stop at 100.
    # Once 100 has been observed, keep draining the socket for a short grace
    # period so we preserve the terminal status/disconnect.
    POST_100_GRACE = 8.0

    while time.monotonic() < hard_deadline:
        now = time.monotonic()

        if first_100_at is not None and now - first_100_at >= POST_100_GRACE:
            print(
                f"\nPost-100 grace window ({POST_100_GRACE:.0f}s) expired "
                "without a disconnect."
            )
            break

        try:
            frame = recv_frame(sock, timeout=1.0)
        except (ConnectionError, ConnectionResetError, BrokenPipeError) as exc:
            if max_progress >= 100:
                print(
                    f"\nCamera connection closed after 100% ({type(exc).__name__}); "
                    "reboot is expected."
                )
                break
            raise
        except OSError as exc:
            # EBADF can occur if python-dvr closes its descriptor while the
            # camera is rebooting.  Treat it as expected only after 100%.
            if exc.errno == 9 and max_progress >= 100:
                print(
                    "\nLocal DVRIP descriptor closed after 100% (EBADF); "
                    "camera reboot/session teardown is expected."
                )
                break
            raise

        kind, value = handle_frame(frame)

        if kind == "timeout":
            if first_100_at is not None:
                continue
            if now - last_activity > 60:
                raise TimeoutError(
                    "no DVRIP response from camera for 60 seconds "
                    f"(last progress {max_progress}%)"
                )
            continue

        last_activity = time.monotonic()

        if kind == "ack":
            continue

        if kind == "ack_bad":
            print(f"\nUnexpected bad ACK after upload: {value!r}")
            continue

        if kind == "keepalive":
            continue

        if kind == "progress":
            obj = value
            if not isinstance(obj, dict):
                print(f"\nNon-JSON progress body: {obj!r}")
                continue

            ret = obj.get("Ret")
            if not isinstance(ret, int):
                print(f"\nProgress/status frame without integer Ret: {obj!r}")
                continue

            progress_values.append(ret)

            if 0 <= ret <= 100:
                max_progress = max(max_progress, ret)

                if ret != last_progress_print:
                    print(
                        f"\rFlash:    {ret:3d}%",
                        end="",
                        flush=True,
                    )
                    last_progress_print = ret

                if ret == 100 and first_100_at is None:
                    first_100_at = time.monotonic()

                continue

            # Values outside 0..100 are terminal/status codes, not percentages.
            terminal_values.append(ret)
            print(
                f"\nTerminal upgrade status: Ret={ret} "
                f"(0x{ret:04X})"
            )
            continue

        if kind == "other":
            msgid, seq, obj, raw = value
            if obj is not None:
                print(
                    f"\nOther DVRIP frame: msgid={msgid} "
                    f"seq={seq} JSON={obj}"
                )
            else:
                print(
                    f"\nOther DVRIP frame: msgid={msgid} "
                    f"seq={seq} length={len(raw)}"
                )

    if last_progress_print is not None:
        print()

    count_100 = sum(1 for x in progress_values if x == 100)
    print(
        f"Progress summary: max={max_progress}%  "
        f"Ret=100 count={count_100}  "
        f"terminal={terminal_values or 'none observed'}"
    )

    if max_progress < 100:
        raise RuntimeError(
            f"camera never reported 100% progress; maximum was {max_progress}%"
        )

    # In the known-good stock capture the camera emits Ret=515 after 100% and
    # then reboots successfully.  Preserve/report it, but do not mislabel it as
    # a flash failure merely because it is >100.
    if terminal_values:
        print(
            "Note: non-percentage Ret values were observed after 100%. "
            "The validated stock flow emits Ret=515 before reboot."
        )

    print("Camera reached 100%; upgrade transport/flash stage completed.")


def main():
    ap = argparse.ArgumentParser(
        description="Upload XM firmware over DVRIP port 34567."
    )
    ap.add_argument("firmware", type=Path, help="XM .bin/.zip firmware package")
    ap.add_argument("--host", help="camera address (required with --flash)")
    ap.add_argument("--port", type=int, default=34567)
    ap.add_argument("--user", default="admin")
    ap.add_argument(
        "--password",
        help="camera password (omit to prompt; avoids shell history)",
    )
    ap.add_argument(
        "--flash",
        action="store_true",
        help="actually contact the camera and perform the upgrade",
    )
    ap.add_argument(
        "--yes",
        action="store_true",
        help="skip the final interactive confirmation",
    )
    args = ap.parse_args()
    if args.flash and not args.host:
        ap.error("--host is required with --flash")

    path = args.firmware.expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"No such firmware file: {path}")

    size = validate_package(path)

    if not args.flash:
        print(
            "\nDRY RUN ONLY. Nothing was sent to the camera.\n"
            "Re-run with --flash when you are ready."
        )
        return 0

    print(
        "\n*** THIS WILL WRITE THE CAMERA'S SPI FLASH. ***\n"
        "Do not power-cycle the camera once transfer begins."
    )

    if not args.yes:
        answer = input(
            f"Type exactly FLASH {args.host} to continue: "
        ).strip()
        if answer != f"FLASH {args.host}":
            print("Cancelled.")
            return 1

    password = args.password
    if password is None:
        password = getpass.getpass("Camera admin password: ")

    cam = make_cam(args.host, args.port, args.user, password)

    try:
        print(f"\nConnecting to {args.host}:{args.port} as {args.user}...")
        if not cam.login():
            raise RuntimeError("DVRIP login failed")

        print(f"Authenticated. Session = 0x{int(cam.session):08X}")

        start_upgrade(cam, size)
        upload_file(cam, path, size)

        print("\nUpgrade transaction completed.")
        print(
            "If the camera is rebooting, do not interrupt power. "
            "Wait for it to return before judging the result."
        )
        return 0

    finally:
        try:
            cam.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(
            "\nInterrupted by user. If firmware transfer had already started, "
            "DO NOT power-cycle the camera.",
            file=sys.stderr,
        )
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
