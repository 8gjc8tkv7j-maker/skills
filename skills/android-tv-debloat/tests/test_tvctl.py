#!/usr/bin/env python3
"""End-to-end tests for tvctl.py against fake_adb.py (a pretend TCL Google TV).

Run:  python3 -m unittest discover -s tests -v      (from the skill folder)
"""
import copy
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
TVCTL = SKILL / "scripts" / "tvctl.py"
FAKE = HERE / "fake_adb.py"
APK = SKILL / "launcher" / "dist" / "TVHome.apk"
sys.path.insert(0, str(SKILL / "scripts"))
import tvctl  # noqa: E402


def pkg(enabled=True, system=True, launcher=False, home=False, **extra):
    return dict(enabled=enabled, system=system, launcher=launcher, home=home, **extra)


def tv_state():
    return {
        "serial": "192.168.1.42:5555",
        "connected": False,
        "authorized": True,
        "props": {
            "ro.serialno": "TCL55C735XYZ", "ro.product.manufacturer": "TCL", "ro.product.brand": "TCL",
            "ro.product.model": "55C735", "ro.product.device": "kiwi", "ro.build.version.release": "12",
            "ro.build.version.sdk": "31", "ro.build.fingerprint": "TCL/kiwi/kiwi:12/STT1/1:user/release-keys",
        },
        "packages": {
            "android": pkg(),
            "com.android.systemui": pkg(),
            "com.android.tv.settings": pkg(),
            "com.android.vending": pkg(launcher=True),
            "com.google.android.gms": pkg(),
            "com.google.android.apps.tv.launcherx": pkg(home=True),
            "com.google.android.tungsten.setupwraith": pkg(home=True),
            "com.google.android.youtube.tv": pkg(launcher=True),
            "com.netflix.ninja": pkg(launcher=True),
            "com.google.android.katniss": pkg(),
            "com.google.android.apps.mediashell": pkg(),
            "com.google.android.feedback": pkg(),
            "com.google.android.tvrecommendations": pkg(),
            "com.google.android.apps.tv.dreamx": pkg(),
            "com.google.android.play.games": pkg(enabled=False, launcher=True),
            "com.tcl.overseasappshow": pkg(),
            "com.tcl.bootadservice": pkg(),
            "com.tcl.messagebox": pkg(crash_on_disable="com.tcl.notify"),
            "com.tcl.guard": pkg(),
            "com.tcl.tv": pkg(),
            "com.tcl.tvinput": pkg(),
            "com.tcl.mystery": pkg(),
            "com.tcl.adtracker": pkg(),
            "com.mediatek.wwtv.tvcenter": pkg(launcher=True),
            "com.plexapp.android": pkg(system=False, launcher=True),
            "com.spotify.tv.android": pkg(system=False, launcher=True),
        },
        "settings": {
            "global": {"window_animation_scale": "1.0", "transition_animation_scale": "1.0",
                       "animator_duration_scale": "1.0", "wifi_scan_always_enabled": "1"},
            "secure": {"location_mode": "0"},
            "system": {"screen_brightness": "128"},
        },
        "role_home": "com.google.android.apps.tv.launcherx",
        "stock_intercepts_home": True,
        "foreground": "com.google.android.apps.tv.launcherx/.Home",
        "error_dialog": None,
        "files": {},
        "log": [],
    }


class TvctlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tvctl-test-"))
        self.state_file = self.tmp / "state.json"
        self.write_state(tv_state())
        self.backups = self.tmp / "tv-backups"
        self.env = dict(os.environ, FAKE_ADB_STATE=str(self.state_file))
        os.chmod(FAKE, 0o755)
        self.assertTrue(APK.is_file(), "build the launcher first (launcher/dist/TVHome.apk)")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helpers

    def write_state(self, st):
        self.state_file.write_text(json.dumps(st))

    def state(self):
        return json.loads(self.state_file.read_text())

    def tvctl(self, *args, answers=None, expect=0):
        extra = []
        if answers is not None:
            f = self.tmp / "answers.json"
            f.write_text(json.dumps(answers))
            extra = ["--answers", str(f)]
        p = subprocess.run([sys.executable, str(TVCTL), "--adb", str(FAKE), "--dir", str(self.backups), *extra, *args],
                           env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        if expect is not None:
            self.assertEqual(p.returncode, expect, p.stdout)
        return p.stdout

    def connect_and_backup(self):
        self.tvctl("connect", "192.168.1.42", "--wait", "3")
        self.tvctl("backup")
        return sorted(self.backups.glob("tcl-55c735-*"))[-1]

    def comparable(self, st):
        return ({p: v["enabled"] for p, v in st["packages"].items()}, st["settings"], st["role_home"])

    def make_all_changes(self):
        plan = json.loads(self.tvctl("debloat", "--plan", "--json"))
        batch = [i["pkg"] for i in plan["batch"] if i["pkg"] != "com.tcl.messagebox"]
        self.tvctl("debloat", "--apply", *batch, "--settle", "0")
        self.tvctl("debloat", "--apply", "com.google.android.katniss", "--settle", "0")
        self.tvctl("tweaks", "--apply", "animations", "adblock-dns")
        self.tvctl("home", "install", "--apk", str(APK))
        self.tvctl("home", "dock", "com.netflix.ninja", "com.google.android.youtube.tv", "settings")
        out = self.tvctl("home", "set-default", "--disable-stock", "com.google.android.apps.tv.launcherx")
        self.assertIn("TV Home is now the home screen", out)
        return batch

    # ---------------------------------------------------------------- tests

    def test_backup_writes_snapshot_and_restore_scripts(self):
        d = self.connect_and_backup()
        for name in ("device.json", "packages.json", "settings-global.txt", "home.json", "restore.sh",
                     "restore.cmd", "report.html", "README.txt"):
            self.assertTrue((d / name).is_file(), name)
        self.assertTrue(list((d / "screens").glob("01-before-any-change.png")))
        pk = json.loads((d / "packages.json").read_text())
        self.assertFalse(pk["com.google.android.play.games"]["enabled"])
        home = json.loads((d / "home.json").read_text())
        self.assertEqual(home["role_holders"], ["com.google.android.apps.tv.launcherx"])

    def test_plan_groups_and_never_offers_protected_packages(self):
        self.connect_and_backup()
        plan = json.loads(self.tvctl("debloat", "--plan", "--json"))
        names = {k: {i["pkg"] for i in v} for k, v in plan.items()}
        self.assertTrue({"com.tcl.overseasappshow", "com.tcl.bootadservice", "com.google.android.feedback"}
                        <= names["batch"])
        self.assertTrue({"com.google.android.katniss", "com.google.android.apps.mediashell", "com.netflix.ninja",
                         "com.google.android.youtube.tv", "com.tcl.guard", "com.google.android.tvrecommendations"}
                        <= names["ask"])
        self.assertEqual(names["review"], {"com.tcl.mystery", "com.tcl.adtracker"})
        self.assertIn("com.google.android.apps.tv.launcherx", names["launcher"])
        offered = set().union(*names.values())
        for protected in ("com.tcl.tv", "com.tcl.tvinput", "com.mediatek.wwtv.tvcenter", "com.android.vending",
                          "com.google.android.gms", "android"):
            self.assertNotIn(protected, offered)
        # Apps you installed yourself are never suggested.
        self.assertNotIn("com.spotify.tv.android", offered)
        self.assertNotIn("com.plexapp.android", offered)
        # Already-disabled packages aren't offered again.
        self.assertNotIn("com.google.android.play.games", offered)

    def test_apply_refuses_protected_and_launchers(self):
        self.connect_and_backup()
        out = self.tvctl("debloat", "--apply", "com.tcl.tv", "com.google.android.gms", "--settle", "0")
        self.assertIn("protected", out)
        st = self.state()
        self.assertTrue(st["packages"]["com.tcl.tv"]["enabled"])
        self.assertTrue(st["packages"]["com.google.android.gms"]["enabled"])
        self.tvctl("debloat", "--apply", "com.google.android.apps.tv.launcherx", expect=1)

    def test_full_makeover_then_restore_returns_tv_to_original(self):
        original = self.comparable(tv_state())
        d = self.connect_and_backup()
        batch = self.make_all_changes()
        st = self.state()
        for p in batch + ["com.google.android.katniss", "com.google.android.apps.tv.launcherx"]:
            self.assertFalse(st["packages"][p]["enabled"], p)
        self.assertEqual(st["settings"]["global"]["window_animation_scale"], "0.5")
        self.assertEqual(st["settings"]["global"]["private_dns_specifier"], "dns.adguard-dns.com")
        self.assertEqual(st["role_home"], "dev.tvhome")
        self.assertTrue(st["foreground"].startswith("dev.tvhome/"))
        self.assertEqual(st["files"]["/sdcard/Android/data/dev.tvhome/files/dock.txt"],
                         "com.netflix.ninja\ncom.google.android.youtube.tv\nsettings\n")
        shots = sorted(p.name for p in (d / "screens").glob("*.png"))
        self.assertGreaterEqual(len(shots), 6, shots)

        out = self.tvctl("restore")
        self.assertIn("everything tvctl changed has been put back", out)
        after = self.state()
        self.assertEqual(self.comparable(after), original)
        self.assertNotIn("dev.tvhome", after["packages"])
        self.assertTrue(after["foreground"].startswith("com.google.android.apps.tv.launcherx"))
        self.assert_never_uninstalled_anything_but_tv_home(after)

    def test_restore_sh_alone_undoes_everything(self):
        original = self.comparable(tv_state())
        d = self.connect_and_backup()
        self.make_all_changes()
        script = (d / "restore.sh").read_text()
        self.assertNotIn("pm uninstall", script)
        env = dict(self.env, ADB=str(FAKE))
        p = subprocess.run(["sh", str(d / "restore.sh")], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("Done", p.stdout)
        after = self.state()
        self.assertEqual(self.comparable(after), original)
        self.assertNotIn("dev.tvhome", after["packages"])
        self.assert_never_uninstalled_anything_but_tv_home(after)

    def test_restore_sh_accepts_new_ip_and_keeps_tv_home(self):
        d = self.connect_and_backup()
        self.make_all_changes()
        st = self.state()
        st["serial"] = "192.168.1.77:5555"
        st["connected"] = False
        self.write_state(st)
        env = dict(self.env, ADB=str(FAKE))
        p = subprocess.run(["sh", str(d / "restore.sh"), "192.168.1.77", "--keep-tv-home"], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout)
        after = self.state()
        self.assertIn("dev.tvhome", after["packages"])
        self.assertEqual(after["role_home"], "com.google.android.apps.tv.launcherx")
        self.assertTrue(after["packages"]["com.google.android.apps.tv.launcherx"]["enabled"])

    def second_session(self):
        self.connect_and_backup()
        self.make_all_changes()
        self.tvctl("backup")  # a later session starts from the already-changed TV
        newest = sorted(self.backups.glob("tcl-55c735-*"))[-1]
        self.tvctl("debloat", "--apply", "com.tcl.guard", "--settle", "0")
        self.tvctl("tweaks", "--apply", "scanning")
        return newest

    def test_later_backup_still_restores_to_the_original(self):
        original = self.comparable(tv_state())
        self.second_session()
        self.tvctl("restore")
        after = self.state()
        self.assertEqual(self.comparable(after), original)
        self.assertNotIn("dev.tvhome", after["packages"])

    def test_later_restore_sh_chains_to_the_earlier_one(self):
        original = self.comparable(tv_state())
        newest = self.second_session()
        env = dict(self.env, ADB=str(FAKE))
        p = subprocess.run(["sh", str(newest / "restore.sh")], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("earlier session", p.stdout)
        self.assertEqual(self.comparable(self.state()), original)
        self.assertIn('call "%~dp0..\\', (newest / "restore.cmd").read_text())

    def test_restore_cmd_is_generated_for_windows(self):
        d = self.connect_and_backup()
        self.make_all_changes()
        raw = (d / "restore.cmd").read_bytes()
        self.assertIn(b"\r\n", raw)
        text = raw.decode()
        self.assertIn("shell pm enable --user 0 com.tcl.overseasappshow", text)
        self.assertIn("settings delete global private_dns_specifier", text)
        self.assertIn("settings put global window_animation_scale 1.0", text)
        self.assertIn("cmd role add-role-holder --user 0 android.app.role.HOME com.google.android.apps.tv.launcherx",
                      text)
        self.assertIn("uninstall dev.tvhome", text)

    def test_package_that_breaks_something_is_turned_back_on(self):
        self.connect_and_backup()
        out = self.tvctl("debloat", "--apply", "com.tcl.messagebox", "--settle", "0", expect=4)
        self.assertIn("turning it back on", out)
        st = self.state()
        self.assertTrue(st["packages"]["com.tcl.messagebox"]["enabled"])
        self.assertIsNone(st["error_dialog"])

    def test_interactive_wizard_follows_answers(self):
        self.connect_and_backup()
        plan = json.loads(self.tvctl("debloat", "--plan", "--json"))
        keep_first = plan["batch"][0]["pkg"]
        answers = {"debloat.batch": True, "debloat.batch.keep": "1", "debloat.ask.*": False,
                   "debloat.ask.com.google.android.katniss": True, "debloat.review": "2"}
        self.tvctl("debloat", "--settle", "0", answers=answers)
        st = self.state()
        self.assertTrue(st["packages"][keep_first]["enabled"], "kept because the answer said so")
        self.assertFalse(st["packages"]["com.google.android.katniss"]["enabled"])
        self.assertTrue(st["packages"]["com.netflix.ninja"]["enabled"], "apps are kept unless you say so")
        self.assertTrue(st["packages"]["com.google.android.youtube.tv"]["enabled"])
        review2 = plan["review"][1]["pkg"]
        self.assertFalse(st["packages"][review2]["enabled"])

    def test_wizard_refuses_to_guess_without_a_terminal(self):
        self.connect_and_backup()
        out = self.tvctl("debloat", expect=1)
        self.assertIn("--plan", out)

    def test_google_tv_setup_taking_over_home_is_handled(self):
        st = tv_state()
        st["wraith_takes_over"] = True
        self.write_state(st)
        self.connect_and_backup()
        self.tvctl("home", "install", "--apk", str(APK))
        out = self.tvctl("home", "set-default", "--disable-stock", "com.google.android.apps.tv.launcherx",
                         "com.google.android.tungsten.setupwraith")
        self.assertIn("TV Home is now the home screen", out)
        st = self.state()
        self.assertFalse(st["packages"]["com.google.android.tungsten.setupwraith"]["enabled"])
        self.tvctl("restore")
        st = self.state()
        self.assertTrue(st["packages"]["com.google.android.tungsten.setupwraith"]["enabled"])
        self.assertTrue(st["packages"]["com.google.android.apps.tv.launcherx"]["enabled"])

    def test_changes_require_a_backup_first(self):
        self.tvctl("connect", "192.168.1.42", "--wait", "3")
        out = self.tvctl("debloat", "--apply", "com.tcl.bootadservice", "--settle", "0")
        self.assertIn("backing up first", out)
        d = sorted(self.backups.glob("tcl-55c735-*"))[-1]
        pk = json.loads((d / "packages.json").read_text())
        self.assertTrue(pk["com.tcl.bootadservice"]["enabled"], "snapshot was taken before the change")

    def test_journal_is_written_before_the_change(self):
        st = tv_state()
        st["packages"]["com.tcl.bootadservice"]["undisableable"] = True
        self.write_state(st)
        d = self.connect_and_backup()
        self.tvctl("debloat", "--apply", "com.tcl.bootadservice", "--settle", "0")
        entries = [json.loads(l) for l in (d / "changes.jsonl").read_text().splitlines()]
        self.assertEqual(entries[0]["pkg"], "com.tcl.bootadservice")
        self.assertIn("pm enable --user 0 com.tcl.bootadservice", (d / "restore.sh").read_text())

    def test_source_never_uninstalls_other_apps(self):
        src = TVCTL.read_text()
        for m in re.finditer(r"uninstall", src):
            line = src[src.rfind("\n", 0, m.start()) + 1:src.find("\n", m.start())]
            self.assertTrue("HOME_PKG" in line or "dev.tvhome" in line or "uninstalled" in line
                            or "never uninstall" in line.lower() or "only TV Home" in line
                            or line.strip().startswith(("#", '"', "'")) or "adb uninstall" in line, line)
        self.assertNotIn("pm uninstall", src)
        self.assertNotIn("pm clear", src)

    def assert_never_uninstalled_anything_but_tv_home(self, st):
        for argv in st["log"]:
            joined = " ".join(argv)
            if "uninstall" in joined:
                self.assertTrue(joined.endswith("uninstall dev.tvhome"), joined)

    # ---------------------------------------------------------------- discovery

    def test_mdns_response_parsing(self):
        def name(n):
            return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".")) + b"\x00"
        inst = "Living Room TV._googlecast._tcp.local"
        txt = b"".join(bytes([len(s)]) + s for s in (b"fn=Living Room TV", b"md=55C735"))
        rr = lambda n, t, data: name(n) + struct.pack("!HHIH", t, 1, 120, len(data)) + data
        pkt = struct.pack("!HHHHHH", 0, 0x8400, 0, 3, 0, 1)
        pkt += rr("_googlecast._tcp.local", 12, name(inst))
        pkt += rr(inst, 33, struct.pack("!HHH", 0, 0, 8009) + name("tv.local"))
        pkt += rr(inst, 16, txt)
        pkt += rr("tv.local", 1, socket.inet_aton("192.168.1.42"))
        recs = tvctl.parse_mdns(pkt)
        kinds = {r[0]: r for r in recs}
        self.assertEqual(kinds["PTR"][2], inst)
        self.assertEqual(kinds["SRV"][2][0], 8009)
        self.assertEqual(kinds["TXT"][2]["fn"], "Living Room TV")
        self.assertEqual(kinds["A"][2], "192.168.1.42")

    def test_mdns_query_packet_is_well_formed(self):
        pkt = tvctl.mdns_query_packet(["_googlecast._tcp.local"])
        self.assertEqual(struct.unpack("!H", pkt[4:6])[0], 1)
        self.assertTrue(pkt.endswith(b"\x00\x00\x0c\x80\x01"))


if __name__ == "__main__":
    unittest.main()
