#!/usr/bin/env bash
# Shared helpers for setup_*.sh. SOURCE this file; do not execute it.
# Contract: default mode is CHECK-ONLY (changes nothing). System changes need --apply AND confirmation.

MODE="check"; ASSUME_YES=0; N_OK=0; N_WARN=0; N_BAD=0; TODO_PKGS=()

parse_common_args() {          # leaves unrecognised args in REST_ARGS
  REST_ARGS=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) MODE="check" ;;
      --apply) MODE="apply" ;;
      --yes|-y) ASSUME_YES=1 ;;
      -h|--help) usage; exit 0 ;;
      *) REST_ARGS+=("$1") ;;
    esac; shift
  done
  if [ "$ASSUME_YES" = 1 ] && [ "$MODE" != "apply" ]; then
    echo "[!] --yes only makes sense with --apply" >&2; exit 2
  fi
}

say()  { printf '[*] %s\n' "$*"; }
ok()   { printf '  [ OK ] %s\n' "$*"; N_OK=$((N_OK+1)); }
warn() { printf '  [WARN] %s\n' "$*"; N_WARN=$((N_WARN+1)); }
bad()  { printf '  [FAIL] %s\n' "$*"; N_BAD=$((N_BAD+1)); }

detect_os() {                   # sets OS_ID, OS_LIKE, PKG_MGR (pacman|apt|dnf|unknown)
  OS_ID="unknown"; OS_LIKE=""
  if [ -r /etc/os-release ]; then . /etc/os-release; OS_ID="${ID:-unknown}"; OS_LIKE="${ID_LIKE:-}"; fi
  PKG_MGR="unknown"
  case " $OS_ID $OS_LIKE " in
    *" arch "*|*" endeavouros "*) PKG_MGR="pacman" ;;
    *" debian "*|*" ubuntu "*)    PKG_MGR="apt" ;;
    *" fedora "*|*" rhel "*|*" centos "*|*" amzn "*) PKG_MGR="dnf" ;;
  esac
  say "OS: id=$OS_ID like='$OS_LIKE' package-manager=$PKG_MGR"
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

pkg_installed() {               # $1 = package name for $PKG_MGR
  case "$PKG_MGR" in
    pacman) pacman -Qi "$1" >/dev/null 2>&1 ;;
    apt)    dpkg -s "$1" >/dev/null 2>&1 ;;
    dnf)    rpm -q "$1" >/dev/null 2>&1 ;;
    *)      return 1 ;;
  esac
}

check_pkg() {                   # $1 = pkg name (already mapped). Records missing ones.
  if pkg_installed "$1"; then ok "package $1"; else bad "package $1 not installed"; TODO_PKGS+=("$1"); fi
}

check_cmd() {                   # $1 = command, $2 = hint
  if have_cmd "$1"; then ok "command $1 ($(command -v "$1"))"; else bad "command $1 missing${2:+ ($2)}"; fi
}

confirm_apply() {               # returns 0 only on explicit consent
  [ "$MODE" = "apply" ] || return 1
  [ "${#TODO_PKGS[@]}" -eq 0 ] && return 1
  echo; say "About to run (needs root): $(install_cmd_str)"
  [ "$ASSUME_YES" = 1 ] && return 0
  if [ ! -t 0 ]; then echo "[!] not a terminal and --yes not given; refusing to change the system" >&2; return 1; fi
  read -r -p "Proceed? [y/N] " a; [ "$a" = "y" ] || [ "$a" = "Y" ]
}

install_cmd_str() {
  case "$PKG_MGR" in
    pacman) echo "sudo pacman -S --needed ${TODO_PKGS[*]}" ;;
    apt)    echo "sudo apt-get install -y ${TODO_PKGS[*]}" ;;
    dnf)    echo "sudo dnf install -y ${TODO_PKGS[*]}" ;;
  esac
}

do_install() {
  case "$PKG_MGR" in
    pacman) sudo pacman -S --needed "${TODO_PKGS[@]}" ;;
    apt)    sudo apt-get install -y "${TODO_PKGS[@]}" ;;
    dnf)    sudo dnf install -y "${TODO_PKGS[@]}" ;;
  esac
}

summary_and_exit() {
  echo; say "Summary: ok=$N_OK warn=$N_WARN fail=$N_BAD (mode=$MODE)"
  if [ "$N_BAD" -gt 0 ] && [ "$MODE" = "check" ]; then
    [ "${#TODO_PKGS[@]}" -gt 0 ] && say "Missing packages would be installed with: $(install_cmd_str)  (re-run with --apply)"
  fi
  [ "$N_BAD" -eq 0 ]; exit $?
}

# version_ge A B  -> true if A >= B (dotted numeric versions)
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }
