# Decisions and inputs — status log

Records every decision made about the experiment design. Nothing is changed silently;
every resolved item shows what was decided and what (if anything) was updated in config.

---

## 1. Scenario A capacity ✅ RESOLVED (final)
**Decision: Option 3 — revert to original approved plan. A WAN TBF = 20 Mbit/s.**

`config/experiment.yaml` updated:
- `qos.scenario_a_effective_rate_mbit`: 18 → **20**
- `qos.scenario_a_rate_decision`: `option2_approved` → `option3_approved`

**What this means in practice:**

| | Scenario A | Scenarios B / C / D |
|---|---|---|
| Shaper location | WAN interface (after WireGuard wraps packet) | wg0 / LAN (before WireGuard wraps packet) |
| Configured rate | 20 Mbit/s | 18 Mbit/s (= 20 × 0.90 HTB root fraction) |
| Inner upload capacity | ~19.3 Mbit/s (20 minus ~3.5% WG overhead) | 18 Mbit/s exactly |
| Download (LAN egress) | 20 Mbit/s | 18 Mbit/s |

The gap is structural — it comes from where the qdisc sits — and cannot be fully
eliminated without moving the Scenario A shaper. Option 3 keeps the original plan,
keeps the numbers simple to explain, and minimises the absolute size of the gap
compared to Option 2 (which widened it to 2.7 Mbit/s).

**What you must disclose in the manuscript (Group A, item 3 in section 8):**
One sentence in the experimental setup section: Scenario A uses a 20 Mbit/s WAN TBF,
which limits outer (WireGuard-encapsulated) traffic; the effective inner upload capacity
is approximately 19.3 Mbit/s due to per-packet WireGuard overhead (~60 bytes), compared
to exactly 18 Mbit/s for Scenarios B–D whose HTB root sits on the wg0 interface before
encapsulation. This is a minor structural difference inherent to the Scenario A design
and does not affect the classifier evaluation.

---

## 2. NAT-aware upload filter patch ✅ RESOLVED
**Decision: Apply the patch. Use conntrack marks for Scenario D upload filter.**

The patch file `0001-controller-nat-aware-upload-filter.patch` was not in the uploads
and will be **written from scratch in Phase 4** based on the handoff description.
It is listed in `patches/README.md` as patch 0001.

**Why the patch is needed (summary):**
Linux MASQUERADE rewrites the source IP of upload packets before they leave the WAN
interface. The controller's tc filter matches on source IP. Without the patch, the filter
looks for the client's LAN address (e.g. 10.42.0.50) on the WAN interface, but by then
the address is already the gateway's public WAN IP — the filter never matches, and packets
fall through to the default HTB class regardless of what the classifier decided.

**Impact by scenario:**
- Scenarios A: no classifier, no filter — not affected.
- Scenarios B/C: HTB root on wg0 (pre-NAT). Filter on wg0 matches correctly without
  the patch. Patch still applied for consistency and correctness on the LAN-side filter.
- Scenario D: HTB root on WAN interface (post-NAT). **Without the patch, upload QoS
  enforcement is silently broken for D.** This is the primary reason the patch is needed.

**What the patch does:**
Instead of matching upload flows by source IP, the controller stamps a `conntrack` mark
on the connection when it classifies it (one mark value per traffic class). The tc filter
on the WAN interface matches on that mark instead of the IP address. Marks are attached
to the kernel connection tracking entry, so they survive MASQUERADE.

**What Phase 4 will do:**
1. Write `patches/0001-controller-nat-aware-upload-filter.patch` targeting both
   Python controllers.
2. Verify the patch applies cleanly to both controller working copies.
3. Add the patch hash to `manifests/sources.manifest.json`.
4. Preflight will confirm `conntrack` is available on the gateway before any trial.

---

## 3. Reference-period composition and RTT prober ✅ RESOLVED
**Decision: Follow the manuscript.**

`config/experiment.yaml` updated:
- `workloads.rtt_probe.confirmed_by_user`: false → **true**

**Trial lifecycle confirmed:**
1. **Reference phase (15 s):** Only the real-time UDP stream (iperf3 UDP, 64 Kbps) runs.
   The RTT prober (100 ms interval, ICMP + UDP) also runs throughout.
   The purpose is to establish a clean per-flow baseline with no competing traffic.
2. **Active phase (75 s):** Bulk TCP (iperf3, 4 parallel streams) and HLS streaming
   (cvlc, EC2 → client) start in addition to the continuing real-time stream and prober.
   This is when the classifier and QoS enforcement are active for scenarios B/C/D.
3. **Cooldown (30 s):** All workloads stop; qdiscs remain until the teardown step.

**What this means for Scenario A:** The classifier is disabled, so the reference/active
distinction only matters for analysis (comparing RTT/throughput before and after competing
traffic). The same lifecycle is used for consistency.

---

