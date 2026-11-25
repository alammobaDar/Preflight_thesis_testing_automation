#!/usr/bin/env bash
# Device 2: gateway / system under test. CHECK-ONLY by default. Run ON THE GATEWAY.
# Distro is not confirmed: apt, dnf and pacman are supported.
# This script never changes network, hotspot, firewall, routes, NAT or qdiscs.
usage() { cat <<U
Usage: $0 [--check] [--apply [--yes]] [--deploy-dir DIR] [--src-root DIR]
  --deploy-dir DIR  gateway.deploy_dir (required for build/venv steps)
  --src-root DIR    contains the four working copies:
                    capture_engine_first_n capture_engine_post_n python_controller_first_n python_controller_post_n
  --apply           install missing OS packages; build both Go variants; create the Python venv (all idempotent)
Go is NEVER installed by this script (distro Go is often too old): it only checks the version.
U
}
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; . "$HERE/lib/common.sh"
parse_common_args "$@"
DEPLOY_DIR=""; SRC_ROOT=""
set -- "${REST_ARGS[@]+"${REST_ARGS[@]}"}"
while [ $# -gt 0 ]; do
  case "$1" in
    --deploy-dir) DEPLOY_DIR="${2:-}"; shift ;;
    --src-root)   SRC_ROOT="${2:-}"; shift ;;
    *) echo "[!] unknown argument $1" >&2; usage; exit 2 ;;
  esac; shift
done
REQ_GO="1.26.4"   # from go.mod of both engines
VARIANTS="capture_engine_first_n capture_engine_post_n"
detect_os
[ "$PKG_MGR" = "unknown" ] && { echo "[!] Unsupported OS id=$OS_ID (need pacman, apt or dnf)" >&2; exit 3; }

say "Required OS packages"
case "$PKG_MGR" in
  apt)    PK="iproute2 wireguard-tools libpcap-dev gcc python3 python3-venv python3-pip chrony sysstat conntrack iptables iperf3 jq" ;;
  dnf)    PK="iproute wireguard-tools libpcap-devel gcc python3 python3-pip chrony sysstat conntrack-tools iptables iperf3 jq" ;;
  pacman) PK="iproute2 wireguard-tools libpcap gcc python python-pip chrony sysstat conntrack-tools iptables iperf3 jq" ;;
esac
for p in $PK; do check_pkg "$p"; done
say "Commands used by the experiment"
for c in tc ip wg wg-quick conntrack pidstat chronyc iptables python3; do check_cmd "$c"; done
have_cmd nmcli && ok "nmcli present (hotspot is managed manually / by the user, not by this script)" || warn "nmcli not found: confirm how the hotspot is provided"

say "Go toolchain (>= $REQ_GO required by go.mod)"
if have_cmd go; then
  GOV="$(go version | awk '{print $3}' | sed 's/^go//')"
  if version_ge "$GOV" "$REQ_GO"; then ok "go $GOV"; else bad "go $GOV is older than $REQ_GO; install the official toolchain from go.dev manually (not done by this script)"; fi
else bad "go not found; install Go >= $REQ_GO manually (not done by this script)"; fi

say "Privileges"
if [ "$(id -u)" -eq 0 ]; then ok "running as root"; elif sudo -n true 2>/dev/null; then ok "passwordless sudo available"; else warn "sudo requires a password: automation (sudo_mode) must be set to 'interactive' or passwordless sudo configured"; fi

say "Deployment (informational unless --deploy-dir given)"
if [ -z "$DEPLOY_DIR" ]; then warn "no --deploy-dir: skipping build/venv checks"
else
  for v in $VARIANTS; do
    B="$DEPLOY_DIR/bin/$v/capture_engine"
    if [ -x "$B" ]; then ok "binary $B sha256=$(sha256sum "$B" | cut -c1-16)..."; else warn "binary $B not built yet"; MISSING_BIN=1; fi
  done
  if [ -x "$DEPLOY_DIR/venv/bin/python" ]; then
    ok "venv present"
    "$DEPLOY_DIR/venv/bin/python" - <<'PY' || bad "venv import check failed"
import sklearn, numpy, pandas, scipy, joblib, zmq
print("  sklearn", sklearn.__version__, "| numpy", numpy.__version__, "| pandas", pandas.__version__)
if sklearn.__version__ != "1.9.1":
    raise SystemExit("  [FAIL] scikit-learn must be 1.9.1 (the version both rf_model.joblib files were pickled with)")
PY
  else warn "venv $DEPLOY_DIR/venv not created yet"; MISSING_VENV=1; fi
fi

if confirm_apply; then do_install; fi

if [ "$MODE" = "apply" ] && [ -n "$DEPLOY_DIR" ] && [ -n "$SRC_ROOT" ]; then
  if [ "$ASSUME_YES" != 1 ]; then
    [ -t 0 ] || { echo "[!] not a terminal and --yes not given; refusing to build" >&2; exit 2; }
    read -r -p "Build Go variants and create venv under $DEPLOY_DIR? [y/N] " a; [ "$a" = y ] || { echo "aborted"; exit 2; }
  fi
  mkdir -p "$DEPLOY_DIR/bin" "$DEPLOY_DIR/venv"
  for v in $VARIANTS; do
    [ -d "$SRC_ROOT/$v" ] || { bad "missing source dir $SRC_ROOT/$v"; continue; }
    mkdir -p "$DEPLOY_DIR/bin/$v"
    # Requires network access for module download unless the module cache is populated.
    ( cd "$SRC_ROOT/$v" && go build -trimpath -o "$DEPLOY_DIR/bin/$v/capture_engine" ./cmd/main ) \
      && ok "built $v" || bad "go build failed for $v"
  done
  [ -x "$DEPLOY_DIR/venv/bin/python" ] || python3 -m venv "$DEPLOY_DIR/venv"
  REQ="$HERE/../gateway/requirements.gateway.txt"
  "$DEPLOY_DIR/venv/bin/pip" install -r "$REQ" && ok "venv packages installed" || bad "pip install failed"
elif [ "$MODE" = "apply" ]; then
  warn "build/venv steps need both --deploy-dir and --src-root"
fi
summary_and_exit
