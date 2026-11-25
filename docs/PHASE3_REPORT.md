# Phase 3 report -- device setup foundation (21 Nov 2026)

No physical testing has happened. Nothing below was run on your laptop, gateway or EC2.

## A. What I verified in your actual repositories (not just the handoff)
| Item | Finding |
|---|---|
| Capture engine B | branch `initial_packets`, HEAD `75a8e31`, clean tree, `TargetWindowSize = 7`, parses *every* IP packet incl. handshakes (literal first-N) |
| Capture engine C/D | branch `adaptive_window`, HEAD `e8692c8`, **dirty tree** (comment, whitespace, `run.sh` deleted), N=8 hard-coded, filters to TLS/QUIC app-data, UDP payload>0, TCP non-SYN/FIN/RST payload>0 |
| Controllers | `initial_packets` HEAD `6f565a8`, `adaptive_window` HEAD `b49fec1`. Excluding `.venv`, `.git`, caches, the model and the benchmark CSV the two trees are **identical** (the handoff's claim holds) |
| Models | SHA-256 `010d1a37af6449...3caf4` (B) and `bf4e3118750e5c...ceab` (C/D), matching the handoff prefixes. Both RandomForest, 100 trees, 23 features, 3 classes (`Bulk-Transfer`, `Multimedia-Streaming`, `Real-Time-Interactive`). No training provenance embedded |
| NAT-aware patch | **Not present.** Controllers contain no NAT-aware logic; the patch file was not uploaded |
| Controller as shipped | LAN iface hard-coded `wlan0`; WAN iface defaults to **`wlan0`** if `GATEWAY_WAN_IFACE` is unset (would put a second HTB root on the LAN interface); defaults 45/40 Mbit, class ceilings = total (not the approved 3/8/7 and 4/18/18); deletes and recreates root qdiscs at start (conflicts with a TBF root); unenforced/duplicate predictions are not logged; benchmark CSV is cwd-relative and appended |
| Go engines | `go 1.26.4` in `go.mod`; capture device `wlan0` hard-coded; IPC path hard-coded `/tmp/flow_pipeline.ipc`; Go dials a socket the controller binds, so the controller must start first (already in the lifecycle) |
| Other | model pickles are scikit-learn **1.9.1**, repos pin 1.9.0; `.venv` folders (~360 MB) are machine-specific; old gateway scripts contain a hard-coded hotspot password |

## B. Files created (`experiment/`)
* `config/`: `experiment.yaml`, `scenarios.yaml`, `validation.yaml`, `calibration.template.yaml` (unknowns are `null`)
* `src/config_loader.py` (structure rules + per-operation required-value enforcement), `src/preflight.py` (client/gateway/server checks)
* `scripts/`: `setup_{client,gateway,server}.sh`, `preflight_{client,gateway,server}.sh`, `prepare_hls.sh`, `lib/common.sh`
* `tools/make_manifests.py`; generated `manifests/models.manifest.json`, `manifests/sources.manifest.json`
* `server/` nginx HLS + iperf3 unit + WireGuard server templates; `gateway/` WireGuard client template, `requirements.gateway.txt`
* `schemas/trial_metadata.schema.json`; `docs/` (this report, DECISIONS_NEEDED, CALIBRATION, RESULTS_LAYOUT); `patches/README.md`; `tests/`

## C. Tests actually run (all in my sandbox: Ubuntu, Python 3.12)
* **27 unit tests, all pass** (`python3 -m unittest discover -s tests`): config values match the handoff (HTB 18 = 3+8+7, ceilings, priorities, N=7/8/8, WG on/off);
  8 deliberate config corruptions are each rejected; real runs are refused with the template config; Scenario D does not demand WireGuard values;
  parsers (chrony, HLS playlist, `tc`); gateway preflight logic against a **mocked** command runner
  (WireGuard state vs scenario, route device vs scenario, stale HTB/TBF root, clock limit, missing WAN never defaulted, only read-only `tc qdisc show` is issued);
  manifest hashes match the handoff; no secrets in templates.
* Setup scripts: `bash -n` on all; check mode runs end-to-end (on Ubuntu it correctly reports missing deps and exits 1); `--yes` without `--apply` exits 2;
  `--apply` without a TTY or `--yes` refuses and installs nothing; client script exits 3 on a non-Arch OS; unknown args exit 2.
* `prepare_hls.sh` with a synthetic 104 s video: produced single-variant 1920x1080 H.264 HLS, 11 segments, 10 s each, manifest with SHA-256s; refused a 40 s source,
  a missing source, and overwriting an existing output.
* Manifest tool run against your four uploaded repositories.

## D. NOT tested, and why
* **Everything that touches real devices:** interface/route/hotspot/forwarding/WireGuard/qdisc/process checks in preflight, SSH reachability, chrony offsets
  (mocked only), HLS fetch over real HTTPS, nginx/iperf3 service deployment. They need your hardware.
* **`setup_client.sh --apply` on EndeavourOS** and the Arch package names (`vlc` for `cvlc`, `iputils` for `ping`): I could only check syntax and the non-Arch refusal.
  `setup_gateway.sh`/`setup_server.sh` `--apply` paths (package install, `go build`, venv creation, nginx/systemd deployment) were not executed.
* **Go builds**: no Go toolchain in my sandbox, so neither engine was compiled here.
* **`pip install -r gateway/requirements.gateway.txt`**: no network here; the pinned versions come from your repos plus the 1.9.1 change.
* **HLS playback by cvlc and burst/five-tuple behaviour**: not tested; `prepare_hls.sh` only proves the media files are well formed.

## E. Limitations
Preflight checks that a playlist can be fetched and is single-variant/VOD/long enough, not that VLC plays it. A passing preflight is necessary, not sufficient.
Cross-device clock agreement is bounded by each device's own chrony offset; a direct client-gateway-EC2 offset measurement belongs to Phase 4.
Mocked tests prove the decision logic, not that the Linux commands behave as assumed.

## F. Your next actions
1. Decide/answer **docs/DECISIONS_NEEDED.md** items 1-4 (Scenario A capacity, missing NAT patch, reference-phase/RTT prober, scikit-learn 1.9.1).
2. Upload `0001-controller-nat-aware-upload-filter.patch`.
3. Fill the `null`s in `config/experiment.yaml` as you learn them (gateway WAN interface, SSH details, EC2/WireGuard values); none of this blocks Phase 4 coding.
4. On the laptop: `scripts/setup_client.sh` (check), then `--apply` if you agree; `python3 -m unittest discover -s tests`.
5. On the gateway: install Go >= 1.26.4 manually, then `scripts/setup_gateway.sh --check`.
6. Supply the source video (>= 100 s) when available; run `prepare_hls.sh` once.

## G. Proposed Phase 4 (remote management) once you answer the above
SSH manager (host/user/key from config, BatchMode, timeouts), deployment of patched working copies to the gateway with per-trial source/patch hashes,
gateway per-trial and per-scenario scripts that coordinate with the controller's HTB roots (one root qdisc per interface), log retrieval via rsync,
P2/G1/G2/G3 patches with overhead measurement, and `main.py` with dry-run first.