## 4. scikit-learn version ✅ RESOLVED
**Decision: Approved — use 1.9.1.**

`gateway/requirements.gateway.txt` pins **scikit-learn==1.9.1**, which matches the version
both `rf_model.joblib` files were pickled with. The controller repos' own `.venv` (1.9.0)
and `requirements.txt` (1.9.0) are ignored; only `requirements.gateway.txt` is used for
deployment. Preflight checks the installed version on the gateway before every trial.

**What you must keep in mind:** If you retrain the models, pin the new sklearn version
in `gateway/requirements.gateway.txt` and regenerate the manifests.

---

## 5. Dirty / non-reproducible source trees ✅ RESOLVED
**Decision: Disregard** (build from HEAD commits; ignore working-tree edits and old venvs).

The automation (Phase 4) will deploy from committed HEAD, not from the working-tree state.
The specific items and their resolution:

| Item | Resolution |
|---|---|
| `CaptureEngine_Post_N` uncommitted edits | Build from HEAD `e8692c8`; working-tree diff is cosmetic only |
| `README.md` deleted in both controllers | Irrelevant to deployment |
| `qos_telemetry_benchmarks.csv` modified | Per-trial output path is set by Phase 4 (P2 patch); old data ignored |
| `.venv` dirs (~360 MB, Python 3.14.7, `/home/darren/...` paths) | Never deployed; gateway uses the venv built by `setup_gateway.sh` |

---

## 6. Existing gateway scripts (`start_gateway.sh`, `start_wo_wireguard.sh`) ✅ RESOLVED
**Decision: Disregard** — the automation will not call these scripts.

No action required. The scripts are left in the repository as-is. Note that they contain
a hard-coded hotspot password in the git history; consider rotating that credential when
convenient, independently of this experiment.

---

## 7. Config null values — fill-in instructions ℹ️ PENDING (you fill these in later)
All `null` values in `config/experiment.yaml` now have inline instructions explaining
exactly what each value means and how to find it. Nothing has been invented or guessed.

**Checklist — fill these in as you gather the information:**

### Experiment
- [ ] `experiment.max_attempts_per_scenario` — suggested 25–30

### Client (your laptop)
- [ ] `client.ssh_key_path` — path to private key for gateway + EC2 SSH
- [ ] `client.workload_bind_iface` — Wi-Fi iface that joins the gateway hotspot (`ip link`)
- [ ] `client.expected_client_ip` — IP your laptop gets from the hotspot DHCP

### Gateway
- [ ] `gateway.os_id` — from `/etc/os-release` on the gateway
- [ ] `gateway.host` — IP/hostname your laptop uses to SSH into the gateway
- [ ] `gateway.ssh_user` — Linux username on the gateway
- [ ] `gateway.ssh_key_path` — path on your laptop to the gateway SSH private key
- [ ] `gateway.management_path` — `"separate"` or `"shared"` (see inline comment)
- [ ] `gateway.wan_iface` — **critical**: WAN interface (`ip route` → default route device)
- [ ] `gateway.hotspot.ssid` — optional, for documentation
- [ ] `gateway.deploy_dir` — where experiment code lives on the gateway, e.g. `/opt/ppqos`
- [ ] `gateway.results_dir_remote` — where per-trial logs are written on the gateway
- [ ] `gateway.sudo_mode` — `"passwordless"` or `"interactive"` (`sudo -n true` to check)

### Server (EC2)
- [ ] `server.os_id` — from `/etc/os-release` on the EC2 instance
- [ ] `server.instance_type` — e.g. `t3.medium` (documentation only)
- [ ] `server.public_address` — EC2 public IPv4 (Scenario D target)
- [ ] `server.wireguard_address` — EC2 address inside the tunnel (Scenarios A–C target)
- [ ] `server.ssh_host` — hostname/IP for SSH from your laptop
- [ ] `server.ssh_user` — EC2 SSH username (`ubuntu`, `ec2-user`, etc.)
- [ ] `server.ssh_key_path` — path on your laptop to the EC2 `.pem` or private key
- [ ] `server.results_dir_remote` — where server logs are written on EC2

### WireGuard
- [ ] `wireguard.client_side.address` — gateway tunnel address, e.g. `10.9.0.2/24`
- [ ] `wireguard.client_side.private_key_path` — path on the GATEWAY to WG private key
- [ ] `wireguard.server_side.interface` — WG interface name on EC2, e.g. `wg0`
- [ ] `wireguard.server_side.address` — same as `server.wireguard_address`
- [ ] `wireguard.server_side.listen_port` — UDP port WG listens on EC2, e.g. `51820`
- [ ] `wireguard.server_side.public_key` — EC2 WG public key (safe to store here)
- [ ] `wireguard.endpoint` — `<ec2_public_ip>:<listen_port>` as seen by the gateway
- [ ] `wireguard.allowed_ips` — recommend `10.9.0.1/32` (split-tunnel)
- [ ] `wireguard.mtu` — after path-MTU measurement (see docs/CALIBRATION.md step 5)
- [ ] `wireguard.persistent_keepalive_s` — recommended `25`

