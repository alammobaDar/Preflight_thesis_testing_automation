"""Config loading and 'required value' enforcement.

Design rule (handoff s.1, s.13): unknown values stay null in the YAML; operations that
genuinely need them are REFUSED rather than guessed. This module is the single place that
decides which config paths each operation requires.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ("A", "B", "C", "D")
CLASS_KEYS = ("realtime", "streaming", "bulk")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    experiment: dict
    scenarios: dict
    validation: dict
    root: Path = ROOT
    problems: list = field(default_factory=list)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.experiment
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def scenario(self, name: str) -> dict:
        if name not in self.scenarios["scenarios"]:
            raise ConfigError(f"unknown scenario {name!r}")
        return self.scenarios["scenarios"][name]


def config_to_json(cfg: Config) -> str:
    import json
    return json.dumps({"experiment": cfg.experiment, "scenarios": cfg.scenarios, "validation": cfg.validation})


def config_from_json(text: str) -> Config:
    import json
    d = json.loads(text)
    cfg = Config(d["experiment"], d["scenarios"], d["validation"])
    cfg.problems = validate_structure(cfg)
    return cfg


def load_config(config_dir: Path | str | None = None) -> Config:
    import yaml  # lazy: remote devices may lack PyYAML and use from_json() instead
    d = Path(config_dir) if config_dir else ROOT / "config"
    try:
        exp = yaml.safe_load((d / "experiment.yaml").read_text())
        scn = yaml.safe_load((d / "scenarios.yaml").read_text())
        val = yaml.safe_load((d / "validation.yaml").read_text())
    except (OSError, yaml.YAMLError) as e:  # noqa
        raise ConfigError(f"cannot load config from {d}: {e}") from e
    cfg = Config(exp, scn, val, root=d.parent)
    cfg.problems = validate_structure(cfg)
    return cfg


# ---------- structural validation (always runs; does not need real values) ----------

def validate_structure(cfg: Config) -> list[str]:
    errs: list[str] = []
    e = cfg.experiment
    if e.get("schema_version") != 1:
        errs.append("experiment.yaml: schema_version must be 1")
    n = cfg.get("experiment.valid_trials_per_scenario")
    if not isinstance(n, int) or n < 1:
        errs.append("experiment.valid_trials_per_scenario must be a positive integer")
    for k in ("reference_s", "active_s", "cooldown_s"):
        v = cfg.get(f"timing.{k}")
        if not isinstance(v, (int, float)) or v <= 0:
            errs.append(f"timing.{k} must be a positive number")
    # qos sanity
    frac = cfg.get("qos.htb_root_fraction")
    up = cfg.get("qos.bottleneck_up_mbit")
    if not (isinstance(frac, (int, float)) and 0 < frac <= 1):
        errs.append("qos.htb_root_fraction must be in (0, 1]")
    classes = cfg.get("qos.classes", {}) or {}
    if set(classes) != set(CLASS_KEYS):
        errs.append(f"qos.classes must define exactly {CLASS_KEYS}")
    else:
        root = (up or 0) * (frac or 0)
        total = sum(c["rate_mbit"] for c in classes.values())
        if abs(total - root) > 1e-6:
            errs.append(f"qos: class guarantees sum to {total} Mbit/s but HTB root is {root}")
        for k, c in classes.items():
            if c["ceil_mbit"] < c["rate_mbit"]:
                errs.append(f"qos.classes.{k}: ceil < rate")
            if c["ceil_mbit"] > root:
                errs.append(f"qos.classes.{k}: ceil exceeds HTB root {root}")
        if len({c["classid"] for c in classes.values()}) != 3:
            errs.append("qos.classes: classids must be unique")
    # scenarios
    sc = cfg.scenarios.get("scenarios", {})
    if tuple(sc) != SCENARIOS:
        errs.append(f"scenarios.yaml must define exactly {SCENARIOS} in order")
    models = {}
    for name, s in sc.items():
        if s["classifier_enabled"]:
            for k in ("observation_strategy", "n_packets", "engine_variant", "controller_variant", "model_key"):
                if s.get(k) in (None, ""):
                    errs.append(f"scenario {name}: classifier enabled but {k} unset")
            models[name] = s.get("model_key")
        else:
            for k in ("n_packets", "engine_variant", "controller_variant", "model_key"):
                if s.get(k) is not None:
                    errs.append(f"scenario {name}: classifier disabled but {k} is set")
            if s.get("classification_metrics"):
                errs.append(f"scenario {name}: must not report classification metrics")
        if s["target"] not in ("wireguard_address", "public_address"):
            errs.append(f"scenario {name}: bad target {s['target']!r}")
        if s["wireguard"] != (s["target"] == "wireguard_address"):
            errs.append(f"scenario {name}: wireguard flag inconsistent with target")
        # exactly one root qdisc per interface is enforced by having ONE type per egress slot
        for slot, q in s["qdisc"].items():
            if q["type"] not in ("tbf", "htb_sfq", "none"):
                errs.append(f"scenario {name}.{slot}: bad qdisc type {q['type']!r}")
    if models.get("B") and models.get("B") == models.get("C"):
        errs.append("scenarios B and C must use different model artifacts (first_n vs post_n)")
    if models.get("C") != models.get("D"):
        errs.append("scenarios C and D must use the same model artifact")
    return errs


# ---------- required-value enforcement (per operation) ----------

COMMON_REAL = [
    "client.os_id", "client.ssh_key_path", "client.workload_bind_iface",
    "gateway.host", "gateway.ssh_user", "gateway.ssh_key_path", "gateway.management_path",
    "gateway.wan_iface", "gateway.deploy_dir", "gateway.results_dir_remote", "gateway.sudo_mode",
    "server.public_address", "server.ssh_host", "server.ssh_user", "server.ssh_key_path",
    "server.results_dir_remote", "server.os_id",
    "workloads.streaming.playlist_path", "workloads.streaming.tls.server_name",
    "workloads.streaming.tls.verify_mode", "qos.calibration_file",
    "experiment.max_attempts_per_scenario",
]
WG_REAL = [
    "server.wireguard_address", "wireguard.client_side.address", "wireguard.client_side.private_key_path",
    "wireguard.server_side.address", "wireguard.server_side.listen_port",
    "wireguard.server_side.public_key", "wireguard.endpoint", "wireguard.allowed_ips",
]
SETUP_ROLE = {
    "client": ["client.ssh_key_path"],
    "gateway": ["gateway.deploy_dir"],
    "server": ["server.ssh_host"],
}


def required_paths(cfg: Config, scenarios: Iterable[str], purpose: str = "real") -> list[str]:
    """Config paths that must be non-null for `purpose` in {'real','dry-run','setup:<role>'}."""
    if purpose == "dry-run":
        return []                      # dry-run may use placeholders; it must not touch devices
    if purpose.startswith("setup:"):
        return list(SETUP_ROLE[purpose.split(":", 1)[1]])
    paths = list(COMMON_REAL)
    if any(cfg.scenario(s)["wireguard"] for s in scenarios):
        paths += WG_REAL
    return paths


def missing_values(cfg: Config, scenarios: Iterable[str], purpose: str = "real") -> list[str]:
    miss = [p for p in required_paths(cfg, scenarios, purpose) if cfg.get(p) in (None, "")]
    if purpose == "real":
        for k, v in (cfg.validation.get("thresholds") or {}).items():
            if v is None:
                miss.append(f"validation.thresholds.{k}")
        if not cfg.get("workloads.rtt_probe.confirmed_by_user") and cfg.get("workloads.rtt_probe.enabled"):
            miss.append("workloads.rtt_probe.confirmed_by_user (RTT prober is a proposal)")
        if cfg.get("qos.scenario_a_rate_decision") == "pending_user_review":
            miss.append("qos.scenario_a_rate_decision (pending user/adviser review; see DECISIONS_NEEDED.md)")
    return miss


def refuse_unless_ready(cfg: Config, scenarios: Iterable[str], purpose: str = "real") -> None:
    if cfg.problems:
        raise ConfigError("config structurally invalid:\n  - " + "\n  - ".join(cfg.problems))
    miss = missing_values(cfg, list(scenarios), purpose)
    if miss:
        raise ConfigError(
            f"refusing {purpose!r} run: required values missing/unresolved:\n  - " + "\n  - ".join(miss))


def validate_ip_fields(cfg: Config) -> list[str]:
    """Type-check addresses that ARE set (null is fine here)."""
    errs = []
    for p in ("gateway.lan_address", "server.public_address", "server.wireguard_address",
              "wireguard.client_side.address", "client.expected_client_ip"):
        v = cfg.get(p)
        if v:
            try:
                ipaddress.ip_interface(str(v)) if "/" in str(v) else ipaddress.ip_address(str(v))
            except ValueError:
                errs.append(f"{p}: {v!r} is not a valid IP")
    sn = cfg.get("gateway.lan_subnet")
    if sn:
        try:
            net = ipaddress.ip_network(sn)
            la = cfg.get("gateway.lan_address")
            if la and ipaddress.ip_address(la) not in net:
                errs.append("gateway.lan_address is outside gateway.lan_subnet")
        except ValueError:
            errs.append(f"gateway.lan_subnet: {sn!r} invalid")
    if cfg.get("gateway.wan_iface") in ("eth0",):
        errs.append("gateway.wan_iface=eth0 looks like an unverified default; confirm on the real gateway")
    return errs


def resolve_rate(cfg: Config, rate_key: str) -> float:
    v = cfg.get(rate_key)
    if v is None:
        raise ConfigError(f"rate key {rate_key} unset")
    return float(v)


def htb_root_mbit(cfg: Config) -> float:
    return cfg.get("qos.bottleneck_up_mbit") * cfg.get("qos.htb_root_fraction")
