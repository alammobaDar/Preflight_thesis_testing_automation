"""Preflight validators for client / gateway / server (stdlib only).

Run:  python3 -m src.preflight --role client --scenarios A B C D --purpose real
      python3 -m src.preflight --role gateway --config-json cfg.json --scenario B     (on the gateway)
Exit code 0 only if there are no FAIL results. Real runs are REFUSED while required config is missing.

IMPORTANT: Only the config/manifest/parsing logic is unit-tested locally. Every check that touches
interfaces, routes, tc, WireGuard, SSH, nginx, HLS or chrony is UNTESTED on real hardware.
"""
from __future__ import annotations

import argparse, hashlib, json, os, re, shutil, stat, subprocess, sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Optional

from .config_loader import (Config, config_from_json, load_config, missing_values,
                            validate_ip_fields, ConfigError)

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


@dataclass
class Result:
    check: str
    status: str
    detail: str = ""
    needs_hardware: bool = False


Runner = Callable[[list], "tuple[int, str, str]"]


def default_runner(cmd: list, timeout: int = 15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# ------------------------------------------------------------------ pure parsers (unit-tested)

def parse_chrony_offset_ms(tracking_text: str) -> Optional[float]:
    m = re.search(r"System time\s*:\s*([0-9.]+) seconds (fast|slow)", tracking_text)
    if not m:
        return None
    v = float(m.group(1)) * 1000.0
    return v if m.group(2) == "fast" else -v


def parse_hls_playlist(text: str) -> dict:
    durs = [float(x) for x in re.findall(r"#EXTINF:([0-9.]+)", text)]
    return {"is_master": "#EXT-X-STREAM-INF" in text, "segments": len(durs),
            "duration_s": sum(durs), "is_vod": "#EXT-X-ENDLIST" in text,
            "max_segment_s": max(durs) if durs else 0.0}


def parse_tc_roots(tc_show_text: str) -> list:
    """Return [(kind, handle)] for ROOT qdiscs in `tc qdisc show dev X` output."""
    return [(m.group(1), m.group(2)) for m in re.finditer(r"^qdisc (\w+) ([0-9a-f]+:) .*\broot\b", tc_show_text, re.M)]


def experiment_qdisc_present(tc_show_text: str) -> bool:
    return any(k in ("htb", "tbf") for k, _ in parse_tc_roots(tc_show_text))


def perms_too_open(mode: int) -> bool:
    return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


# ------------------------------------------------------------------ check library

class Checker:
    def __init__(self, cfg: Config, run: Runner = default_runner):
        self.cfg, self.run, self.results = cfg, run, []

    def add(self, check, status, detail="", hw=False):
        self.results.append(Result(check, status, detail, hw))

    # ---- common
    def config_checks(self, scenarios, purpose):
        self.add("config.structure", FAIL if self.cfg.problems else PASS, "; ".join(self.cfg.problems))
        ip_errs = validate_ip_fields(self.cfg)
        self.add("config.ip_fields", FAIL if ip_errs else PASS, "; ".join(ip_errs))
        if purpose == "real":
            miss = missing_values(self.cfg, scenarios, "real")
            self.add("config.required_values", FAIL if miss else PASS,
                     "REFUSING real run, unresolved: " + ", ".join(miss) if miss else "all required values set")
        else:
            self.add("config.required_values", SKIP, "dry-run: placeholders allowed; no device is touched")

    def cmds(self, names):
        for n in names:
            self.add(f"command.{n}", PASS if shutil.which(n) else FAIL, shutil.which(n) or "missing")

    def clock(self):
        mx = self.cfg.get("clock_sync.max_offset_ms")
        rc, out, _ = self.run(["chronyc", "tracking"])
        off = parse_chrony_offset_ms(out) if rc == 0 else None
        if off is None:
            rc2, o2, _ = self.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
            if rc2 == 0 and o2.strip() == "yes":
                self.add("clock.sync", WARN, "NTP synchronized but offset unmeasurable (chronyc unavailable)", True)
            else:
                self.add("clock.sync", FAIL, "no chrony offset and NTP not confirmed synchronized", True)
            return
        ok = abs(off) <= mx
        self.add("clock.offset_ms", PASS if ok else FAIL, f"{off:+.3f} ms vs max {mx} ms (offset to its own NTP source)", True)

    def pidfile_free_disk(self, path: Path, need_gb=2.0):
        try:
            free = shutil.disk_usage(path).free / 1e9
            self.add(f"disk.{path}", PASS if free >= need_gb else FAIL, f"{free:.1f} GB free (need >= {need_gb})")
        except OSError as e:
            self.add(f"disk.{path}", FAIL, str(e))

    # ---- client
    # Supported client package managers (must match setup_client.sh)
    _CLIENT_PKG_MGRS = {
        "arch": "pacman", "endeavouros": "pacman", "manjaro": "pacman",
        "ubuntu": "apt", "debian": "apt", "linuxmint": "apt", "pop": "apt",
        "fedora": "dnf", "rhel": "dnf", "centos": "dnf", "amzn": "dnf",
    }

    def client(self, scenarios, purpose):
        osr = Path("/etc/os-release").read_text() if Path("/etc/os-release").exists() else ""
        actual_id = (re.search(r"^ID=(.+)", osr, re.M) or ["", "unknown"])[0]
        actual_id = re.sub(r'^ID=|"', "", actual_id).strip()
        actual_like = re.search(r'^ID_LIKE="?([^"\n]+)"?', osr, re.M)
        actual_like = actual_like.group(1) if actual_like else ""
        # Determine supported package manager from actual OS
        pkg_mgr = None
        for key in [actual_id] + actual_like.split():
            if key in self._CLIENT_PKG_MGRS:
                pkg_mgr = self._CLIENT_PKG_MGRS[key]; break
        if pkg_mgr:
            self.add("client.os", PASS, f"id={actual_id} → package manager: {pkg_mgr}")
        else:
            self.add("client.os", FAIL, f"id={actual_id} not in supported list (pacman/apt/dnf). "
                     "Update setup_client.sh if your distro uses one of these managers.")
        # Cross-check against config if os_id is set
        cfg_os = self.cfg.get("client.os_id")
        if cfg_os and cfg_os != actual_id:
            self.add("client.os.config_match", WARN,
                     f"config says client.os_id={cfg_os!r} but this machine is {actual_id!r}. "
                     "Update config/experiment.yaml if running on a different machine than expected.")
        self.cmds(["iperf3", "cvlc", "ssh", "rsync", "chronyc", "ping", "python3"])
        for m in ("yaml", "numpy", "pandas", "scipy", "matplotlib"):
            rc, _, _ = self.run([sys.executable, "-c", f"import {m}"])
            self.add(f"python.{m}", PASS if rc == 0 else FAIL)
        self.clock()
        self.pidfile_free_disk(self.cfg.root / self.cfg.get("experiment.results_dir", "results"))
        if purpose != "real":
            return
        for who in ("gateway", "server"):
            k = self.cfg.get(f"{who}.ssh_key_path") or self.cfg.get("client.ssh_key_path")
            if k:
                p = Path(k).expanduser()
                if not p.exists():
                    self.add(f"ssh.{who}.key", FAIL, f"{p} not found")
                elif perms_too_open(p.stat().st_mode):
                    self.add(f"ssh.{who}.key", FAIL, f"{p} permissions too open (chmod 600)")
                else:
                    self.add(f"ssh.{who}.key", PASS, str(p))
            host = self.cfg.get(f"{who}.host" if who == "gateway" else "server.ssh_host")
            user = self.cfg.get(f"{who}.ssh_user")
            port = str(self.cfg.get(f"{who}.ssh_port", 22))
            if host and user and k:
                rc, _, err = self.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-p", port,
                                       "-i", str(Path(k).expanduser()), f"{user}@{host}", "true"])
                self.add(f"ssh.{who}.connect", PASS if rc == 0 else FAIL, err.strip()[:200], True)
        self.hotspot_membership()
        self.hls_over_https(scenarios)

    def hotspot_membership(self):
        import ipaddress
        iface, sn = self.cfg.get("client.workload_bind_iface"), self.cfg.get("gateway.lan_subnet")
        if not iface:
            return self.add("client.hotspot", FAIL, "client.workload_bind_iface unset")
        rc, out, _ = self.run(["ip", "-j", "addr", "show", "dev", iface])
        if rc != 0:
            return self.add("client.hotspot", FAIL, f"interface {iface} not found", True)
        addrs = [a["local"] for i in json.loads(out) for a in i.get("addr_info", []) if a.get("family") == "inet"]
        inside = [a for a in addrs if ipaddress.ip_address(a) in ipaddress.ip_network(sn)]
        self.add("client.hotspot.address", PASS if inside else FAIL, f"{iface}: {addrs} (subnet {sn})", True)
        rc, out, _ = self.run(["ip", "-j", "route", "get", self.cfg.get("server.public_address") or "1.1.1.1"])
        if rc == 0:
            r = json.loads(out)[0]
            gw_ok = r.get("gateway") == self.cfg.get("gateway.lan_address") and r.get("dev") == iface
            self.add("client.route_via_gateway", PASS if gw_ok else FAIL,
                     f"route: dev={r.get('dev')} via={r.get('gateway')} (expected {iface} via {self.cfg.get('gateway.lan_address')})", True)

    def hls_over_https(self, scenarios):
        wl = self.cfg.get("workloads.streaming")
        name, path, tls = wl["tls"]["server_name"], wl["playlist_path"], wl["tls"]
        if not (name and path):
            return self.add("hls.playlist", FAIL, "streaming.tls.server_name / playlist_path unset")
        for s in scenarios:
            scn = self.cfg.scenario(s)
            tgt = self.cfg.get("server.wireguard_address" if scn["target"] == "wireguard_address" else "server.public_address")
            if not tgt:
                self.add(f"hls.playlist.{s}", FAIL, "scenario target address unset"); continue
            cmd = ["curl", "-sS", "--max-time", "10", "--resolve", f"{name}:{wl['port']}:{tgt}"]
            if tls.get("verify_mode") == "insecure":
                cmd.append("-k")
            rc, out, err = self.run(cmd + [f"https://{name}:{wl['port']}{path}"])
            if rc != 0:
                self.add(f"hls.playlist.{s}", FAIL, f"fetch failed: {err.strip()[:150]}", True); continue
            pl = parse_hls_playlist(out)
            problems = []
            if pl["is_master"]: problems.append("playlist is a master/adaptive playlist; need single variant")
            if not pl["is_vod"]: problems.append("not a VOD playlist (no ENDLIST)")
            if pl["duration_s"] < wl["min_media_duration_s"]: problems.append(f"duration {pl['duration_s']:.0f}s < {wl['min_media_duration_s']}s")
            self.add(f"hls.playlist.{s}", FAIL if problems else PASS,
                     "; ".join(problems) or f"{pl['segments']} segments, {pl['duration_s']:.0f}s (fetch only; playback NOT verified)", True)

    # ---- gateway
    def gateway(self, scenario, purpose):
        # Gateway = your laptop (EndeavourOS, confirmed). This runs ON the gateway itself.
        scn = self.cfg.scenario(scenario)
        osr = Path("/etc/os-release").read_text() if Path("/etc/os-release").exists() else ""
        actual_id = re.sub(r'^ID=|"', "", (re.search(r"^ID=(.+)", osr, re.M) or ["", "unknown"])[0]).strip()
        cfg_os = self.cfg.get("gateway.os_id") or "endeavouros"
        self.add("gateway.os", PASS if actual_id == cfg_os else FAIL,
                 f"id={actual_id} (config expects {cfg_os})")
        self.cmds(["tc", "ip", "wg", "conntrack", "pidstat", "chronyc"])
        self.clock()
        if purpose != "real":
            return
        lan, wan, wg = (self.cfg.get("gateway.lan_iface"), self.cfg.get("gateway.wan_iface"), self.cfg.get("gateway.wg_iface"))
        for role, ifn in (("lan", lan), ("wan", wan)):
            if not ifn:
                self.add(f"iface.{role}", FAIL, f"gateway.{role}_iface unset (never defaulted)"); continue
            rc, _, _ = self.run(["ip", "link", "show", ifn])
            self.add(f"iface.{role}", PASS if rc == 0 else FAIL, ifn, True)
        if lan:
            rc, out, _ = self.run(["ip", "-j", "addr", "show", "dev", lan])
            have = [a["local"] for i in (json.loads(out) if rc == 0 else []) for a in i.get("addr_info", []) if a.get("family") == "inet"]
            self.add("hotspot.lan_address", PASS if self.cfg.get("gateway.lan_address") in have else FAIL, f"{lan}: {have}", True)
        rc, out, _ = self.run(["sysctl", "-n", "net.ipv4.ip_forward"])
        self.add("forwarding.ipv4", PASS if out.strip() == "1" else FAIL, out.strip(), True)
        # WireGuard state must match the scenario
        rc, out, _ = self.run(["ip", "link", "show", wg])
        wg_up = rc == 0
        if scn["wireguard"]:
            self.add("wireguard.state", PASS if wg_up else FAIL, f"{wg} {'up' if wg_up else 'missing'} (scenario {scenario} needs it)", True)
            if wg_up:
                rc, hs, _ = self.run(["wg", "show", wg, "latest-handshakes"])
                self.add("wireguard.handshake", PASS if hs.split() and hs.split()[-1] != "0" else FAIL, hs.strip()[:100] or "none", True)
        else:
            self.add("wireguard.state", FAIL if wg_up else PASS, f"{wg} must be DOWN for scenario {scenario}", True)
        # path to scenario target
        key = "server.wireguard_address" if scn["target"] == "wireguard_address" else "server.public_address"
        tgt = self.cfg.get(key)
        if tgt:
            rc, out, _ = self.run(["ip", "-j", "route", "get", tgt])
            if rc == 0:
                dev = json.loads(out)[0].get("dev")
                want = wg if scn["wireguard"] else wan
                self.add("route.to_target", PASS if dev == want else FAIL, f"{tgt} leaves via {dev}, expected {want}", True)
            rc, _, _ = self.run(["ping", "-c", "3", "-W", "2", tgt])
            self.add("target.reachable", PASS if rc == 0 else WARN, f"ping {tgt}", True)
        # stale state: report only, never delete here
        for ifn in filter(None, (lan, wg, wan)):
            rc, out, _ = self.run(["tc", "qdisc", "show", "dev", ifn])
            stale = rc == 0 and experiment_qdisc_present(out)
            self.add(f"stale.qdisc.{ifn}", FAIL if stale else PASS,
                     "htb/tbf root already present (stale experiment state? clean only experiment-owned state)" if stale else "no experiment root qdisc", True)
        rc, out, _ = self.run(["pgrep", "-af", "capture_engine|PythonController|ppqos"])
        self.add("stale.processes", FAIL if out.strip() else PASS, out.strip()[:200] or "none", True)
        self.add("stale.ipc_socket", FAIL if os.path.exists(self.cfg.get("gateway.ipc_path")) else PASS, self.cfg.get("gateway.ipc_path"), True)
        self.deployment(scenario)

    def deployment(self, scenario):
        scn = self.cfg.scenario(scenario)
        if not scn["classifier_enabled"]:
            return self.add("deploy", SKIP, "scenario A: classifier disabled, no engine/controller/model needed")
        dd = self.cfg.get("gateway.deploy_dir")
        if not dd:
            return self.add("deploy", FAIL, "gateway.deploy_dir unset")
        eng = Path(dd) / "bin" / scn["engine_variant"] / "capture_engine"
        self.add("deploy.engine_binary", PASS if os.access(eng, os.X_OK) else FAIL, str(eng), True)
        mf = json.loads((self.cfg.root / self.cfg.get("controllers.models_manifest")).read_text())
        want = mf["models"][scn["model_key"]]["sha256"]
        mp = Path(dd) / "src" / scn["controller_variant"] / "models" / "rf_model.joblib"
        if mp.exists():
            got = sha256_file(mp)
            self.add("deploy.model_hash", PASS if got == want else FAIL, f"{got[:12]}... vs manifest {want[:12]}...")
        else:
            self.add("deploy.model_hash", FAIL, f"{mp} not found")
        py = Path(dd) / "venv" / "bin" / "python"
        rc, out, err = self.run([str(py), "-c", "import sklearn;print(sklearn.__version__)"])
        want_v = mf["models"][scn["model_key"]]["introspected"].get("pickled_with_sklearn_version")
        self.add("deploy.sklearn_version", PASS if rc == 0 and out.strip() == want_v else FAIL,
                 f"venv={out.strip() or err.strip()[:80]} model pickled with {want_v}", True)

    # ---- server
    def server(self, scenario, purpose):
        self.cmds(["iperf3", "nginx", "wg", "chronyc", "pidstat"])
        self.clock()
        if purpose != "real":
            return
        rc, out, _ = self.run(["ss", "-H", "-ltn"])
        for port in (self.cfg.get("server.iperf3_realtime_port"), self.cfg.get("server.iperf3_bulk_port"), 443):
            self.add(f"listen.tcp.{port}", PASS if re.search(rf":{port}\s", out) else FAIL, "", True)
        rc, out, _ = self.run(["systemctl", "is-active", "nginx"])
        self.add("service.nginx", PASS if out.strip() == "active" else FAIL, out.strip(), True)
        if self.cfg.scenario(scenario)["wireguard"]:
            iface = self.cfg.get("wireguard.server_side.interface")
            rc, _, _ = self.run(["ip", "link", "show", iface]) if iface else (1, "", "")
            self.add("wireguard.server", PASS if rc == 0 else FAIL, str(iface), True)


