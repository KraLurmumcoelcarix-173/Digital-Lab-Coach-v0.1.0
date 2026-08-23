#!/usr/bin/env bash
# Digital Lab Coach uninstall (macOS / Linux).
# Windows users: run UNINSTALL.bat instead.
echo "This removes Digital Lab Coach's local data folder: ~/.dlc"
echo "(settings, machine-id cache, telemetry spool)."
echo
echo "NOTE: your course usage limits live on the course server and are"
echo "keyed to this machine - uninstalling or re-downloading never resets them."
echo
read -r -p "Type YES to continue: " CONFIRM
[ "$CONFIRM" = "YES" ] || { echo "Cancelled."; exit 0; }
rm -rf "$HOME/.dlc"
echo "Done. To finish, delete this folder:  $(cd "$(dirname "$0")" && pwd)"
