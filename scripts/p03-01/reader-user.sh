#!/bin/bash
# P03-01: manage the low-privilege reader account. Run INSIDE the lab VM as a user with sudo.
set -u
U=mcp-log-reader
if [ "$(hostname)" != "linux-log-lab" ] && [ "${LAB_ALLOW_ANY_HOST:-}" != 1 ]; then
  echo "Refusing: this is not the lab VM (set LAB_ALLOW_ANY_HOST=1 to override)"; exit 1
fi
drop() { for g in systemd-journal adm; do sudo gpasswd -d "$U" "$g" >/dev/null 2>&1; done; }
case "${1:-}" in
  create)
    id "$U" >/dev/null 2>&1 || sudo adduser --system --group --no-create-home --shell /usr/sbin/nologin "$U"
    id "$U"; sudo -l -U "$U" ;;
  groups-none)         drop; id "$U" ;;
  groups-journal)      drop; sudo usermod -aG systemd-journal "$U"; id "$U" ;;
  groups-journal-adm)  drop; sudo usermod -aG systemd-journal,adm "$U"; id "$U" ;;
  remove)              sudo deluser "$U"; sudo delgroup "$U" 2>/dev/null; echo removed ;;
  *) echo "usage: $0 create|groups-none|groups-journal|groups-journal-adm|remove"; exit 2 ;;
esac
