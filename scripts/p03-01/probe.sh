#!/bin/bash
# P03-01: run read probes as mcp-log-reader. Usage: bash probe.sh <label>  -> ~/p03-01-<label>.txt
# Prints only readable/denied for sensitive files (never their content).
set -u
U=mcp-log-reader
LABEL="${1:?usage: probe.sh <label>}"
OUT="$HOME/p03-01-$LABEL.txt"
sudo -v || exit 1
{
echo "##### PROBE $LABEL - $(date -Is) - $(hostname)"
for c in \
  "id" \
  "journalctl -n 2 --no-pager" \
  "journalctl -u lab-backend.service -n 2 --no-pager" \
  "journalctl -t lab-fixture -n 2 --no-pager" \
  "journalctl -k --no-pager | grep -i 'out of memory' | tail -1" \
  "dmesg | grep -i 'out of memory' | tail -1" \
  "systemctl is-active nginx" \
  "systemctl --failed --no-pager | head -4" \
  "df -h /" \
  "ss -tlnp" \
  "nginx -t" \
  "test -r /var/log/syslog && echo readable || echo denied" \
  "test -r /var/log/auth.log && echo readable || echo denied" \
  "test -r /var/log/nginx/error.log && echo readable || echo denied" \
  "test -r /etc/nginx/nginx.conf && echo readable || echo denied" \
  "test -r /etc/shadow && echo READABLE || echo denied" \
  "test -r /etc/sudoers && echo READABLE || echo denied" \
  "test -r /etc/ssh/ssh_host_rsa_key && echo READABLE || echo denied" \
  "test -r /root && echo READABLE || echo denied" \
  "test -r /home/qtrang/.bash_history && echo READABLE || echo denied" \
  "sudo -n true"
do
  echo "=== $c"
  out=$(sudo -u "$U" env SYSTEMD_COLORS=0 bash -c "$c" 2>&1 | head -3)
  echo "${out:-<no output>}"
done
} 2>&1 | tee "$OUT"
echo "saved: $OUT"
