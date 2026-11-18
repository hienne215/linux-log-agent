# Runbook: reader account, fixtures and probes (P03-01)

Everything here runs **inside the lab VM** `linux-log-lab`, never on the Windows host. To undo all of it, restore the snapshot `clean-baseline-v1` (see `docs/setup-guide.md`).

## 1. Prepare

From PowerShell on the host, copy the scripts into the VM and fix line endings:

```
scp -r .\scripts\p03-01 <lab-user>@192.168.56.101:~/
ssh <lab-user>@192.168.56.101
cd ~/p03-01 && sed -i 's/\r$//' *.sh
```

## 2. Create the account and the fixtures

```
bash reader-user.sh create
bash fixtures.sh up
```

Expected: `id` shows only the group `mcp-log-reader`; `sudo -l -U` says "not allowed to run sudo"; `systemctl --failed` lists `lab-backend.service`.

## 3. Run the probes (three stages)

```
bash reader-user.sh groups-none;        bash probe.sh A-no-groups
bash reader-user.sh groups-journal;     bash probe.sh B-journal
bash reader-user.sh groups-journal-adm; bash probe.sh C-journal-adm
```

Each run writes `~/p03-01-<label>.txt`. Compare with `docs/permission-matrix.md` section 3. Any `READABLE` line for `/etc/shadow`, `/etc/sudoers`, a host key, `/root` or a home directory is a defect: stop and report it.

## 4. Evidence to keep

1. Probe output (copy to the host): `scp <lab-user>@192.168.56.101:p03-01-*.txt .`
2. Screenshot of `sudo -l -U mcp-log-reader` and `sudo -u mcp-log-reader journalctl -u lab-backend.service -n 3 --no-pager`.

## 5. Final state and cleanup

Until the team decides the groups (matrix section 5), return to least privilege:

```
bash reader-user.sh groups-none
```

Remove the fixture and, if no longer needed, the account:

```
bash fixtures.sh down
bash reader-user.sh remove
```

Journal lines written by the fixtures stay until reboot or rotation. Restore the snapshot for a fully clean VM.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `bad interpreter` or `\r` errors | Run `sed -i 's/\r$//' *.sh` (Windows line endings) |
| "Refusing: this is not the lab VM" | Safety check; run only in `linux-log-lab` |
| `unknown user mcp-log-reader` | Run `bash reader-user.sh create` first |
| Journal empty in stage A | Expected; the account sees only its own messages without a group |
