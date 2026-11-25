# Results layout (client laptop is the central store)

```
results/raw/<scenario>/<trial_id>/          # one directory per ATTEMPT, valid or not; never overwritten
  trial_metadata.json                       # schemas/trial_metadata.schema.json
  config_snapshot/                          # exact experiment/scenarios/validation/calibration used
  client/   iperf3_udp.json iperf3_tcp.json cvlc.log rtt_probe.csv workload_exit_status.json clock.txt
  gateway/  classification_events.csv       # P2: EVERY prediction incl. skipped/failed enforcement
            engine.log libpcap_stats.csv    # G1: ps_recv/ps_drop/ps_ifdrop
            qdisc_before.txt qdisc_after.txt tc_class_stats.csv conntrack_notes.txt
            cpu_mem.csv pidstat.txt         # per-process CPU/RSS
            wireguard_state.txt routes.txt clock.txt
  server/   iperf3_5201.json iperf3_5203.json nginx_hls_access.log pidstat.txt clock.txt wg_show.txt
results/processed/   per-trial tables (CSV/Parquet) regenerated from raw
results/figures/     plots
results/reports/     report-ready exports
```
Rules: raw files are chmod a-w after collection and hashed into `trial_metadata.json:files`;
invalid attempts keep status + reason codes; the runner resumes by scanning `trial_metadata.json`
files and counting `status == valid` per scenario (target 15); nothing is ever overwritten.
