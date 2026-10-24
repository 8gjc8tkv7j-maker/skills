#!/usr/bin/env python3
"""A pretend `adb` talking to a pretend Google TV, for testing tvctl.py without a television.

State lives in the JSON file named by $FAKE_ADB_STATE; every command is appended to its "log".
"""
import json
import os
import shlex
import struct
import sys
import zlib

STATE = os.environ["FAKE_ADB_STATE"]
HOME_FILES = "/sdcard/Android/data/dev.tvhome/files"
LAUNCHERX = "com.google.android.apps.tv.launcherx"
WRAITH = "com.google.android.tungsten.setupwraith"


def load():
    with open(STATE) as f:
        return json.load(f)


def save(st):
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)


def png(w, h, noisy):
    rows = b""
    seed = 12345
    for _ in range(h):
        row = bytearray([0])
        for _ in range(w):
            if noisy:
                seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
                row += bytes([seed & 255, (seed >> 8) & 255, (seed >> 16) & 255])
            else:
                row += b"\x00\x00\x00"
        rows += bytes(row)

    def chunk(kind, data):
        c = struct.pack("!I", len(data)) + kind + data
        return c + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def enabled(st, pkg):
    p = st["packages"].get(pkg)
    return bool(p and p["enabled"])


def home_target(st):
    if st.get("stock_intercepts_home") and enabled(st, LAUNCHERX):
        return LAUNCHERX
    if st.get("wraith_takes_over") and not enabled(st, LAUNCHERX) and enabled(st, WRAITH):
        return WRAITH
    holder = st.get("role_home")
    if holder and enabled(st, holder):
        return holder
    if enabled(st, WRAITH):
        return WRAITH
    for pkg, p in st["packages"].items():
        if p.get("home") and p["enabled"]:
            return pkg
    return "android"


def shell(st, cmd):
    a = shlex.split(cmd)
    if not a:
        return 0, ""
    if a[0] == "getprop":
        if len(a) == 1:
            return 0, "\n".join(f"[{k}]: [{v}]" for k, v in st["props"].items())
        return 0, st["props"].get(a[1], "")
    if a[:3] == ["pm", "list", "packages"]:
        flags = a[3:]
        out = []
        for pkg, p in sorted(st["packages"].items()):
            if not p.get("installed", True) and "-u" not in flags:
                continue
            if "-d" in flags and p["enabled"]:
                continue
            if "-e" in flags and not p["enabled"]:
                continue
            if "-s" in flags and not p["system"]:
                continue
            if "-3" in flags and p["system"]:
                continue
            out.append(f"package:/system/app/{pkg}.apk={pkg}" if "-f" in flags else f"package:{pkg}")
        return 0, "\n".join(out)
    if a[:2] == ["pm", "disable-user"]:
        pkg = a[-1]
        p = st["packages"].get(pkg)
        if not p:
            return 1, f"Error: Unknown package: {pkg}"
        if p.get("undisableable"):
            return 1, f"Error: java.lang.SecurityException: Shell cannot change component state for {pkg}"
        p["enabled"] = False
        if p.get("crash_on_disable"):
            st["error_dialog"] = f"Application Error: {p['crash_on_disable']}"
        return 0, f"Package {pkg} new state: disabled-user"
    if a[:2] == ["pm", "enable"]:
        pkg = a[-1]
        p = st["packages"].get(pkg)
        if not p:
            return 1, f"Error: Unknown package: {pkg}"
        p["enabled"] = True
        return 0, f"Package {pkg} new state: enabled"
    if a[:2] == ["pm", "uninstall"]:
        return 1, "Failure [not allowed in the fake]"
    if a[0] == "settings":
        ns = a[2] if len(a) > 2 else ""
        table = st["settings"].setdefault(ns, {})
        if a[1] == "list":
            return 0, "\n".join(f"{k}={v}" for k, v in sorted(st["settings"].get(a[2], {}).items()))
        if a[1] == "get":
            return 0, table.get(a[3], "null")
        if a[1] == "put":
            table[a[3]] = a[4]
            return 0, ""
        if a[1] == "delete":
            table.pop(a[3], None)
            return 0, "Deleted 1 rows"
    if a[:2] == ["cmd", "role"]:
        role_cmd = a[2]
        if role_cmd == "get-role-holders":
            return 0, st.get("role_home") or ""
        if role_cmd == "add-role-holder":
            st["role_home"] = a[-1]
            return 0, ""
        if role_cmd == "remove-role-holder":
            if st.get("role_home") == a[-1]:
                st["role_home"] = None
            return 0, ""
    if a[:3] == ["cmd", "package", "resolve-activity"]:
        return 0, ("priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n"
                   f"{home_target(st)}/.Home")
    if a[:3] == ["cmd", "package", "set-home-activity"]:
        st["role_home"] = a[-1].split("/")[0]
        return 0, "Success"
    if a[:3] == ["cmd", "package", "query-activities"]:
        out = []
        for pkg, p in sorted(st["packages"].items()):
            if p.get("launcher") and p["enabled"]:
                out += ["priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false",
                        f"{pkg}/.MainActivity"]
        return 0, "\n".join(out)
    if a[:2] == ["input", "keyevent"]:
        if a[2] in ("KEYCODE_HOME", "3"):
            st["foreground"] = f"{home_target(st)}/.Home"
        elif a[2] in ("KEYCODE_BACK", "4"):
            st["error_dialog"] = None
        return 0, ""
    if a[:3] == ["dumpsys", "activity", "activities"]:
        return 0, f"  topResumedActivity=ActivityRecord{{1a2b3c u0 {st['foreground']} t12}}"
    if a[:2] == ["dumpsys", "window"]:
        focus = st.get("error_dialog") or st["foreground"]
        return 0, f"  mCurrentFocus=Window{{4d5e6f u0 {focus}}}"
    if a[:2] == ["am", "start"]:
        comp = a[a.index("-n") + 1]
        if not enabled(st, comp.split("/")[0]):
            return 1, "Error: Activity not started, unable to resolve Intent"
        st["foreground"] = comp
        if "--es" in a and a[a.index("--es") + 1] == "dock":
            st["files"][f"{HOME_FILES}/dock.txt"] = "\n".join(a[a.index("--es") + 2].split(",")) + "\n"
        return 0, f"Starting: Intent {{ cmp={comp} }}"
    if a[0] == "pidof":
        return 0, "1234" if st.get("system_server", True) else ""
    if a[0] in ("mkdir", "rm", "ps"):
        return 0, ""
    if a[0] == "cat":
        if a[1] in st["files"]:
            return 0, st["files"][a[1]]
        return 1, f"cat: {a[1]}: No such file or directory"
    if a[0] == "screencap":
        st["files"][a[-1]] = "<png>"
        return 0, ""
    return 127, f"/system/bin/sh: {a[0]}: not supported by fake adb"


