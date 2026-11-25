#!/usr/bin/env bash
# Device 1 (CLIENT): groupmate's laptop. CHECK-ONLY by default.
# Installs only what the client needs: iperf3, VLC (cvlc), ssh, rsync, chrony, Python analysis stack.
#
# Supported distros: EndeavourOS / Arch (pacman), Ubuntu / Debian (apt), Fedora (dnf).
# The client OS is not hardcoded — this script detects it automatically.
#
# Usage:
#   --check         (default) report what is missing; change nothing
#   --apply         install missing packages after confirmation
#   --yes           skip confirmation prompt (only with --apply)
usage() { cat <<U
Usage: $0 [--check] [--apply [--yes]]
  --check   (default) report what is missing; change nothing
  --apply   install missing packages / enable chronyd, after confirmation
  --yes     skip the confirmation prompt (only valid with --apply)
Run this on the CLIENT LAPTOP (your groupmate's machine), not on the gateway.
U
}
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; . "$HERE/lib/common.sh"
parse_common_args "$@"
detect_os

case "$PKG_MGR" in
  pacman|apt|dnf) : ;;
  *) echo "[!] Unsupported OS: id=$OS_ID. Supported: Arch/EndeavourOS (pacman), Ubuntu/Debian (apt), Fedora (dnf)." >&2; exit 3 ;;
esac

say "Required packages"
case "$PKG_MGR" in
  pacman) PKGS="python python-yaml python-numpy python-pandas python-scipy python-matplotlib iperf3 vlc openssh rsync chrony iproute2 iputils jq curl" ;;
  apt)    PKGS="python3 python3-yaml python3-numpy python3-pandas python3-scipy python3-matplotlib iperf3 vlc openssh-client rsync chrony iproute2 iputils-ping jq curl" ;;
  dnf)    PKGS="python3 python3-pyyaml python3-numpy python3-pandas python3-scipy python3-matplotlib iperf3 vlc openssh-clients rsync chrony iproute iputils jq curl" ;;
esac
for p in $PKGS; do check_pkg "$p"; done

say "Optional packages"
case "$PKG_MGR" in
  pacman) OPT="python-pyarrow ffmpeg" ;;
  apt)    OPT="python3-pyarrow ffmpeg" ;;
  dnf)    OPT="python3-pyarrow ffmpeg" ;;
esac
for p in $OPT; do
  if pkg_installed "$p"; then ok "optional $p"
  else warn "optional $p not installed (pyarrow: Parquet output; ffmpeg: only needed if preparing HLS on this machine)"; fi
done

say "Commands"
for c in iperf3 cvlc ssh rsync chronyc ping python3 ip jq curl; do check_cmd "$c"; done

say "Python modules"
PY="${PYTHON:-python3}"
for m in yaml numpy pandas scipy matplotlib; do
  if "$PY" -c "import $m" >/dev/null 2>&1; then ok "python module $m"
  else bad "python module $m not importable (is the package installed and using the right python?)"; fi
done

say "Clock synchronisation"
if systemctl is-active --quiet chronyd 2>/dev/null || systemctl is-active --quiet chrony 2>/dev/null; then
  ok "chrony active"
  have_cmd chronyc && chronyc tracking 2>/dev/null | grep -i "system time" | sed 's/^/  /'
else
  warn "chrony not active — enable it (--apply does this) or use another NTP service"
  NEED_CHRONY=1
fi

say "SSH key (for gateway and EC2 access)"
warn "SSH key path is set in config/experiment.yaml (client.ssh_key_path). This script does not"
warn "generate or install keys. See the FILL IN instructions in experiment.yaml for how to do this."

say "Notes"
warn "This script does not configure Wi-Fi or join the gateway hotspot. That is a manual step."
warn "Run preflight_client.sh after joining the hotspot to verify the connection."

if confirm_apply; then do_install; fi

if [ "$MODE" = "apply" ] && [ "${NEED_CHRONY:-0}" = 1 ]; then
  if [ "$ASSUME_YES" = 1 ] || { [ -t 0 ] && read -r -p "Enable and start chrony now? [y/N] " a && [ "$a" = y ]; }; then
    sudo systemctl enable --now chronyd 2>/dev/null || sudo systemctl enable --now chrony
  fi
fi
summary_and_exit