def run_preflight(cfg: Config, role: str, scenarios: list, purpose: str, runner: Runner = default_runner):
    c = Checker(cfg, runner)
    c.config_checks(scenarios, purpose)
    refused = purpose == "real" and any(r.check == "config.required_values" and r.status == FAIL for r in c.results)
    try:
        if role == "client":
            c.client(scenarios, "dry-run" if refused else purpose)
        elif role == "gateway":
            c.gateway(scenarios[0], "dry-run" if refused else purpose)
        elif role == "server":
            c.server(scenarios[0], "dry-run" if refused else purpose)
    except Exception as e:  # never crash silently: record it
        c.add("preflight.internal_error", FAIL, f"{type(e).__name__}: {e}")
    if refused:
        c.add("preflight.device_checks", SKIP, "skipped: real-run config incomplete -> SSH/interface/route/qdisc/service/HLS checks were NOT attempted (only local dependency checks ran)")
    return c.results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", required=True, choices=["client", "gateway", "server"])
    ap.add_argument("--scenarios", nargs="+", default=["A", "B", "C", "D"])
    ap.add_argument("--scenario", help="single scenario (gateway/server roles)")
    ap.add_argument("--purpose", default="real", choices=["real", "dry-run"])
    ap.add_argument("--config-dir"); ap.add_argument("--config-json")
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)
    try:
        cfg = config_from_json(Path(a.config_json).read_text()) if a.config_json else load_config(a.config_dir)
    except (ConfigError, OSError, KeyError) as e:
        print(f"[FAIL] cannot load config: {e}", file=sys.stderr); return 2
    scn = [a.scenario] if a.scenario else a.scenarios
    res = run_preflight(cfg, a.role, scn, a.purpose)
    sym = {PASS: " OK ", FAIL: "FAIL", WARN: "WARN", SKIP: "SKIP"}
    for r in res:
        print(f"[{sym[r.status]}] {r.check}{' (needs real hardware; untested offline)' if r.needs_hardware and r.status != SKIP else ''}"
              f"{': ' + r.detail if r.detail else ''}")
    n_fail = sum(r.status == FAIL for r in res)
    print(f"\npreflight role={a.role} purpose={a.purpose} scenarios={scn}: {n_fail} FAIL, "
          f"{sum(r.status == WARN for r in res)} WARN -> {'NOT READY' if n_fail else 'READY'}")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps([asdict(r) for r in res], indent=2))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
