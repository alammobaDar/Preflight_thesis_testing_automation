#!/usr/bin/env bash
# Device 3: AWS EC2 (Singapore). CHECK-ONLY by default. Run ON THE EC2 INSTANCE.
# EC2 OS is NOT confirmed: apt (Debian/Ubuntu), dnf (Amazon Linux/Fedora/RHEL) and pacman are handled.
usage() { cat <<U
Usage: $0 [--check] [--apply [--yes]] [--deploy-services]
  --apply            install missing packages
  --deploy-services  (with --apply) install nginx HLS site + iperf3 systemd units from server/ templates.
                     Needs env: PPQOS_SERVER_NAME PPQOS_TLS_CERT PPQOS_TLS_KEY PPQOS_HLS_ROOT
Never edits AWS security groups, firewalls, WireGuard keys, or certificates. Prints the ports you must open.
U
}
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; . "$HERE/lib/common.sh"
DEPLOY_SERVICES=0
ARGS=(); for a in "$@"; do [ "$a" = "--deploy-services" ] && DEPLOY_SERVICES=1 || ARGS+=("$a"); done
parse_common_args "${ARGS[@]+"${ARGS[@]}"}"
detect_os
[ "$PKG_MGR" = "unknown" ] && { echo "[!] Unsupported OS id=$OS_ID (need pacman, apt or dnf)" >&2; exit 3; }
[ "${#REST_ARGS[@]}" -gt 0 ] && { echo "[!] unknown argument ${REST_ARGS[0]}" >&2; usage; exit 2; }

say "Required packages"
for p in iperf3 nginx wireguard-tools chrony sysstat openssl; do check_pkg "$p"; done
for c in iperf3 nginx wg chronyc pidstat openssl ip; do check_cmd "$c"; done

say "Services (state only; this script does not start anything in check mode)"
for s in nginx chronyd chrony; do systemctl list-unit-files "$s.service" >/dev/null 2>&1 && \
  { systemctl is-active --quiet "$s" && ok "$s active" || warn "$s installed but not active"; }; done

say "Ports the experiment needs (open them in the AWS security group yourself)"
warn "TCP+UDP 5201 (iperf3 real-time), TCP+UDP 5203 (iperf3 bulk), TCP 443 (HLS), UDP <wireguard listen port>, TCP 22 (SSH from your management source only)"
warn "iperf3 UDP tests also need the TCP control connection on the same port"

if [ -f /etc/nginx/nginx.conf ] && ! grep -Eq 'include +/etc/nginx/conf\.d/\*\.conf' /etc/nginx/nginx.conf; then
  warn "nginx.conf does not include conf.d/*.conf: the site template would not be loaded"
fi

if confirm_apply; then do_install; fi

if [ "$DEPLOY_SERVICES" = 1 ]; then
  if [ "$MODE" != "apply" ]; then warn "--deploy-services ignored without --apply"; else
    for v in PPQOS_SERVER_NAME PPQOS_TLS_CERT PPQOS_TLS_KEY PPQOS_HLS_ROOT; do
      [ -n "${!v:-}" ] || { echo "[!] $v must be set for --deploy-services" >&2; exit 2; }; done
    for f in "$PPQOS_TLS_CERT" "$PPQOS_TLS_KEY"; do [ -r "$f" ] || { echo "[!] cannot read $f" >&2; exit 2; }; done
    if [ "$ASSUME_YES" != 1 ]; then
      [ -t 0 ] || { echo "[!] not a terminal and --yes not given; refusing" >&2; exit 2; }
      read -r -p "Install nginx site + iperf3 units (existing ppqos-* files are replaced)? [y/N] " a; [ "$a" = y ] || exit 2
    fi
    sed -e "s|@SERVER_NAME@|$PPQOS_SERVER_NAME|g" -e "s|@TLS_CERT@|$PPQOS_TLS_CERT|g" \
        -e "s|@TLS_KEY@|$PPQOS_TLS_KEY|g" -e "s|@HLS_ROOT@|$PPQOS_HLS_ROOT|g" \
        "$HERE/../server/nginx-hls.conf.template" | sudo tee /etc/nginx/conf.d/ppqos-hls.conf >/dev/null
    sudo nginx -t && ok "nginx config valid" || bad "nginx -t failed"
    IPERF="$(command -v iperf3 || echo /usr/bin/iperf3)"
    for port in 5201 5203; do
      sed -e "s|@PORT@|$port|g" -e "s|@IPERF3@|$IPERF|g" "$HERE/../server/ppqos-iperf3.service.template" \
        | sudo tee "/etc/systemd/system/ppqos-iperf3-$port.service" >/dev/null
    done
    sudo systemctl daemon-reload
    say "Units installed but NOT started. Start per trial via the remote manager, or: sudo systemctl start ppqos-iperf3-5201 ppqos-iperf3-5203"
    sudo systemctl reload nginx 2>/dev/null || warn "nginx not running; start it after verifying the config"
  fi
fi
summary_and_exit
