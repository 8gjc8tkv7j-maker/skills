#!/usr/bin/env python3
"""tvctl: find an Android TV on your network, back it up, debloat it and give it a new home screen.

Everything is reversible:
  * apps are only ever *disabled* for the main user (pm disable-user --user 0), never uninstalled;
  * every change is written to the backup folder's journal *before* it is made, and restore.sh /
    restore.cmd are regenerated after every change, so the TV can always be put back.

Typical session (each step takes screenshots from the TV into the backup folder):
  python3 tvctl.py discover                  find TVs on the Wi-Fi
  python3 tvctl.py connect 192.168.1.42      allow the debugging prompt on the TV
  python3 tvctl.py backup                    full backup before anything changes
  python3 tvctl.py debloat                   ads/telemetry, then asks about each app/feature
  python3 tvctl.py tweaks                    optional speed/privacy settings
  python3 tvctl.py home setup                install TV Home, pick the dock, make it the home screen
  python3 tvctl.py restore                   undo everything (or run restore.sh / restore.cmd)

Non-interactive forms for scripts and assistants: `debloat --plan --json`, `debloat --apply PKG...`,
`tweaks --plan`, `tweaks --apply ID...`, `home dock PKG...`, `home set-default --disable-stock PKG...`.
Needs only Python 3.8+ and adb (Android platform-tools).
"""
import argparse
import html
import json
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

VERSION = "1.0"
HERE = Path(__file__).resolve().parent
CATALOG_PATH = HERE / "catalog.json"
DEFAULT_APK = HERE.parent / "launcher" / "dist" / "TVHome.apk"
HOME_PKG = "dev.tvhome"
HOME_COMPONENT = "dev.tvhome/.HomeActivity"
HOME_FILES = "/sdcard/Android/data/dev.tvhome/files"
HOME_ROLE = "android.app.role.HOME"
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:/@+-]*$")
PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
BLANK_PNG_BYTES = 20000

TWEAKS = [
    {
        "id": "animations",
        "title": "Snappier animations (0.5x instead of 1x)",
        "why": "Menus and app launches feel about twice as fast. Nothing else changes.",
        "default": True,
        "min_sdk": 17,
        "set": [("global", "window_animation_scale", "0.5"),
                ("global", "transition_animation_scale", "0.5"),
                ("global", "animator_duration_scale", "0.5")],
    },
    {
        "id": "scanning",
        "title": "Stop background Wi-Fi and Bluetooth scanning",
        "why": "These scans feed location services even when Wi-Fi/Bluetooth are off. A TV doesn't move.",
        "default": True,
        "min_sdk": 18,
        "set": [("global", "wifi_scan_always_enabled", "0"),
                ("global", "ble_scan_always_enabled", "0")],
    },
    {
        "id": "adblock-dns",
        "title": "Block ads system-wide with Private DNS (AdGuard DNS)",
        "why": "Filters ad and tracker domains for every app, including the stock home screen. "
               "Some free, ad-supported apps (Pluto TV, Tubi) may refuse to play while it is on.",
        "default": False,
        "min_sdk": 28,
        "set": [("global", "private_dns_mode", "hostname"),
                ("global", "private_dns_specifier", "dns.adguard-dns.com")],
    },
]


# ============================================================================ output & prompts

def say(msg=""):
    print(msg, flush=True)


