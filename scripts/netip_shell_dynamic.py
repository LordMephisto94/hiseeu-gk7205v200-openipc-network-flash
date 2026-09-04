#!/usr/bin/env python3

from dvrip import DVRIPCam
from getpass import getpass
from pathlib import Path
import argparse
import json
import os

HOST_DEFAULT = "172.16.10.44"
USER_DEFAULT = "admin"
MSGID_NETIPTELNET = 1020
LEGACY_CHECKCODE = 446012311
CHECKCODE_FILE = Path.home() / ".config" / "xm-netip" / "checkcode"


def parse_checkcode(value: str) -> int:
    value = value.strip()
    if not value:
        raise ValueError("empty CheckCode")
    code = int(value, 0)
    if not 0 <= code <= 0xFFFFFFFF:
        raise ValueError("CheckCode must fit in an unsigned 32-bit integer")
    return code


def load_saved_checkcode():
    try:
        return parse_checkcode(CHECKCODE_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def save_checkcode(code: int):
    try:
        CHECKCODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHECKCODE_FILE.write_text(f"{code}\n")
    except OSError as exc:
        print(f"[warning] could not save CheckCode: {exc}")


def resolve_checkcode(cli_value):
    if cli_value is not None:
        return parse_checkcode(cli_value)

    env_value = os.environ.get("XM_CHECKCODE")
    if env_value:
        return parse_checkcode(env_value)

    saved = load_saved_checkcode()
    if saved is not None:
        return saved

    print(f"No saved CheckCode. Last known pre-reboot value was {LEGACY_CHECKCODE}, but it may now be stale.")
    while True:
        raw = input("Current NetIPTelnet CheckCode: ").strip()
        try:
            code = parse_checkcode(raw)
        except ValueError as exc:
            print(f"Invalid CheckCode: {exc}")
            continue
        save_checkcode(code)
        return code


def make_payload(session_id: str, checkcode: int, command: str, mode: str = "normal") -> dict:
    return {
        "Name": "NetIPTelnet",
        "SessionID": session_id,
        "NetIPTelnet": {
            "CheckCode": checkcode,
            "Command": command,
            "Type": mode,
        },
    }


def send_command(cam: DVRIPCam, session_id: str, checkcode: int, command: str, mode: str = "normal"):
    return cam.send(MSGID_NETIPTELNET, make_payload(session_id, checkcode, command, mode))


def extract_data(reply):
    if not isinstance(reply, dict):
        return None
    netip = reply.get("NetIPTelnet")
    if isinstance(netip, dict) and "Data" in netip:
        return netip["Data"]
    return None


def main():
    parser = argparse.ArgumentParser(description="XM NetIPTelnet interactive shell")
    parser.add_argument("--host", default=HOST_DEFAULT)
    parser.add_argument("--user", default=USER_DEFAULT)
    parser.add_argument("--checkcode", help="NetIPTelnet CheckCode (decimal or 0xHEX)")
    args = parser.parse_args()

    checkcode = resolve_checkcode(args.checkcode)
    password = getpass("Camera admin password: ")
    cam = DVRIPCam(args.host, user=args.user, password=password)

    if not cam.login():
        raise SystemExit("DVRIP login failed")

    sid = f"0x{cam.session:08X}"

    print(f"\nConnected to {args.host}")
    print(f"Session: {sid}")
    print(f"CheckCode: {checkcode} (0x{checkcode:08X})")
    print("Type commands normally.")
    print("Local commands: :help, :checkcode [value], :raw <cmd>, :console <cmd>, :quit\n")

    try:
        while True:
            try:
                line = input("xmcam> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue

            if line in (":quit", ":exit", "quit", "exit"):
                break

            if line == ":help":
                print(
                    "\n"
                    "  <command>              send as NetIPTelnet Type=normal\n"
                    "  :raw <command>         print the complete DVRIP reply\n"
                    "  :console <command>     send as NetIPTelnet Type=console\n"
                    "  :checkcode             show current CheckCode\n"
                    "  :checkcode <value>     replace and save CheckCode (decimal or 0xHEX)\n"
                    "  :quit / :exit          close the DVRIP session\n"
                )
                continue

            if line == ":checkcode":
                print(f"CheckCode: {checkcode} (0x{checkcode:08X})")
                continue

            if line.startswith(":checkcode "):
                try:
                    checkcode = parse_checkcode(line.split(None, 1)[1])
                except ValueError as exc:
                    print(f"Invalid CheckCode: {exc}")
                    continue
                save_checkcode(checkcode)
                print(f"CheckCode updated: {checkcode} (0x{checkcode:08X})")
                continue

            mode = "normal"
            raw_reply = False
            command = line

            if line.startswith(":raw "):
                raw_reply = True
                command = line[5:].strip()
            elif line.startswith(":console "):
                mode = "console"
                command = line[9:].strip()

            if not command:
                continue

            try:
                reply = send_command(cam, sid, checkcode, command, mode)
            except Exception as exc:
                print(f"[send error] {exc}")
                continue

            # 107 is an authorization/permission failure. On this camera a stale
            # cloud CheckCode is a leading cause, but it is not the only possible cause.
            if isinstance(reply, dict) and reply.get("Ret") == 107:
                print(f"[Ret=107] Camera rejected authorization using CheckCode {checkcode}.")
                raw = input("New CheckCode to retry now (Enter = keep current/no retry): ").strip()
                if raw:
                    try:
                        new_code = parse_checkcode(raw)
                    except ValueError as exc:
                        print(f"Invalid CheckCode: {exc}")
                    else:
                        checkcode = new_code
                        save_checkcode(checkcode)
                        try:
                            reply = send_command(cam, sid, checkcode, command, mode)
                        except Exception as exc:
                            print(f"[retry send error] {exc}")
                            continue

            if raw_reply:
                print(json.dumps(reply, indent=2, ensure_ascii=False))
                continue

            data = extract_data(reply)
            if data is not None:
                if data:
                    print(data, end="" if str(data).endswith("\n") else "\n")
                else:
                    print("[no output]")
                continue

            if isinstance(reply, dict):
                ret = reply.get("Ret")
                print(f"[no NetIPTelnet.Data; Ret={ret}]")
                print(json.dumps(reply, indent=2, ensure_ascii=False))
            else:
                print(repr(reply))

    finally:
        cam.close()
        print("Disconnected.")


if __name__ == "__main__":
    main()
