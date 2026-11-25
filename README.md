# PP-QoS experiment automation (work in progress: Phase 3 complete, see docs/PHASE3_REPORT.md)

Three devices: **client laptop (EndeavourOS)** = controller + results store; **gateway** = system under test;
**AWS EC2 Singapore** = iperf3 + HLS origin + WireGuard exit. Nothing here has been run on the real devices.

```
python3 -m unittest discover -s tests -v                 # local tests (27)
python3 tools/make_manifests.py --engine-first DIR --engine-post DIR --ctrl-first DIR --ctrl-post DIR
scripts/setup_client.sh   [--apply [--yes]]              # EndeavourOS/Arch; check-only by default
scripts/setup_gateway.sh  [--apply] --deploy-dir D --src-root S
scripts/setup_server.sh   [--apply] [--deploy-services]
scripts/preflight_{client,gateway,server}.sh --purpose dry-run|real [--scenarios A B C D]
scripts/prepare_hls.sh --source VIDEO --outdir DIR       # one-time, user-supplied video >= 100 s
```
Config: `config/experiment.yaml`, `scenarios.yaml`, `validation.yaml` (nulls = unknown; real runs refuse until set).
Docs: `docs/DECISIONS_NEEDED.md`, `docs/CALIBRATION.md`, `docs/RESULTS_LAYOUT.md`, `patches/README.md`.
