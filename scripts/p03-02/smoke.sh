#!/bin/bash
# P03-02 smoke test (read-only). Run INSIDE the lab VM as a user with sudo.
# usage: bash smoke.sh <label> <dir containing oom_events.py and oom_worker.py>
# Runs the worker as mcp-log-reader for the last 24 h and prints a summary plus RAM facts.
set -u
LABEL="${1:?usage: smoke.sh <label> <dir>}"; SRC="${2:?usage: smoke.sh <label> <dir>}"
U=mcp-log-reader
if [ "$(hostname)" != "linux-log-lab" ] && [ "${LAB_ALLOW_ANY_HOST:-}" != 1 ]; then
  echo "Refusing: this is not the lab VM"; exit 1
fi
D=/tmp/p03-02
sudo rm -rf "$D"; sudo mkdir -p "$D"
sudo cp "$SRC/oom_events.py" "$SRC/oom_worker.py" "$D/" && sudo chmod -R a+rX "$D" || exit 1
SINCE=$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ); UNTIL=$(date -u +%Y-%m-%dT%H:%M:%SZ)
REQ=$(printf '{"schema_version":"1.0.0","session_id":"smoke","trace_id":"smoke-%s","logical_tool_call_id":"smoke-1","attempt_id":"smoke-1","since":"%s","until":"%s"}' "$LABEL" "$SINCE" "$UNTIL")
OUT="$HOME/p03-02-$LABEL.txt"
{
echo "##### P03-02 smoke $LABEL - $(date -Is) - $(hostname)"
sudo -u "$U" id
echo "=== worker (window $SINCE .. $UNTIL)"
printf '%s' "$REQ" | sudo -u "$U" /usr/bin/python3 -I "$D/oom_worker.py" > /tmp/p03-02-response.json; echo "worker exit code: $?"
/usr/bin/python3 - <<'PY'
import json
r = json.load(open("/tmp/p03-02-response.json"))
print("status=%s error_code=%s truncated=%s" % (r["status"], r["error_code"], r["truncated"]))
d = r["data"]
for c in d.get("coverage", []):
    print("coverage: %-15s %-18s oldest=%s" % (c["source"], c["status"], c["oldest_available_at"]))
print("message:", d.get("message"))
for e in d.get("events", []):
    print("event: %s pid=%s process=%s total_vm_kb=%s" % (e["timestamp"], e["pid"], e["process"], e["total_vm_kb"]))
print("events=%s evidence=%s" % (len(d.get("events", [])), len(r["evidence"])))
PY
echo "=== RAM facts readable by $U (read-only)"
sudo -u "$U" grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
} 2>&1 | tee "$OUT"
echo "saved: $OUT"