### HLS / streaming
- [ ] `workloads.streaming.playlist_path` — `/hls/index.m3u8` after media prep on EC2
- [ ] `workloads.streaming.tls.server_name` — hostname matching the nginx certificate
- [ ] `workloads.streaming.tls.cert_path` — path to cert on EC2
- [ ] `workloads.streaming.tls.verify_mode` — `"normal"` or `"insecure"`

### Post-calibration (fill after docs/CALIBRATION.md)
- [ ] `qos.calibration_file` — point to `config/calibration.yaml` after calibration
- [ ] All `validation.thresholds.*` values in `config/validation.yaml`

### Phase 4 deployment (auto-filled by deployment scripts)
- [ ] `controllers.*.source_dir` — set by Phase 4 after patched working copies are deployed

---

## 8. Manuscript items to update ✅ LOGGED (check these when editing the manuscript)

### Group A — Experimental parameters (numbers that changed)
- [ ] **N=7 everywhere for Scenarios C and D.** The manuscript currently says N=7 for all
  classifier scenarios. The actual `adaptive_window` engine uses N=**8**. Every table, figure
  caption, and sentence that specifies N for C or D must be updated to N=8.
- [ ] **Scenario A bottleneck rate.** Now 18 Mbit/s (Option 2), not 20. Any sentence
  or table that states the Scenario A TBF rate must reflect 18 Mbit/s.
- [ ] **Scenario A upload note.** Add a sentence in the experimental setup section stating
  that A uses a 20 Mbit/s WAN TBF (outer/encapsulated traffic), giving an effective inner
  upload of ~19.3 Mbit/s due to WireGuard per-packet overhead (~60 bytes, ~3.5%), compared
  to exactly 18 Mbit/s for Scenarios B–D whose HTB root sits on wg0 before encapsulation.
  State that this structural difference is minor and does not affect the classifier evaluation.

### Group B — Interface and topology details (concrete values that were unknown)
- [ ] **`eth0` in Scenario D.** The manuscript names `eth0` as the WAN interface for
  Scenario D. The actual WAN interface is unknown until confirmed on the gateway.
  Replace `eth0` with the confirmed interface name (or use a generic description like
  "the gateway WAN interface") and add a footnote if necessary.
- [ ] **WireGuard MTU.** If the manuscript states a specific WireGuard MTU (1420 or similar),
  verify it matches the measured value from calibration (docs/CALIBRATION.md step 5).

### Group C — QoS and tc implementation details
- [ ] **`conntrack -F` (global flush).** If the manuscript mentions flushing the conntrack
  table as part of the teardown or scenario transition, change this to a targeted deletion
  (e.g. `conntrack -D` by mark or address). The handoff explicitly says to avoid a global
  flush because it disrupts unrelated connections on the gateway.
- [ ] **HTB class parameters.** Verify any table or figure in the manuscript that lists
  the HTB class rates/ceilings/priorities matches the approved values:
  Real-Time 3/4 Mbit, Multimedia 8/18 Mbit, Bulk 7/18 Mbit, priorities 1/2/3.

### Group D — Statistics and terminology
- [ ] **"99th percentile (p95)".** There is a contradiction in the manuscript: a sentence
  or caption says "99th percentile" but uses the abbreviation "p95", or vice versa.
  Pick one (p95 = 95th percentile, p99 = 99th percentile) and apply it consistently.
- [ ] **"7–15 clients" wording.** A sentence describes the testbed as representing
  "7–15 clients" or similar. This describes the N range across scenarios (7 for B, 8 for C/D),
  not actual concurrent clients. Reword to avoid implying multi-client traffic.

### Group E — HLS/streaming methodology
- [ ] **Adaptive streaming / QUIC.** If the manuscript describes HLS as adaptive (ABR)
  or uses HTTP/3 / QUIC for streaming delivery, correct this: the implementation uses
  **fixed single-variant 1080p HLS over HTTPS/TCP (TLS 1.2 or 1.3)**. No adaptive bitrate
  switching, no QUIC.
- [ ] **VLC as the HLS client.** If the manuscript does not mention VLC (`cvlc`) as the
  streaming client, add it. The choice of player affects how segments are requested
  (VLC's prefetch behaviour differs from browser-based players).

### Group F — Instrumentation and overhead
- [ ] **Instrumentation overhead disclosure.** The manuscript should include a brief
  discussion of measurement overhead: libpcap packet capture on the gateway (G1 patch
  adds ps_drop monitoring), iperf3 JSON logging, pidstat CPU/memory sampling interval,
  and the Python controller's ZeroMQ IPC latency. At minimum, state that CPU and memory
  of the controller and engine were monitored per trial and report any trial where overhead
  exceeded a threshold (to be set after calibration).