def main(argv):
    st = load()
    st["log"].append(argv)
    serial = None
    if argv[:1] == ["-s"]:
        serial, argv = argv[1], argv[2:]
    rc, out, binary = 0, "", None
    cmd = argv[0] if argv else ""
    if cmd == "devices":
        lines = ["List of devices attached"]
        if st["connected"]:
            lines.append(f"{st['serial']}\t{'device' if st['authorized'] else 'unauthorized'}")
        out = "\n".join(lines)
    elif cmd == "connect":
        if argv[1] == st["serial"] or argv[1].split(":")[0] == st["serial"].split(":")[0]:
            st["connected"] = True
            out = f"connected to {argv[1]}"
        else:
            rc, out = 1, f"failed to connect to '{argv[1]}': Connection refused"
    elif cmd == "pair":
        out = f"Successfully paired to {argv[1]} [guid=adb-FAKE]"
    elif not st["connected"] or (serial and serial != st["serial"]):
        rc, out = 1, f"error: device '{serial}' not found"
    elif cmd == "get-state":
        out = "device" if st["authorized"] else "unauthorized"
        rc = 0 if st["authorized"] else 1
    elif cmd == "install":
        st["packages"]["dev.tvhome"] = {"enabled": True, "system": False, "launcher": True, "home": True}
        out = "Performing Streamed Install\nSuccess"
    elif cmd == "uninstall":
        pkg = argv[-1]
        p = st["packages"].get(pkg)
        if p and not p["system"]:
            del st["packages"][pkg]
            out = "Success"
        else:
            rc, out = 1, "Failure [DELETE_FAILED_INTERNAL_ERROR]"
    elif cmd == "push":
        with open(argv[1], "rb") as f:
            st["files"][argv[2]] = f"<{len(f.read())} bytes>"
        out = f"{argv[1]}: 1 file pushed"
    elif cmd == "pull":
        with open(argv[2], "wb") as f:
            f.write(png(160, 90, True))
        out = "1 file pulled"
    elif cmd == "exec-out" and argv[1].startswith("screencap"):
        binary = png(160, 90, not st.get("blank_screen") and not st.get("error_dialog"))
    elif cmd == "shell":
        rc, out = shell(st, " ".join(argv[1:]))
    else:
        rc, out = 1, f"fake adb: unsupported command {argv}"
    save(st)
    if binary is not None:
        sys.stdout.buffer.write(binary)
    elif out:
        print(out)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
