#!/usr/bin/env bash
# Restart compositor staging systemd units (supervisor first, then API).
set -euo pipefail

SERVICES=(
  compositor-media-supervisor-staging.service
  compositor-backend-test.service
)

run_systemctl() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    sudo systemctl "$@"
  else
    systemctl "$@"
  fi
}

echo "Restarting: ${SERVICES[*]}"
run_systemctl restart "${SERVICES[@]}"

echo
for svc in "${SERVICES[@]}"; do
  run_systemctl --no-pager status "$svc" || true
  echo
done