def die(msg, code=1):
    print("error: " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


class Prompter:
    """Asks questions on a terminal; answers can also come from --yes or an --answers JSON file."""

    def __init__(self, yes=False, answers=None):
        self.yes = yes
        self.answers = answers or {}
        self.tty = sys.stdin.isatty()

    @property
    def can_ask(self):
        return self.tty or self.yes or bool(self.answers)

    def _lookup(self, key):
        if key in self.answers:
            return self.answers[key]
        for k, v in self.answers.items():  # allow "debloat.ask.*" style wildcards
            if k.endswith("*") and key.startswith(k[:-1]):
                return v
        return None

    def yes_no(self, key, question, default):
        a = self._lookup(key)
        if a is not None:
            say(f"{question} -> {'yes' if a else 'no'} (from answers)")
            return bool(a)
        if self.yes or not self.tty:
            say(f"{question} -> {'yes' if default else 'no'} (default)")
            return default
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            r = input(f"{question} {hint} ").strip().lower()
            if not r:
                return default
            if r in ("y", "yes"):
                return True
            if r in ("n", "no"):
                return False

    def text(self, key, question, default=""):
        a = self._lookup(key)
        if a is not None:
            say(f"{question} -> {a} (from answers)")
            return str(a)
        if self.yes or not self.tty:
            return default
        r = input(f"{question} ").strip()
        return r or default


# ============================================================================ adb

def find_adb(explicit=None):
    candidates = [explicit, os.environ.get("ADB"), shutil.which("adb")]
    home = Path.home()
    candidates += [
        home / "Library/Android/sdk/platform-tools/adb",
        home / "Android/Sdk/platform-tools/adb",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe",
        Path("C:/platform-tools/adb.exe"),
    ]
    for c in candidates:
        if not c:
            continue
        p = shutil.which(str(c)) or (str(c) if Path(c).is_file() else None)
        if p:
            return p
    die("adb not found. Install Android platform-tools:\n"
        "  macOS:   brew install android-platform-tools\n"
        "  Windows: winget install Google.PlatformTools\n"
        "  Linux:   sudo apt install adb   (or your distro's android-tools package)\n"
        "or pass --adb /path/to/adb")


class Adb:
    def __init__(self, path, serial=None):
        self.path = path
        self.serial = serial
        self._sdk = None

    def _base(self):
        return [self.path] + (["-s", self.serial] if self.serial else [])

    def raw(self, *args, timeout=90):
        try:
            p = subprocess.run(self._base() + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, b"", b"timed out"
        return p.returncode, p.stdout, p.stderr

    def run(self, *args, timeout=90):
        rc, out, err = self.raw(*args, timeout=timeout)
        text = (out + err).decode("utf-8", "replace").replace("\r\n", "\n")
        return rc, text.strip()

    def shell(self, cmd, timeout=90):
        return self.run("shell", cmd, timeout=timeout)

    def out(self, cmd, timeout=90):
        return self.shell(cmd, timeout=timeout)[1]

    def state(self):
        rc, out = self.run("get-state", timeout=15)
        return out.strip() if rc == 0 else ("unauthorized" if "unauthorized" in out else "offline")

    def prop(self, name):
        return self.out(f"getprop {name}").strip()

    @property
    def sdk(self):
        if self._sdk is None:
            try:
                self._sdk = int(self.prop("ro.build.version.sdk") or 0)
            except ValueError:
                self._sdk = 0
        return self._sdk

    def packages(self, flag=""):
        rc, out = self.shell(f"pm list packages {flag}".strip())
        return {line[8:].strip() for line in out.splitlines() if line.startswith("package:")}

    def package_state(self, pkg):
        if pkg not in self.packages("-u"):
            return "missing"
        return "disabled" if pkg in self.packages("-d") else "enabled"

    def leanback_apps(self):
        out = self.out("cmd package query-activities --brief -a android.intent.action.MAIN "
                       "-c android.intent.category.LEANBACK_LAUNCHER")
        found = []
        for line in out.splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)\s*$", line) or \
                re.search(r"packageName=([A-Za-z0-9_.]+)", line)
            if m and m.group(1) not in found:
                found.append(m.group(1))
        return found

    def resumed(self):
        """Package/activity in the foreground, or '' if unknown."""
        out = self.out("dumpsys activity activities", timeout=30)
        m = re.search(r"(?:topResumedActivity|mResumedActivity|ResumedActivity)[:=]\s*ActivityRecord\{\S+ u\d+ (\S+)",
                      out)
        if m:
            return m.group(1).rstrip("}")
        return self.focus()

    def focus(self):
        out = self.out("dumpsys window", timeout=30)
        m = re.search(r"mCurrentFocus=Window\{\S+ u\d+ ([^}]*)\}", out)
        return m.group(1).strip() if m else ""

    def screenshot(self, path):
        rc, data, _ = self.raw("exec-out", "screencap -p", timeout=60)
        if rc != 0 or not data.startswith(b"\x89PNG"):
            self.shell("screencap -p /sdcard/tvctl-shot.png")
            tmp = str(path) + ".part"
            self.run("pull", "/sdcard/tvctl-shot.png", tmp)
            self.shell("rm /sdcard/tvctl-shot.png")
            data = Path(tmp).read_bytes() if Path(tmp).exists() else b""
            if Path(tmp).exists():
                os.remove(tmp)
        if not data.startswith(b"\x89PNG"):
            return False
        Path(path).write_bytes(data)
        return True

    def key(self, name):
        self.shell(f"input keyevent {name}")


def device_list(adb_path):
    rc, out = Adb(adb_path).run("devices")
    devs = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devs.append((parts[0], parts[1]))
    return devs


# ============================================================================ discovery

MDNS_SERVICES = {
    "_androidtvremote2._tcp.local": "Android/Google TV",
    "_googlecast._tcp.local": "Cast",
    "_adb-tls-connect._tcp.local": "Wireless debugging",
    "_adb-tls-pairing._tcp.local": "Wireless debugging pairing",
    "_adb._tcp.local": "ADB",
    "_amzn-wplay._tcp.local": "Fire TV",
}


def _dns_name(name):
    out = b""
    for part in name.rstrip(".").split("."):
        out += bytes([len(part)]) + part.encode()
    return out + b"\x00"


def mdns_query_packet(services):
    pkt = struct.pack("!HHHHHH", 0, 0, len(services), 0, 0, 0)
    for s in services:
        pkt += _dns_name(s) + struct.pack("!HH", 12, 0x8001)  # PTR, IN, unicast-response bit
    return pkt


def _read_name(data, off):
    labels, jumped, end = [], False, off
    for _ in range(64):
        if off >= len(data):
            break
        n = data[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            ptr = ((n & 0x3F) << 8) | data[off + 1]
            if not jumped:
                end = off + 2
            jumped = True
            off = ptr
            continue
        labels.append(data[off + 1:off + 1 + n].decode("utf-8", "replace"))
        off += 1 + n
    return ".".join(labels), (end if jumped else off)


def parse_mdns(data):
    """Returns a list of (type, name, value) records from one mDNS response packet."""
    if len(data) < 12:
        return []
    _, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
    off = 12
    for _ in range(qd):
        _, off = _read_name(data, off)
        off += 4
    records = []
    for _ in range(an + ns + ar):
        name, off = _read_name(data, off)
        if off + 10 > len(data):
            break
        rtype, _, _, rdlen = struct.unpack("!HHIH", data[off:off + 10])
        off += 10
        rd = data[off:off + rdlen]
        if rtype == 12:
            records.append(("PTR", name, _read_name(data, off)[0]))
        elif rtype == 33 and rdlen >= 6:
            port = struct.unpack("!H", rd[4:6])[0]
            records.append(("SRV", name, (port, _read_name(data, off + 6)[0])))
        elif rtype == 16:
            txt, i = {}, 0
            while i < len(rd):
                n = rd[i]
                entry = rd[i + 1:i + 1 + n].decode("utf-8", "replace")
                if "=" in entry:
                    k, v = entry.split("=", 1)
                    txt[k] = v
                i += 1 + n
            records.append(("TXT", name, txt))
        elif rtype == 1 and rdlen == 4:
            records.append(("A", name, socket.inet_ntoa(rd)))
        off += rdlen
    return records


def mdns_scan(timeout=3.0):
    found = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.bind(("", 0))
        sock.settimeout(0.3)
        pkt = mdns_query_packet(list(MDNS_SERVICES))
        for _ in range(2):
            sock.sendto(pkt, ("224.0.0.251", 5353))
    except OSError:
        return found
    end = time.time() + timeout
    while time.time() < end:
        try:
            data, (ip, _) = sock.recvfrom(9000)
        except socket.timeout:
            continue
        except OSError:
            break
        dev = found.setdefault(ip, {"names": [], "services": {}, "model": ""})
        for rtype, name, value in parse_mdns(data):
            svc = next((s for s in MDNS_SERVICES if name.endswith(s)), None)
            if rtype == "SRV" and svc:
                dev["services"][MDNS_SERVICES[svc]] = value[0]
            if rtype == "PTR" and value.endswith(tuple(MDNS_SERVICES)):
                inst = value.split("._")[0]
                if inst and inst not in dev["names"] and not inst.startswith("adb-"):
                    dev["names"].append(inst)
            if rtype == "TXT":
                if value.get("fn") and value["fn"] not in dev["names"]:
                    dev["names"].insert(0, value["fn"])
                if value.get("md"):
                    dev["model"] = value["md"]
    sock.close()
    return found


def local_ipv4():
    for probe in ("192.168.255.255", "10.255.255.255", "172.31.255.255", "8.8.8.8"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 1))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except OSError:
            continue
    return None


def port_scan(hosts, ports, timeout=0.4):
    def probe(hp):
        h, p = hp
        try:
            with socket.create_connection((h, p), timeout=timeout):
                return hp
        except OSError:
            return None
    with ThreadPoolExecutor(max_workers=128) as ex:
        return [r for r in ex.map(probe, [(h, p) for h in hosts for p in ports]) if r]


def cmd_discover(args):
    say("Looking for Android TVs on your network (mDNS + port scan)...")
    devices = mdns_scan(args.timeout)
    subnet = args.subnet
    if not subnet:
        ip = local_ipv4()
        if ip:
            subnet = ".".join(ip.split(".")[:3]) + ".0/24"
    if subnet:
        base = subnet.split("/")[0].rsplit(".", 1)[0]
        hosts = [f"{base}.{i}" for i in range(1, 255)]
        for h, p in port_scan(hosts, (5555, 8009, 6466)):
            dev = devices.setdefault(h, {"names": [], "services": {}, "model": ""})
            dev.setdefault("open", []).append(p)
    tvs = []
    for ip, d in sorted(devices.items(), key=lambda kv: tuple(int(x) for x in kv[0].split("."))):
        open_ports = d.get("open", [])
        tvish = 6466 in open_ports or "Android/Google TV" in d["services"] or 5555 in open_ports \
            or "Wireless debugging" in d["services"] or "Fire TV" in d["services"]
        if not tvish and not args.all:
            continue
        tvs.append({
            "ip": ip,
            "name": d["names"][0] if d["names"] else "",
            "model": d["model"],
            "adb_5555_open": 5555 in open_ports,
            "wireless_debugging_port": d["services"].get("Wireless debugging"),
            "pairing_port": d["services"].get("Wireless debugging pairing"),
            "cast": 8009 in open_ports or "Cast" in d["services"],
        })
    if args.json:
        print(json.dumps({"subnet": subnet, "devices": tvs}, indent=2))
        return 0
    if not tvs:
        say("No Android TV found. Check the TV is on the same Wi-Fi, or pass --subnet 192.168.x.0/24.")
        say("You can also connect directly: tvctl.py connect <TV IP>  (Settings > Network > About shows it).")
        return 1
    say(f"Found {len(tvs)} device(s) on {subnet or 'the network'}:")
    for i, t in enumerate(tvs, 1):
        label = f'"{t["name"]}"' if t["name"] else "(no name)"
        model = f" [{t['model']}]" if t["model"] else ""
        say(f"  {i}. {t['ip']:<15} {label}{model}")
        if t["adb_5555_open"]:
            say(f"       ADB is on  -> python3 tvctl.py connect {t['ip']}")
        elif t["wireless_debugging_port"] or t["pairing_port"]:
            say(f"       Wireless debugging is on -> pair first: python3 tvctl.py pair {t['ip']}:<pairing port> <code>")
        else:
            say("       ADB is off -> on the TV: Settings > System (or Device Preferences) > About, press OK on")
            say("       'Build' 7 times, then Developer options > turn on USB debugging AND Network/Wireless debugging.")
    return 0


# ============================================================================ connecting

def state_dir(args):
    d = Path(args.dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_last_target(args, target):
    (state_dir(args) / "last-device.txt").write_text(target + "\n")


def last_target(args):
    f = Path(args.dir).expanduser() / "last-device.txt"
    return f.read_text().strip() if f.exists() else None


def cmd_connect(args):
    adb_path = find_adb(args.adb)
    target = args.target
    if not target:
        die("give the TV's IP address: tvctl.py connect 192.168.1.42  (run `tvctl.py discover` to find it)")
    if ":" not in target:
        target += ":5555"
    rc, out = Adb(adb_path).run("connect", target, timeout=30)
    say(out)
    low = out.lower()
    if "refused" in low or "cannot connect" in low or "failed to connect" in low or "unable" in low:
        say("\nThe TV refused the connection. On the TV turn on Developer options > USB debugging and")
        say("Network debugging (or Wireless debugging on Android 11+, then use `tvctl.py pair`).")
        return 2
    adb = Adb(adb_path, target)
    told = False
    for _ in range(args.wait):
        st = adb.state()
        if st == "device":
            break
        if st == "unauthorized" and not told:
            say("\n>>> Look at your TV: allow USB/network debugging and tick 'Always allow from this computer'.")
            told = True
        time.sleep(1)
    else:
        say("The TV did not authorise this computer in time. Run connect again and accept the prompt.")
        return 3
    save_last_target(args, target)
    info = device_info(adb)
    say(f"\nConnected to {info['manufacturer']} {info['model']} (Android {info['android']}, API {info['sdk']}).")
    say("Next: python3 tvctl.py backup")
    return 0


def cmd_pair(args):
    adb_path = find_adb(args.adb)
    rc, out = Adb(adb_path).run("pair", args.hostport, args.code, timeout=60)
    say(out)
    if rc != 0 or "Successfully paired" not in out:
        return 2
    say("Paired. Now connect using the IP address & port shown under 'Wireless debugging' on the TV:")
    say("  python3 tvctl.py connect <ip>:<port>")
    return 0


def connected_adb(args):
    adb_path = find_adb(args.adb)
    devs = [s for s, st in device_list(adb_path) if st == "device"]
    serial = args.serial
    if not serial:
        last = last_target(args)
        if last and last not in devs and ":" in last:
            Adb(adb_path).run("connect", last, timeout=20)
            devs = [s for s, st in device_list(adb_path) if st == "device"]
        if last in devs:
            serial = last
        elif len(devs) == 1:
            serial = devs[0]
        elif not devs:
            die("no TV connected. Run: tvctl.py discover, then tvctl.py connect <TV IP>")
        else:
            die("several devices connected (" + ", ".join(devs) + "); choose one with -s SERIAL")
    adb = Adb(adb_path, serial)
    if adb.state() != "device":
        die(f"{serial} is not ready ({adb.state()}). Re-run tvctl.py connect and accept the prompt on the TV.")
    return adb


def device_info(adb):
    props = {}
    for line in adb.out("getprop").splitlines():
        m = re.match(r"\[(.+?)\]: \[(.*)\]", line)
        if m:
            props[m.group(1)] = m.group(2)
    ident = props.get("ro.serialno") or props.get("ro.boot.serialno") or adb.serial
    return {
        "id": ident,
        "adb_target": adb.serial,
        "manufacturer": props.get("ro.product.manufacturer", "?"),
        "brand": props.get("ro.product.brand", "?"),
        "model": props.get("ro.product.model", "?"),
        "device": props.get("ro.product.device", "?"),
        "android": props.get("ro.build.version.release", "?"),
        "sdk": int(props.get("ro.build.version.sdk", "0") or 0),
        "fingerprint": props.get("ro.build.fingerprint", ""),
        "google_tv": "com.google.android.apps.tv.launcherx" in adb.packages("-u"),
        "props": props,
    }


# ============================================================================ backup session

def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower() or "tv"


class Session:
    """One backup folder: snapshot files, journal of changes, restore scripts and a screenshot report."""

    def __init__(self, path):
        self.dir = Path(path)
        self.meta = json.loads((self.dir / "device.json").read_text())
        self.journal = self.dir / "changes.jsonl"
        (self.dir / "screens").mkdir(exist_ok=True)

    @staticmethod
    def latest_for(root, device_id):
        best = None
        for d in sorted(Path(root).glob("*/device.json")):
            try:
                meta = json.loads(d.read_text())
            except ValueError:
                continue
            if meta.get("id") == device_id:
                best = d.parent
        return Session(best) if best else None

    def chain(self):
        """This session followed by every earlier one for the same TV, newest first."""
        out = [self]
        while out[-1].meta.get("previous"):
            d = out[-1].dir.parent / out[-1].meta["previous"]
            if not (d / "device.json").is_file():
                say(f"warning: earlier backup {d} is missing; restoring only back to {out[-1].dir.name}")
                break
            out.append(Session(d))
        return out

    def entries(self):
        if not self.journal.exists():
            return []
        out = []
        for line in self.journal.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def record(self, entry):
        entry = dict(entry, ts=datetime.now().isoformat(timespec="seconds"))
        with open(self.journal, "a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.write_restore_scripts()

    def screenshot(self, adb, label, note=""):
        n = len(list((self.dir / "screens").glob("*.png"))) + 1
        path = self.dir / "screens" / f"{n:02d}-{slug(label)}.png"
        if not adb.screenshot(path):
            say(f"  (could not take a screenshot for '{label}')")
            return None
        size = path.stat().st_size
        blank = size < BLANK_PNG_BYTES
        steps = self._steps()
        steps.append({"label": label, "file": f"screens/{path.name}", "note": note, "blank": blank,
                      "ts": datetime.now().isoformat(timespec="seconds")})
        (self.dir / "report.json").write_text(json.dumps(steps, indent=1))
        self._write_report(steps)
        say(f"  screenshot: {path}" + ("   <- looks blank/black" if blank else ""))
        return path

    def _steps(self):
        f = self.dir / "report.json"
        return json.loads(f.read_text()) if f.exists() else []

    def _write_report(self, steps):
        m = self.meta
        rows = []
        for s in steps:
            warn = "<p class=warn>Screenshot is almost empty: black screen, HDMI input or protected video.</p>" \
                if s.get("blank") else ""
            rows.append(f"<section><h2>{html.escape(s['label'])}</h2><p class=t>{s['ts']} "
                        f"{html.escape(s.get('note', ''))}</p>{warn}<img src=\"{s['file']}\" loading=lazy></section>")
        doc = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>TV makeover log</title><style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1000px;padding:16px;background:#111;color:#eee}}
img{{width:100%;border-radius:8px;border:1px solid #333}} h2{{font-size:17px;margin:28px 0 4px}}
.t{{color:#999;margin:0 0 8px}} .warn{{color:#fc6}} code{{background:#222;padding:1px 4px;border-radius:4px}}</style>
<h1>{html.escape(m['manufacturer'])} {html.escape(m['model'])}</h1>
<p>Backup taken {m['backup_time']}. To undo everything run <code>restore.sh</code> (macOS/Linux) or
<code>restore.cmd</code> (Windows) from this folder, or <code>python3 tvctl.py restore</code>.</p>
{''.join(rows)}"""
        (self.dir / "report.html").write_text(doc)

    # ------------------------------------------------------------------ restore planning

    def restore_plan(self):
        pkgs, settings, home, installed = [], {}, None, None
        for e in self.entries():
            t = e.get("type")
            if t == "package" and e.get("action") == "disable" and e.get("before") == "enabled":
                if e["pkg"] not in pkgs:
                    pkgs.append(e["pkg"])
            elif t == "setting":
                settings.setdefault((e["ns"], e["key"]), e["before"])
            elif t == "home" and home is None:
                home = e["before"]
            elif t == "install" and installed is None:
                installed = e["before"]
        pkgs.reverse()
        return pkgs, settings, home, installed

    def write_restore_scripts(self):
        pkgs, settings, home, installed = self.restore_plan()
        m = self.meta
        sh = [
            "#!/bin/sh",
            f"# Restore {m['manufacturer']} {m['model']} to how it was on {m['backup_time']}.",
            "# Re-enables every app tvctl disabled, puts changed settings and the home screen back,",
            "# and removes TV Home. Nothing else is touched. Safe to run more than once.",
            "#   sh restore.sh [TV_IP[:PORT]] [--keep-tv-home]",
            "# Needs adb (Android platform-tools); set ADB=/path/to/adb if it is not on your PATH.",
            'ADB="${ADB:-adb}"',
            f'TARGET="{m["adb_target"]}"',
            "KEEP_HOME=0",
            'for arg in "$@"; do case "$arg" in --keep-tv-home) KEEP_HOME=1;; *) TARGET="$arg";; esac; done',
            'case "$TARGET" in *:*) ;; *.*) TARGET="$TARGET:5555";; esac',
            'case "$TARGET" in *:*) "$ADB" connect "$TARGET" >/dev/null 2>&1;; esac',
            'if [ "$("$ADB" -s "$TARGET" get-state 2>/dev/null)" != "device" ]; then',
            '  echo "Cannot reach the TV at $TARGET. Turn on network debugging, then run: sh restore.sh <TV IP>"',
            "  exit 1",
            "fi",
            'a() { "$ADB" -s "$TARGET" "$@"; }',
            f'echo "Re-enabling {len(pkgs)} app(s)..."',
        ]
        cmd = [
            "@echo off",
            f"rem Restore {m['manufacturer']} {m['model']} to how it was on {m['backup_time']}.",
            "rem Re-enables every app tvctl disabled, puts changed settings and the home screen back,",
            "rem and removes TV Home. Nothing else is touched. Safe to run more than once.",
            "rem   restore.cmd [TV_IP[:PORT]] [--keep-tv-home]",
            "setlocal",
            'if "%ADB%"=="" set "ADB=adb"',
            f'set "TARGET={m["adb_target"]}"',
            'set "KEEP_HOME=0"',
            ":args",
            'if "%~1"=="" goto run',
            'if /I "%~1"=="--keep-tv-home" (set "KEEP_HOME=1") else (set "TARGET=%~1")',
            "shift",
            "goto args",
            ":run",
            'echo %TARGET% | find "." >nul && (echo %TARGET% | find ":" >nul || set "TARGET=%TARGET%:5555")',
            '"%ADB%" connect %TARGET% >nul 2>&1',
            'for /f "usebackq delims=" %%s in (`"%ADB%" -s %TARGET% get-state 2^>nul`) do set "STATE=%%s"',
            'if not "%STATE%"=="device" (echo Cannot reach the TV at %TARGET%. Turn on network debugging, then run: restore.cmd TV_IP & exit /b 1)',
            f"echo Re-enabling {len(pkgs)} app(s)...",
        ]
        for p in pkgs:
            sh.append(f'a shell pm enable --user 0 {p} >/dev/null 2>&1 && echo "  enabled {p}" || echo "  could not enable {p}"')
            cmd.append(f'"%ADB%" -s %TARGET% shell pm enable --user 0 {p} >nul 2>&1 && echo   enabled {p}')
        if settings:
            sh.append('echo "Restoring settings..."')
            cmd.append("echo Restoring settings...")
        for (ns, key), before in settings.items():
            if before is None:
                line = f"settings delete {ns} {key}"
            elif SAFE_VALUE.match(before):
                line = f"settings put {ns} {key} {before}"
            else:
                sh.append(f"# {ns}/{key} had an unusual value; run `python3 tvctl.py restore` to restore it.")
                continue
            sh.append(f"a shell '{line}' >/dev/null")
            cmd.append(f'"%ADB%" -s %TARGET% shell "{line}" >nul')
        if home is not None:
            sh.append('echo "Restoring the home screen..."')
            cmd.append("echo Restoring the home screen...")
            for line in home_restore_commands(home):
                sh.append(f"a shell '{line}' >/dev/null 2>&1")
                cmd.append(f'"%ADB%" -s %TARGET% shell "{line}" >nul 2>&1')
        if installed == "absent":
            sh.append(f'if [ $KEEP_HOME = 0 ]; then a uninstall {HOME_PKG} >/dev/null 2>&1 && echo "Removed TV Home"; fi')
            cmd.append(f'if "%KEEP_HOME%"=="0" "%ADB%" -s %TARGET% uninstall {HOME_PKG} >nul 2>&1 && echo Removed TV Home')
        sh += ["a shell input keyevent KEYCODE_HOME", f'echo "Done: the TV is back to how it was on {m["backup_time"]}."']
        cmd += ['"%ADB%" -s %TARGET% shell input keyevent KEYCODE_HOME',
                f"echo Done: the TV is back to how it was on {m['backup_time']}."]
        if m.get("previous"):
            prev = m["previous"]
            sh += [f'PREV="$(dirname "$0")/../{prev}/restore.sh"',
                   f'if [ -f "$PREV" ]; then echo "Now undoing the earlier session ({prev})..."; sh "$PREV" "$@"; fi']
            cmd += [f'if exist "%~dp0..\\{prev}\\restore.cmd" call "%~dp0..\\{prev}\\restore.cmd" %*']
        cmd.append("endlocal")
        (self.dir / "restore.sh").write_text("\n".join(sh) + "\n")
        os.chmod(self.dir / "restore.sh", 0o755)
        (self.dir / "restore.cmd").write_bytes(("\r\n".join(cmd) + "\r\n").encode())


def home_restore_commands(before):
    """Shell commands that give the home screen back to whoever had it before."""
    cmds = []
    holders = before.get("role_holders")
    if holders is not None:
        if holders:
            cmds += [f"cmd role add-role-holder --user 0 {HOME_ROLE} {h}" for h in holders]
        cmds.append(f"cmd role remove-role-holder --user 0 {HOME_ROLE} {HOME_PKG}")
    elif before.get("component") and "ResolverActivity" not in before["component"]:
        cmds.append(f"cmd package set-home-activity --user 0 {before['component']}")
    return cmds


def session_for(args, adb, auto_backup=True):
    info = device_info(adb)
    s = Session.latest_for(state_dir(args), info["id"])
    if s is None:
        if not auto_backup:
            die("no backup found for this TV. Run: tvctl.py backup")
        say("No backup for this TV yet: backing up first.")
        s = do_backup(args, adb, info)
    return s


def home_state(adb):
    state = {"sdk": adb.sdk}
    if adb.sdk >= 29:
        rc, out = adb.shell(f"cmd role get-role-holders --user 0 {HOME_ROLE}")
        if rc == 0 and "Exception" not in out and "Unknown" not in out:
            state["role_holders"] = [h for h in re.split(r"[;\s]+", out.strip().strip("[]")) if PKG_RE.match(h)]
    out = adb.out("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                  "-c android.intent.category.HOME")
    comps = [l.strip() for l in out.splitlines() if re.match(r"^\s*[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+\s*$", l)]
    state["component"] = comps[-1] if comps else None
    return state


def do_backup(args, adb, info=None):
    info = info or device_info(adb)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = state_dir(args) / f"{slug(info['manufacturer'])}-{slug(info['model'])}-{stamp}"
    d.mkdir(parents=True)
    say(f"Backing up {info['manufacturer']} {info['model']} to {d}")
    props = info.pop("props")
    info["backup_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    info["tvctl_version"] = VERSION
    prev = Session.latest_for(state_dir(args), info["id"])
    if prev:
        info["previous"] = prev.dir.name
        say(f"Note: an earlier backup exists ({prev.dir.name}). The restore scripts of this backup also run")
        say("      the earlier ones, so restoring always goes all the way back to the original state.")
    (d / "getprop.txt").write_text("\n".join(f"[{k}]: [{v}]" for k, v in sorted(props.items())) + "\n")
    allp, disabled, system = adb.packages("-u"), adb.packages("-d"), adb.packages("-s")
    installed = adb.packages()
    pk = {p: {"installed": p in installed, "enabled": p in installed and p not in disabled, "system": p in system}
          for p in sorted(allp)}
    (d / "packages.json").write_text(json.dumps(pk, indent=1))
    (d / "packages-paths.txt").write_text(adb.out("pm list packages -f -u") + "\n")
    for ns in ("global", "secure", "system"):
        (d / f"settings-{ns}.txt").write_text(adb.out(f"settings list {ns}") + "\n")
    (d / "home.json").write_text(json.dumps(home_state(adb), indent=1))
    (d / "launchable-apps.txt").write_text("\n".join(adb.leanback_apps()) + "\n")
    (d / "device.json").write_text(json.dumps(info, indent=1))
    (d / "README.txt").write_text(
        "TV backup made by tvctl.py before any change.\n\n"
        "device.json, getprop.txt   what the TV is\n"
        "packages.json              every package and whether it was enabled\n"
        "settings-*.txt             all system settings\n"
        "home.json                  which app was the home screen\n"
        "changes.jsonl              every change tvctl made, written before making it\n"
        "restore.sh / restore.cmd   undo all of it (macOS/Linux / Windows)\n"
        "report.html, screens/      screenshots from the TV after each step\n\n"
        "Apps are only ever disabled, never uninstalled, so no app data needs backing up.\n")
    s = Session(d)
    s.write_restore_scripts()
    s.screenshot(adb, "before any change", "What the TV showed when the backup was taken.")
    n_dis = sum(1 for v in pk.values() if v["installed"] and not v["enabled"])
    say(f"Saved {len(pk)} packages ({n_dis} already disabled), settings and home screen.")
    say(f"Restore scripts: {d / 'restore.sh'}  and  {d / 'restore.cmd'}")
    return s


def cmd_backup(args):
    adb = connected_adb(args)
    do_backup(args, adb)
    return 0


# ============================================================================ debloat

def load_catalog():
    return json.loads(CATALOG_PATH.read_text())


def is_protected(pkg, cat):
    return pkg in cat["protected"] or any(pkg.startswith(p) for p in cat["protected_prefixes"])


def build_plan(adb, cat):
    enabled = adb.packages("-e")
    system = adb.packages("-s")
    icons = set(adb.leanback_apps())
    plan = {"batch": [], "ask": [], "review": [], "launcher": []}
    for pkg, meta in cat["packages"].items():
        if pkg not in enabled or is_protected(pkg, cat):
            continue
        item = {"pkg": pkg, "label": meta["label"], "group": meta["group"], "what": meta["what"],
                "breaks": meta["breaks"], "has_icon": pkg in icons}
        if meta["group"] == "launcher":
            plan["launcher"].append(item)
        elif meta["group"] in ("ads", "telemetry") and not meta.get("ask"):
            plan["batch"].append(item)
        elif meta["group"] in ("app", "feature") and pkg not in system:
            continue  # you installed it yourself: not bloat
        else:
            plan["ask"].append(item)
    for pkg in sorted(enabled & system):
        if pkg in cat["packages"] or is_protected(pkg, cat):
            continue
        words = [w for w in cat["suspicious_words"] if re.search(rf"(^|[._]){w}", pkg)]
        if any(pkg.startswith(p) for p in cat["oem_prefixes"]) or words:
            plan["review"].append({"pkg": pkg, "has_icon": pkg in icons, "hint": ", ".join(words)})
    return plan


def health_problem(adb):
    for _ in range(30):
        if adb.state() == "device":
            break
        if adb.serial and ":" in adb.serial:
            Adb(adb.path).run("connect", adb.serial, timeout=15)  # network ADB drops when the TV reboots
        time.sleep(2)
    else:
        return "the TV stopped answering adb"
    rc, out = adb.shell("pidof system_server")
    if not out.strip():
        rc, out = adb.shell("ps -A 2>/dev/null | grep system_server || ps | grep system_server")
        if "system_server" not in out:
            return "Android's system_server is not running (the TV may be rebooting)"
    focus = adb.focus()
    if re.search(r"Application Error|Not Responding|isn't responding|has stopped|keeps stopping", focus):
        return f"an error dialog appeared ({focus})"
    return None


def disable_packages(args, adb, session, pkgs, prompter, label="apps"):
    cat = load_catalog()
    done, failed, reverted = [], [], []
    batch = max(1, args.batch)
    for i in range(0, len(pkgs), batch):
        chunk = pkgs[i:i + batch]
        this = []
        for pkg in chunk:
            if not PKG_RE.match(pkg):
                say(f"  skip {pkg}: not a package name")
                continue
            if is_protected(pkg, cat):
                say(f"  skip {pkg}: protected (needed for the TV to work)")
                continue
            st = adb.package_state(pkg)
            if st != "enabled":
                say(f"  skip {pkg}: {st}")
                continue
            session.record({"type": "package", "action": "disable", "pkg": pkg, "before": "enabled"})
            rc, out = adb.shell(f"pm disable-user --user 0 {pkg}")
            if "disabled" not in out:
                say(f"  could not disable {pkg}: {out.splitlines()[-1] if out else rc}")
                failed.append(pkg)
                continue
            time.sleep(args.settle)
            problem = health_problem(adb)
            if problem:
                say(f"  ! {problem} after disabling {pkg}: turning it back on")
                enable_package(adb, session, pkg)
                adb.key("KEYCODE_BACK")
                reverted.append(pkg)
                continue
            say(f"  disabled {pkg}")
            this.append(pkg)
        if not this:
            continue
        adb.key("KEYCODE_HOME")
        time.sleep(2.5)
        shot = session.screenshot(adb, f"after disabling {label} {i // batch + 1}", ", ".join(this))
        problem = health_problem(adb)
        ok = problem is None
        if problem:
            say(f"  ! {problem}")
        if ok and prompter.tty and not prompter.yes and not prompter.answers:
            ok = prompter.yes_no("batch_ok", "  Look at the TV (and the screenshot). Does everything look normal?", True)
        if not ok:
            say("  Turning this batch back on.")
            for pkg in this:
                enable_package(adb, session, pkg)
            reverted += this
            continue
        done += this
    return done, failed, reverted


def enable_package(adb, session, pkg):
    rc, out = adb.shell(f"pm enable --user 0 {pkg}")
    session.record({"type": "package", "action": "enable", "pkg": pkg, "before": "disabled"})
    return "enabled" in out


def print_plan(plan):
    def line(it):
        icon = "app" if it.get("has_icon") else "background"
        return f"  - {it['label']}  ({it['pkg']}, {icon})\n      {it['what']}  Turning it off affects: {it['breaks']}"
    if plan["batch"]:
        say("\nAds and tracking (safe to turn off):")
        for it in plan["batch"]:
            say(line(it))
    if plan["ask"]:
        say("\nApps and features you might use (asked one by one, kept unless you say so):")
        for it in plan["ask"]:
            say(line(it))
    if plan["review"]:
        say("\nOther manufacturer packages (unknown: kept unless you choose them):")
        for it in plan["review"]:
            hint = f"  name suggests: {it['hint']}" if it["hint"] else ""
            say(f"  - {it['pkg']}  ({'app' if it['has_icon'] else 'background'}){hint}")
    if plan["launcher"]:
        say("\nStock home screens (handled by `tvctl.py home set-default`, only after TV Home works):")
        for it in plan["launcher"]:
            say(f"  - {it['label']}  ({it['pkg']})")
    if not any(plan.values()):
        say("Nothing known to disable on this TV.")


def cmd_debloat(args):
    adb = connected_adb(args)
    cat = load_catalog()
    if args.plan:
        plan = build_plan(adb, cat)
        if args.json:
            print(json.dumps(plan, indent=1))
        else:
            print_plan(plan)
        return 0
    session = session_for(args, adb)
    prompter = Prompter(args.yes, load_answers(args))
    if args.apply:
        pkgs = args.apply
        for p in pkgs:
            if p in cat["packages"] and cat["packages"][p]["group"] == "launcher":
                die(f"{p} is a home screen: use `tvctl.py home set-default --disable-stock {p}` instead")
        done, failed, reverted = disable_packages(args, adb, session, pkgs, prompter)
        report_disable(done, failed, reverted, session)
        return 0 if not reverted else 4
    if not prompter.can_ask:
        die("debloat asks questions; run it in a terminal, or use --plan and then --apply PKG...")
    plan = build_plan(adb, cat)
    print_plan(plan)
    chosen = []
    if plan["batch"]:
        say("")
        if prompter.yes_no("debloat.batch", f"Turn off the {len(plan['batch'])} ads/tracking packages above?", True):
            keep = prompter.text("debloat.batch.keep", "Numbers to KEEP from that list (e.g. 2,5), Enter for none:")
            skip = {int(x) - 1 for x in re.findall(r"\d+", keep)}
            chosen += [it["pkg"] for n, it in enumerate(plan["batch"]) if n not in skip]
    for it in plan["ask"]:
        say(f"\n{it['label']} ({it['pkg']})\n  {it['what']}\n  If off: {it['breaks']}")
        if prompter.yes_no(f"debloat.ask.{it['pkg']}", "  Turn it off?", False):
            chosen.append(it["pkg"])
    if plan["review"]:
        say("\nOther manufacturer packages:")
        for n, it in enumerate(plan["review"], 1):
            say(f"  {n}. {it['pkg']}" + (f"  ({it['hint']})" if it["hint"] else ""))
        pick = prompter.text("debloat.review", "Numbers to turn off (Enter keeps them all):")
        for x in re.findall(r"\d+", pick):
            if 1 <= int(x) <= len(plan["review"]):
                chosen.append(plan["review"][int(x) - 1]["pkg"])
    if not chosen:
        say("Nothing to change.")
        return 0
    say(f"\nTurning off {len(chosen)} package(s), {args.batch} at a time, with a screenshot after each batch.")
    done, failed, reverted = disable_packages(args, adb, session, chosen, prompter)
    report_disable(done, failed, reverted, session)
    return 0


def report_disable(done, failed, reverted, session):
    say(f"\nDisabled {len(done)}; could not disable {len(failed)}; turned back on {len(reverted)}.")
    if reverted:
        say("Turned back on because something went wrong: " + ", ".join(reverted))
    say(f"Undo any time: {session.dir / 'restore.sh'} (or restore.cmd), or tvctl.py undo PKG")
    say(f"Screenshots: {session.dir / 'report.html'}")


def cmd_undo(args):
    adb = connected_adb(args)
    session = session_for(args, adb, auto_backup=False)
    for pkg in args.packages:
        ok = enable_package(adb, session, pkg)
        say(f"  {'enabled' if ok else 'could not enable'} {pkg}")
    adb.key("KEYCODE_HOME")
    time.sleep(2)
    session.screenshot(adb, "after undo", ", ".join(args.packages))
    return 0


# ============================================================================ tweaks

def get_setting(adb, ns, key):
    v = adb.out(f"settings get {ns} {key}").strip()
    return None if v in ("null", "") else v


def apply_tweak(adb, session, tweak):
    for ns, key, value in tweak["set"]:
        before = get_setting(adb, ns, key)
        if before == value:
            continue
        session.record({"type": "setting", "ns": ns, "key": key, "before": before, "after": value,
                        "tweak": tweak["id"]})
        adb.shell(f"settings put {ns} {key} {value}")
    say(f"  applied: {tweak['title']}")


def cmd_tweaks(args):
    adb = connected_adb(args)
    available = [t for t in TWEAKS if adb.sdk >= t["min_sdk"]]
    if args.plan:
        rows = [{"id": t["id"], "title": t["title"], "why": t["why"], "default": t["default"],
                 "current": {f"{ns}/{k}": get_setting(adb, ns, k) for ns, k, _ in t["set"]}} for t in available]
        if args.json:
            print(json.dumps(rows, indent=1))
        else:
            for r in rows:
                say(f"- {r['id']}: {r['title']}\n    {r['why']}\n    now: {r['current']}")
        return 0
    session = session_for(args, adb)
    prompter = Prompter(args.yes, load_answers(args))
    if args.apply:
        chosen = [t for t in available if t["id"] in args.apply]
        unknown = set(args.apply) - {t["id"] for t in available}
        if unknown:
            die("unknown or unsupported tweak(s): " + ", ".join(sorted(unknown)))
    else:
        if not prompter.can_ask:
            die("tweaks asks questions; run it in a terminal, or use --plan and then --apply ID...")
        chosen = []
        for t in available:
            say(f"\n{t['title']}\n  {t['why']}")
            if prompter.yes_no(f"tweak.{t['id']}", "  Apply?", t["default"]):
                chosen.append(t)
    for t in chosen:
        apply_tweak(adb, session, t)
    if chosen:
        adb.key("KEYCODE_HOME")
        time.sleep(2)
        session.screenshot(adb, "after tweaks", ", ".join(t["id"] for t in chosen))
    return 0


# ============================================================================ home screen

def home_installed(adb):
    return HOME_PKG in adb.packages()


def cmd_home(args):
    return {"install": home_install, "dock": home_dock, "set-default": home_set_default,
            "wallpapers": home_wallpapers, "setup": home_setup, "status": home_status}[args.action](args)


def home_install(args, adb=None, session=None):
    adb = adb or connected_adb(args)
    session = session or session_for(args, adb)
    apk = Path(args.apk or DEFAULT_APK)
    if not apk.is_file():
        die(f"APK not found: {apk}. Build it with launcher/build.sh or pass --apk")
    before = "installed" if home_installed(adb) else "absent"
    session.record({"type": "install", "pkg": HOME_PKG, "before": before})
    say(f"Installing TV Home from {apk} ...")
    rc, out = adb.run("install", "-r", str(apk), timeout=300)
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out:
        die("a TV Home signed with a different key is installed. Remove it first with "
            f"`adb uninstall {HOME_PKG}` (only TV Home is removed), then run this again.")
    if "Success" not in out:
        die("install failed: " + out)
    adb.shell(f"am start -n {HOME_COMPONENT}")
    time.sleep(4)
    shot = session.screenshot(adb, "TV Home first launch", "Opened directly; not the default home screen yet.")
    fg = adb.resumed()
    say(f"  foreground: {fg or 'unknown'}")
    if HOME_PKG not in fg:
        say("  ! TV Home did not come to the front; check the screenshot.")
        return 5
    return 0


def launcher_apps(adb):
    """(id, label) pairs as TV Home sees them, from the apps.tsv it writes; falls back to package names."""
    out = adb.out(f"cat {HOME_FILES}/apps.tsv")
    apps = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and not line.startswith("#"):
            apps.append((parts[0], parts[1]))
    if apps:
        return apps
    return [(p, p) for p in adb.leanback_apps() if p != HOME_PKG] + [("settings", "Settings")]


def home_dock(args, adb=None, session=None):
    adb = adb or connected_adb(args)
    session = session or session_for(args, adb)
    if not home_installed(adb):
        die("TV Home is not installed yet: tvctl.py home install")
    ids = list(args.ids or [])
    if not ids:
        prompter = Prompter(args.yes, load_answers(args))
        apps = launcher_apps(adb)
        if not prompter.can_ask:
            say("Apps you can put in the dock:")
            for i, (aid, label) in enumerate(apps, 1):
                say(f"  {i}. {label}  ({aid})")
            die("give the dock as ids: tvctl.py home dock com.netflix.ninja com.google.android.youtube.tv settings")
        say("Apps on the TV:")
        for i, (aid, label) in enumerate(apps, 1):
            say(f"  {i:2}. {label}  ({aid})")
        pick = prompter.text("home.dock", "Dock apps in order, as numbers (e.g. 3,1,7,12):")
        for x in re.findall(r"\d+", pick):
            if 1 <= int(x) <= len(apps):
                ids.append(apps[int(x) - 1][0])
    ids = [i for i in ids if re.match(r"^[A-Za-z0-9_.:/-]+$", i)]
    if not ids:
        die("no dock apps chosen")
    adb.shell(f"am start -n {HOME_COMPONENT} --es dock {','.join(ids)}")
    time.sleep(3)
    say("Dock: " + ", ".join(ids))
    session.screenshot(adb, "dock updated", ", ".join(ids))
    return 0


def home_set_default(args, adb=None, session=None, prompter=None):
    adb = adb or connected_adb(args)
    session = session or session_for(args, adb)
    prompter = prompter or Prompter(args.yes, load_answers(args))
    if not home_installed(adb):
        die("TV Home is not installed yet: tvctl.py home install")
    before = home_state(adb)
    session.record({"type": "home", "before": before, "after": HOME_COMPONENT})
    if "role_holders" in before:
        rc, out = adb.shell(f"cmd role add-role-holder --user 0 {HOME_ROLE} {HOME_PKG}")
        say(f"  home role -> TV Home {'(ok)' if rc == 0 else out}")
    else:
        rc, out = adb.shell(f"cmd package set-home-activity --user 0 {HOME_COMPONENT}")
        say(f"  default home -> TV Home {'(ok)' if rc == 0 else out}")
    if press_home_lands_on_us(adb, session, "Home button after making TV Home the default"):
        say("TV Home is now the home screen.")
        return 0
    # The stock launcher still grabs the Home button: offer to disable it (reversible).
    cat = load_catalog()
    plan = build_plan(adb, cat)
    stock = [it for it in plan["launcher"] if it["pkg"] != "com.google.android.tungsten.setupwraith"]
    wanted = list(args.disable_stock or [])
    if not wanted:
        for it in stock:
            say(f"\nThe stock home screen still opens: {it['label']} ({it['pkg']}).\n  If off: {it['breaks']}")
            if prompter.can_ask and prompter.yes_no(f"home.disable.{it['pkg']}", "  Turn it off so TV Home sticks?", True):
                wanted.append(it["pkg"])
    if not wanted:
        say("Left the stock home screen on. TV Home is still in your apps; the Home button opens the stock one.")
        return 6
    for pkg in wanted:
        if pkg not in cat["packages"] or cat["packages"][pkg]["group"] != "launcher":
            die(f"{pkg} is not a known home screen")
        if adb.package_state(pkg) != "enabled":
            continue
        session.record({"type": "package", "action": "disable", "pkg": pkg, "before": "enabled"})
        adb.shell(f"pm disable-user --user 0 {pkg}")
        say(f"  disabled {pkg}")
    if "role_holders" in before:
        adb.shell(f"cmd role add-role-holder --user 0 {HOME_ROLE} {HOME_PKG}")
    if press_home_lands_on_us(adb, session, "Home button after turning off the stock home"):
        say("TV Home is now the home screen.")
        return 0
    fg = adb.resumed()
    wraith = "com.google.android.tungsten.setupwraith"
    if wraith in fg and adb.package_state(wraith) == "enabled":
        say("\nGoogle TV's setup app took over the Home button.")
        say("  If off: " + cat["packages"][wraith]["breaks"])
        if wraith in (args.disable_stock or []) or \
                (prompter.can_ask and prompter.yes_no(f"home.disable.{wraith}", "  Turn it off too?", True)):
            session.record({"type": "package", "action": "disable", "pkg": wraith, "before": "enabled"})
            adb.shell(f"pm disable-user --user 0 {wraith}")
            if press_home_lands_on_us(adb, session, "Home button after turning off Google TV setup"):
                say("TV Home is now the home screen.")
                return 0
    say("! The Home button still doesn't open TV Home. Run the restore script to undo, or tvctl.py undo PKG.")
    return 7


def press_home_lands_on_us(adb, session, label):
    adb.key("KEYCODE_HOME")
    time.sleep(3)
    fg = adb.resumed()
    session.screenshot(adb, label, f"foreground: {fg}")
    say(f"  after Home: {fg or 'unknown'}")
    return HOME_PKG in fg


def home_wallpapers(args):
    adb = connected_adb(args)
    session = session_for(args, adb)
    src = Path(args.folder).expanduser()
    files = [f for f in sorted(src.iterdir()) if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")] \
        if src.is_dir() else []
    if not files:
        die(f"no .jpg/.png/.webp pictures in {src}")
    adb.shell(f"mkdir -p {HOME_FILES}/wallpapers")
    for f in files:
        rc, out = adb.run("push", str(f), f"{HOME_FILES}/wallpapers/{f.name}", timeout=300)
        say(f"  {'pushed' if rc == 0 else 'failed'} {f.name}")
    adb.shell(f"am start -n {HOME_COMPONENT} --ez next_wallpaper true")
    time.sleep(4)
    session.screenshot(adb, "your wallpapers", f"{len(files)} picture(s)")
    return 0


def home_setup(args):
    adb = connected_adb(args)
    session = session_for(args, adb)
    prompter = Prompter(args.yes, load_answers(args))
    if not prompter.can_ask:
        die("home setup asks questions; run it in a terminal, or use home install / dock / set-default")
    rc = home_install(args, adb, session)
    if rc:
        return rc
    if prompter.yes_no("home.pick_dock", "Choose the dock apps now? (otherwise popular apps are picked)", True):
        home_dock(args, adb, session)
    return home_set_default(args, adb, session, prompter)


def home_status(args):
    adb = connected_adb(args)
    say(json.dumps({"installed": home_installed(adb), "home": home_state(adb), "foreground": adb.resumed(),
                    "dock": adb.out(f"cat {HOME_FILES}/dock.txt")}, indent=1))
    return 0


# ============================================================================ restore & status

def cmd_restore(args):
    adb_path = find_adb(args.adb)
    if args.backup:
        newest = Session(args.backup)
    else:
        adb = connected_adb(args)
        newest = Session.latest_for(state_dir(args), device_info(adb)["id"])
        if newest is None:
            die("no backup found for this TV")
    chain = newest.chain()
    target = args.serial or newest.meta["adb_target"]
    if ":" in target:
        Adb(adb_path).run("connect", target, timeout=20)
    adb = Adb(adb_path, target)
    if adb.state() != "device":
        die(f"cannot reach the TV at {target}; connect it first (tvctl.py connect <ip>)")
    enabled_again = []
    for session in chain:
        enabled_again += restore_session(adb, session, args)
    original = json.loads((chain[-1].dir / "packages.json").read_text())
    now_disabled = adb.packages("-d")
    drift = [p for p, v in original.items() if v["installed"] and v["enabled"] and p in now_disabled]
    if drift and (args.all or Prompter(args.yes, load_answers(args)).yes_no(
            "restore.drift", f"{len(drift)} other app(s) were enabled at backup time and are disabled now "
                             f"({', '.join(drift[:5])}{'...' if len(drift) > 5 else ''}). Re-enable them too?", True)):
        for p in drift:
            adb.shell(f"pm enable --user 0 {p}")
            say(f"  enabled {p}")
        enabled_again += drift
    adb.key("KEYCODE_HOME")
    time.sleep(3)
    newest.screenshot(adb, "after restore", f"foreground: {adb.resumed()}")
    still = [p for p in enabled_again if p in adb.packages("-d")]
    if still:
        say("Still disabled (re-enable in Settings > Apps): " + ", ".join(still))
        return 8
    say("Done: everything tvctl changed has been put back.")
    return 0


def restore_session(adb, session, args):
    pkgs, settings, home, installed = session.restore_plan()
    say(f"Restoring from {session.dir}")
    for p in pkgs:
        rc, out = adb.shell(f"pm enable --user 0 {p}")
        say(f"  {'enabled' if 'enabled' in out else 'could not enable'} {p}")
    for (ns, key), before in settings.items():
        if before is None:
            adb.shell(f"settings delete {ns} {key}")
        else:
            adb.shell(f"settings put {ns} {key} {shlex.quote(before)}")
        say(f"  {ns}/{key} -> {before if before is not None else '(unset)'}")
    if home is not None:
        for line in home_restore_commands(home):
            adb.shell(line)
        say("  home screen restored")
    if installed == "absent" and not args.keep_tv_home and home_installed(adb):
        adb.run("uninstall", HOME_PKG)
        say("  removed TV Home")
    session.record({"type": "restore", "packages": pkgs, "settings": [f"{n}/{k}" for n, k in settings]})
    return pkgs


def cmd_status(args):
    adb = connected_adb(args)
    session = session_for(args, adb, auto_backup=False)
    pkgs, settings, home, installed = session.restore_plan()
    say(f"Backup: {session.dir}")
    say(f"Disabled by tvctl ({len(pkgs)}): " + (", ".join(pkgs) or "none"))
    say("Settings changed: " + (", ".join(f"{n}/{k}" for n, k in settings) or "none"))
    say(f"Home screen changed: {'yes' if home is not None else 'no'};  TV Home installed by tvctl: "
        f"{'yes' if installed == 'absent' else 'no'}")
    say(f"Restore: {session.dir / 'restore.sh'}  |  {session.dir / 'restore.cmd'}")
    return 0


def cmd_screenshot(args):
    adb = connected_adb(args)
    session = session_for(args, adb)
    path = session.screenshot(adb, args.name or "screenshot", args.note or "")
    return 0 if path else 1


def cmd_info(args):
    adb = connected_adb(args)
    info = device_info(adb)
    info.pop("props")
    info["foreground"] = adb.resumed()
    info["home"] = home_state(adb)
    print(json.dumps(info, indent=1))
    return 0


def load_answers(args):
    if not getattr(args, "answers", None):
        return {}
    return json.loads(Path(args.answers).read_text())


# ============================================================================ main

def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    p = argparse.ArgumentParser(prog="tvctl.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--adb", help="path to adb")
    p.add_argument("-s", "--serial", help="adb serial / ip:port of the TV")
    p.add_argument("--dir", default="tv-backups", help="where backups live (default ./tv-backups)")
    p.add_argument("--yes", action="store_true", help="accept the default answer to every question")
    p.add_argument("--answers", help="JSON file of answers to questions (for scripting)")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="find Android TVs on the network")
    d.add_argument("--subnet", help="e.g. 192.168.1.0/24 (default: your /24)")
    d.add_argument("--timeout", type=float, default=3.0)
    d.add_argument("--all", action="store_true", help="also list non-TV devices")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_discover)

    c = sub.add_parser("connect", help="connect over network ADB")
    c.add_argument("target", nargs="?", help="TV IP or IP:PORT")
    c.add_argument("--wait", type=int, default=90, help="seconds to wait for the on-TV prompt")
    c.set_defaults(fn=cmd_connect)

    pr = sub.add_parser("pair", help="pair with Wireless debugging (Android 11+)")
    pr.add_argument("hostport", help="IP:PORT shown under 'Pair device with pairing code'")
    pr.add_argument("code", help="six-digit pairing code")
    pr.set_defaults(fn=cmd_pair)

    sub.add_parser("info", help="show device details").set_defaults(fn=cmd_info)
    sub.add_parser("backup", help="back up packages, settings and home screen").set_defaults(fn=cmd_backup)

    db = sub.add_parser("debloat", help="turn off ads, tracking and unwanted apps (reversible)")
    db.add_argument("--plan", action="store_true", help="only list what could be turned off")
    db.add_argument("--json", action="store_true", help="with --plan: JSON output")
    db.add_argument("--apply", nargs="+", metavar="PKG", help="turn off exactly these packages")
    db.add_argument("--batch", type=int, default=8, help="packages per batch between screenshots")
    db.add_argument("--settle", type=float, default=1.0, help="seconds to wait after each package")
    db.set_defaults(fn=cmd_debloat)

    u = sub.add_parser("undo", help="turn packages back on")
    u.add_argument("packages", nargs="+")
    u.set_defaults(fn=cmd_undo)

    tw = sub.add_parser("tweaks", help="optional speed and privacy settings")
    tw.add_argument("--plan", action="store_true")
    tw.add_argument("--json", action="store_true")
    tw.add_argument("--apply", nargs="+", metavar="ID")
    tw.set_defaults(fn=cmd_tweaks)

    h = sub.add_parser("home", help="install and configure the TV Home launcher")
    h.add_argument("action", choices=["setup", "install", "dock", "set-default", "wallpapers", "status"])
    h.add_argument("ids", nargs="*", help="dock: app ids in order (package names, 'settings', 'input:<id>')")
    h.add_argument("--apk", help=f"APK to install (default {DEFAULT_APK})")
    h.add_argument("--folder", help="wallpapers: folder of your photos")
    h.add_argument("--disable-stock", nargs="+", metavar="PKG", help="set-default: stock home screens to turn off")
    h.set_defaults(fn=cmd_home)

    r = sub.add_parser("restore", help="undo everything tvctl changed")
    r.add_argument("backup", nargs="?", help="backup folder (default: latest for the connected TV)")
    r.add_argument("--keep-tv-home", action="store_true", help="leave the TV Home app installed")
    r.add_argument("--all", action="store_true", help="also re-enable apps disabled outside tvctl since the backup")
    r.set_defaults(fn=cmd_restore)

    sub.add_parser("status", help="what tvctl has changed").set_defaults(fn=cmd_status)

    sc = sub.add_parser("screenshot", help="save a screenshot from the TV into the backup folder")
    sc.add_argument("name", nargs="?")
    sc.add_argument("--note")
    sc.set_defaults(fn=cmd_screenshot)

    args = p.parse_args(argv)
    if args.cmd == "home" and args.action == "wallpapers" and not args.folder:
        if args.ids:
            args.folder = args.ids[0]
        else:
            p.error("home wallpapers needs --folder DIR")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
