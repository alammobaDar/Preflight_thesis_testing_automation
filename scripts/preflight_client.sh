#!/usr/bin/env bash
# Preflight for the client device. Read-only: never changes system state. Pass-through args go to src.preflight.
cd "$(dirname "${BASH_SOURCE[0]}")/.." && exec python3 -m src.preflight --role client "$@"
