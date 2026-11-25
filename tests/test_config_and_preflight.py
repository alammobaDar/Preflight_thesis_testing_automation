import copy, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import config_loader as cl, preflight as pf


def filled_cfg():
    """A config with every required value set to a PLACEHOLDER (test-only, not real values)."""
    cfg = cl.load_config()
    e = cfg.experiment
    def setp(path, v):
        d = e; parts = path.split(".")
        for p in parts[:-1]: d = d[p]
        d[parts[-1]] = v
    for p in cl.COMMON_REAL + cl.WG_REAL:
        setp(p, "x")
    setp("client.os_id", "endeavouros")
    setp("client.package_manager", "pacman")
    setp("gateway.lan_iface", "wlan0"); setp("gateway.wan_iface", "enp0s1"); setp("gateway.wg_iface", "wg0")
    setp("server.public_address", "203.0.113.10"); setp("server.wireguard_address", "10.9.0.1")
    setp("wireguard.server_side.interface", "wg0")
    setp("gateway.deploy_dir", "/nonexistent/ppqos")
    setp("workloads.rtt_probe.confirmed_by_user", True); setp("qos.scenario_a_rate_decision", "approved_20")
    for k in cfg.validation["thresholds"]: cfg.validation["thresholds"][k] = 1
    return cfg


class ConfigTests(unittest.TestCase):
    def test_shipped_config_structurally_valid(self):
        self.assertEqual(cl.load_config().problems, [])

    def test_topology(self):
        c = cl.load_config()
        # Gateway is confirmed as your laptop (EndeavourOS)
        self.assertEqual(c.get("gateway.os_id"), "endeavouros")
        self.assertEqual(c.get("gateway.package_manager"), "pacman")
        # Client OS is NOT hardcoded — it must be filled in (groupmate's laptop, OS unknown)
        self.assertIsNone(c.get("client.os_id"))
        self.assertIsNone(c.get("client.package_manager"))
        # client.os_id must appear in missing_values so real runs are refused without it
        self.assertIn("client.os_id", cl.missing_values(c, "ABCD", "real"))

    def test_handoff_values(self):
        c = cl.load_config()
        self.assertEqual(c.get("qos.htb_root_fraction"), 0.90)
        self.assertAlmostEqual(cl.htb_root_mbit(c), 18.0)
        cls = c.get("qos.classes")
        self.assertEqual([cls[k]["rate_mbit"] for k in ("realtime", "streaming", "bulk")], [3, 8, 7])
        self.assertEqual([cls[k]["ceil_mbit"] for k in ("realtime", "streaming", "bulk")], [4, 18, 18])
        self.assertEqual([cls[k]["prio"] for k in ("realtime", "streaming", "bulk")], [1, 2, 3])
        self.assertEqual((c.scenario("B")["n_packets"], c.scenario("C")["n_packets"], c.scenario("D")["n_packets"]), (7, 8, 8))
        self.assertTrue(c.scenario("C")["wireguard"] and not c.scenario("D")["wireguard"])
        self.assertIsNone(c.scenario("A")["model_key"]); self.assertFalse(c.scenario("A")["classification_metrics"])
        self.assertEqual(c.get("experiment.valid_trials_per_scenario"), 15)

    def test_unknowns_are_null_not_invented(self):
        c = cl.load_config()
        for p in ("gateway.wan_iface", "server.public_address", "server.wireguard_address", "server.os_id",
                  "wireguard.endpoint", "wireguard.allowed_ips", "workloads.streaming.playlist_path"):
            self.assertIsNone(c.get(p), p)

    def test_scenario_a_decision_resolved(self):
        # Decision 1 (option 2): A WAN TBF = 18 Mbit/s, approved by user 2026-11-21
        c = cl.load_config()
        self.assertEqual(c.get("qos.scenario_a_effective_rate_mbit"), 20)
        self.assertEqual(c.get("qos.scenario_a_rate_decision"), "option3_approved")
        # Must no longer appear in missing_values (decision is resolved, real runs not blocked by this)
        a_items = [m for m in cl.missing_values(c, "ABCD") if "scenario_a" in m]
        self.assertEqual(a_items, [], f"scenario_a still blocking: {a_items}")
        # HTB root is still 18 (bottleneck * fraction = 20 * 0.9) -- unchanged
        self.assertAlmostEqual(cl.htb_root_mbit(c), 18.0)

    def test_mutations_are_caught(self):
        def broken(mut):
            c = cl.load_config(); mut(c); return cl.validate_structure(c)
        self.assertTrue(broken(lambda c: c.experiment["qos"]["classes"]["bulk"].update(rate_mbit=9)))      # sum != root
        self.assertTrue(broken(lambda c: c.experiment["qos"]["classes"]["realtime"].update(ceil_mbit=1)))  # ceil<rate
        self.assertTrue(broken(lambda c: c.scenarios["scenarios"]["B"].update(model_key="post_n")))        # B==C model
        self.assertTrue(broken(lambda c: c.scenarios["scenarios"]["D"].update(model_key="first_n")))       # C!=D model
        self.assertTrue(broken(lambda c: c.scenarios["scenarios"]["A"].update(model_key="first_n")))       # A has no model
        self.assertTrue(broken(lambda c: c.scenarios["scenarios"]["D"].update(wireguard=True)))            # wg/target mismatch
        self.assertTrue(broken(lambda c: c.scenarios["scenarios"]["A"].update(classification_metrics=True)))
        self.assertTrue(broken(lambda c: c.scenarios["scenarios"]["C"]["qdisc"]["wan_egress"].update(type="bogus")))

    def test_real_refused_when_incomplete_and_ok_when_filled(self):
        with self.assertRaises(cl.ConfigError): cl.refuse_unless_ready(cl.load_config(), "ABCD", "real")
        cl.refuse_unless_ready(filled_cfg(), "ABCD", "real")                 # no exception
        cl.refuse_unless_ready(cl.load_config(), "ABCD", "dry-run")          # dry-run never needs devices

    def test_scenario_d_does_not_require_wireguard_values(self):
        c = filled_cfg()
        for p in cl.WG_REAL:
            d = c.experiment; parts = p.split(".")
            for q in parts[:-1]: d = d[q]
            if parts[-1] != "wireguard_address": d[parts[-1]] = None
        c.experiment["server"]["wireguard_address"] = None
        self.assertEqual(cl.missing_values(c, "D", "real"), [])
        self.assertTrue(cl.missing_values(c, "C", "real"))

    def test_ip_validation(self):
        c = cl.load_config(); c.experiment["gateway"]["lan_address"] = "10.43.0.1"
        self.assertTrue(cl.validate_ip_fields(c))
        c = cl.load_config(); c.experiment["gateway"]["wan_iface"] = "eth0"
        self.assertTrue(any("eth0" in e for e in cl.validate_ip_fields(c)))

    def test_json_roundtrip(self):
        c = cl.load_config(); c2 = cl.config_from_json(cl.config_to_json(c))
        self.assertEqual((c2.experiment, c2.scenarios, c2.validation), (c.experiment, c.scenarios, c.validation))


