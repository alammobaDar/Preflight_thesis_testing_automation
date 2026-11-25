# Calibration procedure (must be done on the real path BEFORE any full trial)

A 20 Mbit/s bottleneck is a *configured* number until these steps measure it. Record results in a copy of
`config/calibration.template.yaml` and point `qos.calibration_file` at it. Nothing below has been run.

1. **Unshaped capacity, both directions** (iperf3 client->EC2 and EC2->client; direct WAN and via WireGuard).
   The physical path must sustain clearly more than 20 Mbit/s, or the shaper is not the bottleneck.
2. **TBF only** (Scenario A layout): apply TBF (latency 100 ms), run iperf3 both ways, compare achieved
   goodput with the configured rate; confirm `tc -s qdisc` shows the backlog/drops at the TBF.
3. **HTB queue location (B, C, D layouts)**: under saturation the backlog must form in the HTB leaf queues
   (`tc -s class show`), not in the upstream WAN TBF (B/C). If the WAN TBF queues, the 0.90 fraction is too high
   for the measured WireGuard overhead.
4. **Class behaviour**: guarantees 3/8/7, ceilings 4/18/18, priorities 1/2/3; measure real-time latency under
   contention with bulk + streaming active; confirm ceilings are respected and borrowing works.
5. **WireGuard overhead and MTU**: measure path MTU, set `wireguard.mtu`, compare RTT/goodput tunnel vs direct.
6. **Filters hit counters**: after controller start, confirm tc u32 filters match real flows in the correct
   direction (upload on wg0 for B/C, WAN for D; download on LAN) and that post-SNAT addresses match (NAT patch).
7. Save `tc -s qdisc/class show` for every layout and reference the files in `final_qdisc_config_snapshot`.
8. Use the measurements to set `validation.yaml` thresholds (all currently null).
