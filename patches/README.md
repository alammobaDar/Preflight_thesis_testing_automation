# Patches (applied to WORKING COPIES only; original repositories are never modified)

Expected, applied in this order, each as a separate traceable file with its SHA-256 recorded in
`manifests/sources.manifest.json:patch_hashes` and in every trial's metadata:

| # | Name | Applies to | Status |
|---|------|-----------|--------|
| 0001 | controller NAT-aware upload filter (`0001-controller-nat-aware-upload-filter.patch`) | both Python controllers | **To be written in Phase 4.** Original file not in uploads; will be written from the handoff description. Uses conntrack marks instead of source IP matching so upload filters survive MASQUERADE on the WAN interface. Required for correct QoS enforcement in Scenario D. |
| P2 | log every prediction (incl. skipped/failed enforcement) | both Python controllers | approved, not yet written |
| G1 | periodic libpcap stats incl. `ps_drop` | both Go engines | approved, not yet written |
| G2 | first-seen timestamps -> first-packet-to-decision latency | Go engines + controllers | approved, not yet written |
| G3 | capture device from env var (default stays unset => refuse, no silent `wlan0`) | both Go engines | approved, not yet written |

Do not modify trained models, the feature compiler, observation-window logic or eligibility rules without explicit approval.
