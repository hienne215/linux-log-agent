#!/bin/bash
# P03-01: lab fixtures (failed unit, journal lines, simulated OOM line). Run INSIDE the lab VM only.
set -u
if [ "$(hostname)" != "linux-log-lab" ] && [ "${LAB_ALLOW_ANY_HOST:-}" != 1 ]; then
  echo "Refusing: this is not the lab VM (set LAB_ALLOW_ANY_HOST=1 to override)"; exit 1
fi
UNIT=/etc/systemd/system/lab-backend.service
case "${1:-}" in
  up)
    sudo tee "$UNIT" >/dev/null <<'UNITEOF'
[Unit]
Description=Lab fixture - backend that always crashes
[Service]
Type=simple
ExecStart=/bin/sh -c 'echo "lab-backend: cannot bind 0.0.0.0:8080 - address already in use" >&2; exit 3'
Restart=no
UNITEOF
    sudo systemctl daemon-reload
    sudo systemctl start lab-backend.service 2>/dev/null
    logger -t lab-fixture -p user.err "lab-fixture: database connection refused (127.0.0.1:5432)"
    echo "lab-fixture: slow request 5231ms" | systemd-cat -t lab-fixture -p warning
    # Simulated OOM line (NOT a real OOM) written to the kernel log buffer.
    echo "<3>Out of memory: Killed process 4242 (lab-oom) total-vm:409600kB, anon-rss:102400kB" | sudo tee /dev/kmsg >/dev/null
    sleep 2; systemctl --failed --no-pager ;;
  down)
    sudo systemctl stop lab-backend.service 2>/dev/null
    sudo rm -f "$UNIT"; sudo systemctl daemon-reload; sudo systemctl reset-failed lab-backend.service 2>/dev/null
    echo "fixture unit removed (journal lines stay until reboot/rotation; restore the snapshot for a fully clean state)" ;;
  *) echo "usage: $0 up|down"; exit 2 ;;
esac