class ParserTests(unittest.TestCase):
    def test_chrony(self):
        self.assertAlmostEqual(pf.parse_chrony_offset_ms("System time     : 0.000023456 seconds fast of NTP time"), 0.023456)
        self.assertAlmostEqual(pf.parse_chrony_offset_ms("System time     : 0.051000000 seconds slow of NTP time"), -51.0)
        self.assertIsNone(pf.parse_chrony_offset_ms("garbage"))

    def test_hls(self):
        pl = "#EXTM3U\n" + "".join(f"#EXTINF:10.0,\nseg{i}.ts\n" for i in range(11)) + "#EXT-X-ENDLIST\n"
        r = pf.parse_hls_playlist(pl); self.assertEqual((r["segments"], r["duration_s"], r["is_vod"], r["is_master"]), (11, 110.0, True, False))
        self.assertTrue(pf.parse_hls_playlist("#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8")["is_master"])

    def test_tc(self):
        t = "qdisc htb 1: root refcnt 2 r2q 10 default 0x30 direct_packets_stat 0\nqdisc sfq 10: parent 1:10 limit 127p\n"
        self.assertEqual(pf.parse_tc_roots(t), [("htb", "1:")]); self.assertTrue(pf.experiment_qdisc_present(t))
        self.assertFalse(pf.experiment_qdisc_present("qdisc fq_codel 0: root refcnt 2 limit 10240p\n"))

    def test_perms(self):
        self.assertTrue(pf.perms_too_open(0o644)); self.assertFalse(pf.perms_too_open(0o600))


class FakeRun:
    def __init__(self, table): self.table = table; self.calls = []
    def __call__(self, cmd, timeout=15):
        self.calls.append(cmd); key = " ".join(cmd)
        for k, v in self.table.items():
            if key.startswith(k): return v
        return 1, "", "no fake"


class GatewayPreflightTests(unittest.TestCase):
    def results(self, scenario, table):
        return {r.check: r for r in pf.run_preflight(filled_cfg(), "gateway", [scenario], "real", FakeRun(table))}

    def base(self, wg_up, route_dev, tc="qdisc fq_codel 0: root refcnt 2\n"):
        t = {"ip link show wlan0": (0, "", ""), "ip link show enp0s1": (0, "", ""),
             "ip link show wg0": (0 if wg_up else 1, "", ""),
             "ip -j addr show dev wlan0": (0, json.dumps([{"addr_info": [{"family": "inet", "local": "10.42.0.1"}]}]), ""),
             "sysctl -n net.ipv4.ip_forward": (0, "1\n", ""),
             "wg show wg0 latest-handshakes": (0, "KEY\t1700000000\n", ""),
             "ip -j route get": (0, json.dumps([{"dev": route_dev}]), ""),
             "ping": (0, "", ""), "tc qdisc show": (0, tc, ""), "pgrep": (1, "", ""), "chronyc tracking": (0, "System time : 0.000010 seconds fast of NTP time", "")}
        return t

    def test_scenario_c_good(self):
        r = self.results("C", self.base(True, "wg0"))
        for k in ("wireguard.state", "route.to_target", "forwarding.ipv4", "iface.wan", "hotspot.lan_address", "stale.qdisc.wg0", "clock.offset_ms"):
            self.assertEqual(r[k].status, pf.PASS, (k, r[k]))

    def test_scenario_d_fails_if_wireguard_up(self):
        r = self.results("D", self.base(True, "enp0s1")); self.assertEqual(r["wireguard.state"].status, pf.FAIL)

    def test_scenario_d_good(self):
        r = self.results("D", self.base(False, "enp0s1"))
        self.assertEqual((r["wireguard.state"].status, r["route.to_target"].status), (pf.PASS, pf.PASS))

    def test_route_mismatch_detected(self):
        self.assertEqual(self.results("C", self.base(True, "enp0s1"))["route.to_target"].status, pf.FAIL)
        self.assertEqual(self.results("D", self.base(False, "wg0"))["route.to_target"].status, pf.FAIL)

    def test_stale_qdisc_detected(self):
        r = self.results("C", self.base(True, "wg0", tc="qdisc htb 1: root refcnt 2 r2q 10 default 0x30\n"))
        self.assertEqual(r["stale.qdisc.wlan0"].status, pf.FAIL)

    def test_clock_offset_over_limit_fails(self):
        t = self.base(True, "wg0"); t["chronyc tracking"] = (0, "System time : 0.080000 seconds slow of NTP time", "")
        self.assertEqual(self.results("C", t)["clock.offset_ms"].status, pf.FAIL)

    def test_scenario_a_needs_no_model(self):
        self.assertEqual(self.results("A", self.base(True, "wg0"))["deploy"].status, pf.SKIP)

    def test_missing_wan_iface_never_defaulted(self):
        c = filled_cfg(); c.experiment["gateway"]["wan_iface"] = None
        res = {r.check: r for r in pf.run_preflight(c, "gateway", ["C"], "real", FakeRun(self.base(True, "wg0")))}
        self.assertEqual(res["config.required_values"].status, pf.FAIL)
        self.assertNotIn("iface.wan", res)        # hardware checks not attempted when config incomplete

    def test_nothing_mutating_is_ever_run(self):
        fr = FakeRun(self.base(True, "wg0")); pf.run_preflight(filled_cfg(), "gateway", ["C"], "real", fr)
        for cmd in fr.calls:
            self.assertNotIn(cmd[0], ("rm", "iptables", "wg-quick"))
            if cmd[0] == "tc": self.assertEqual(cmd[1:3], ["qdisc", "show"])


class ManifestAndScriptTests(unittest.TestCase):
    def test_manifest_hashes_match_handoff(self):
        m = json.loads((ROOT / "manifests/models.manifest.json").read_text())["models"]
        self.assertTrue(m["first_n"]["sha256"].startswith("010d1a37")); self.assertTrue(m["post_n"]["sha256"].startswith("bf4e3118"))
        self.assertEqual((len(m["first_n"]["sha256"]), len(m["post_n"]["sha256"])), (64, 64))
        self.assertEqual(m["first_n"]["training_provenance"]["training_dataset_name"], "TO_BE_COMPLETED_BY_USER")

    def test_sources_manifest_records_dirty_post_n_engine(self):
        s = json.loads((ROOT / "manifests/sources.manifest.json").read_text())["repos"]
        self.assertTrue(s["capture_engine_first_n"]["working_tree_clean"]); self.assertFalse(s["capture_engine_post_n"]["working_tree_clean"])

    def test_client_os_not_hardcoded(self):
        # setup_client.sh must handle pacman, apt and dnf — not just pacman
        script = (ROOT / "scripts/setup_client.sh").read_text()
        self.assertIn("apt", script)
        self.assertIn("dnf", script)
        self.assertIn("pacman", script)
        self.assertNotIn("exit 3", script.split("pacman|apt|dnf")[0])  # no early exit before distro check

    def test_scripts_syntax_and_flags(self):
        for f in sorted((ROOT / "scripts").glob("*.sh")) + [ROOT / "scripts/lib/common.sh"]:
            self.assertEqual(subprocess.run(["bash", "-n", str(f)]).returncode, 0, f)
        r = subprocess.run([str(ROOT / "scripts/setup_server.sh"), "--yes"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)                                    # --yes without --apply refused
        r = subprocess.run([str(ROOT / "scripts/setup_server.sh"), "--apply"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertIn("refusing", r.stderr + r.stdout)                       # --apply without tty/--yes changes nothing

    def test_default_mode_is_check(self):
        self.assertIn('MODE="check"', (ROOT / "scripts/lib/common.sh").read_text())

    def test_no_secrets_in_templates(self):
        import re
        for f in list((ROOT / "config").glob("*")) + list((ROOT / "server").glob("*")) + list((ROOT / "gateway").glob("*")):
            t = f.read_text()
            self.assertFalse(re.search(r"PrivateKey\s*=\s*[A-Za-z0-9+/]{43}=", t), f)
            self.assertNotIn("GatewayPassword", t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
