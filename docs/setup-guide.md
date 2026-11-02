# Lab Environment Setup Guide (P00-04)

Status: Proposed (waiting for review)
Task: P00-04 - VM snapshot and resource inventory
Owner: Trang | Reviewer: Thuy
Date measured: 2026-11-02

## 1. Purpose and scope

This document records the host resources, the lab virtual machine (VM), the clean snapshot, how to restore it, and a test of running the VM and a local LLM at the same time.
All measurements below were taken on one team member's laptop. Other members should repeat the checks on their own machines.

All fault scenarios in this project run only inside the VM. The Windows host is never used for fault injection.

## 2. Host inventory

| Item | Value |
|---|---|
| OS | Windows 11 Home Single Language, build 26200 |
| CPU | 12th Gen Intel Core i5-1235U, 10 cores, 12 logical processors |
| RAM | 7,877 MB total |
| Disk (C:) | 213.37 GB used, 57.06 GB free (at measurement time) |
| GPU | Intel UHD Graphics only (no discrete GPU) |
| Virtualization | Enabled (checked in Task Manager) |
| Hypervisor | Oracle VirtualBox 7.2.14 r174565 |

Commands used (PowerShell):

```
systeminfo | findstr /C:"OS Name" /C:"OS Version" /C:"Total Physical Memory" /C:"Available Physical Memory"
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors
Get-PSDrive C
```

## 3. Lab VM specification

| Item | Value |
|---|---|
| VM name | `linux-log-lab` |
| OS | Ubuntu 20.04.6 LTS (installed from the 20.04.4 Desktop ISO, then updated with `apt upgrade`) |
| Kernel | 5.15.0-139-generic |
| Boot target | `multi-user.target` (no graphical interface, to save RAM) |
| RAM | 2048 MB (guest shows 1.9 GiB; swap 1.1 GiB) |
| vCPU | 2 |
| CPU processing cap | 100% (no cap). The VM can use at most 2 of the 12 host logical processors, so it cannot take all host CPU. |
| Virtual disk | 25 GB, dynamically allocated (guest `/`: 24 GB, 8.4 GB used, 37%) |
| Python | 3.8.10 (system default) |
| Services | nginx 1.18.0 (active), OpenSSH server (active) |
| Failed units | 0 (`systemctl --failed`) |

### Network

| Adapter | Type | Address |
|---|---|---|
| Adapter 1 | NAT | 10.0.2.15 (internet access for package installs) |
| Adapter 2 | Host-only | 192.168.56.101 (lab network; assigned by DHCP, may change) |

Checks from the Windows host:

```
ssh <lab-user>@192.168.56.101
curl.exe -I http://192.168.56.101
```

Result of the `curl.exe` check: `HTTP/1.1 200 OK`, `Server: nginx/1.18.0 (Ubuntu)`.

Credentials are shared privately inside the team and are never committed to the repository.

## 4. Clean snapshot

| Item | Value |
|---|---|
| Snapshot name | `clean-baseline-v1` |
| Snapshot UUID | `670a3859-789c-4143-a8fb-818977ad71b4` |
| Description | Ubuntu 20.04.6, nginx 1.18 + ssh, 0 failed units, 02/11/2026 |
| Taken while | VM powered off |

To list snapshots (PowerShell):

```
& "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" snapshot linux-log-lab list --details
```

## 5. How to restore the snapshot

Restore before every evaluation run that needs a clean state.

### Using the VirtualBox window (tested)

1. Power off the VM completely (status must be **Powered Off**).
2. Open the **Snapshots** view of the VM.
3. Select `clean-baseline-v1` and click **Restore**.
4. If asked "Create a snapshot of the current machine state", untick it, then confirm.
5. Start the VM and check it (section 6).

### Using the command line (alternative, not tested yet)

```
& "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" controlvm linux-log-lab poweroff
& "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" snapshot linux-log-lab restore clean-baseline-v1
& "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" startvm linux-log-lab --type headless
```

Note: restoring discards everything done in the VM after the snapshot was taken.

## 6. Restore test result

Procedure and result (2026-11-02):

1. In the running VM, created `~/marker-before-restore.txt` and stopped nginx (`systemctl is-active nginx` returned `inactive`).
2. Powered off the VM and restored `clean-baseline-v1` from the VirtualBox window.
3. Started the VM and checked:

| Check | Result |
|---|---|
| `ls ~/marker-before-restore.txt` | `No such file or directory` (file is gone) |
| `systemctl is-active nginx ssh` | `active` and `active` |
| `systemctl --failed` | `0 loaded units listed` |
| `curl.exe -I http://192.168.56.101` from the host | `HTTP/1.1 200 OK`, nginx/1.18.0 |

The restore returned the VM to the clean state. Screenshots are attached to the Pull Request.

## 7. Running the VM and a local LLM together

Goal: find out whether the VM and an Ollama model can run at the same time on an 8 GB laptop.

Conditions:

- Host: the machine in section 2, CPU-only inference (no discrete GPU).
- VM `linux-log-lab` running with 2048 MB RAM.
- Chrome open with 1 tab; no other heavy applications.
- Ollama 0.40.13, model `qwen2.5:3b` (about 1.9 GB download).
- Prompt: "Journald trên Linux dùng để làm gì? Trả lời ngắn." (44 prompt tokens, short answer).

Memory:

| Moment | Available physical memory |
|---|---|
| VM running, model not loaded | 1,081 MB |
| VM running, after the first model run | 875 MB |

During the first run, Task Manager showed memory in use 7.0 / 7.7 GB (91%) and CPU about 53%.

Model speed (two runs, `ollama run --verbose`):

| Metric | Run 1 | Run 2 |
|---|---|---|
| Load duration | 13.1 s | 19.2 s |
| Total duration | 19.6 s | 25.5 s |
| Prompt eval rate | 20.8 tokens/s | 15.6 tokens/s |
| Generation rate (eval rate) | 5.20 tokens/s | 5.35 tokens/s |

VM behavior:

- The VM stayed responsive: `uptime` returned immediately while the model had been running.
- An earlier VM console session showed kernel messages `watchdog: BUG: soft lockup - CPU#0 stuck for 804s` and `1317s`, and `rcu_sched` stall warnings. The cause is not confirmed. Possible causes are the host being under memory pressure, or the laptop being paused or asleep while the VM was running.
- A later check with `sudo dmesg | grep -iE "soft lockup|rcu_sched"` returned no output.

Conclusion:

- A 3B model and the 2 GB VM can run together on this 8 GB laptop, but memory use is about 91%, so there is little headroom.
- Generation is slow (about 5 tokens/s). Prompt processing was measured only with a very short prompt. Agent prompts (tool descriptions and log excerpts) are much longer, so each step is expected to take longer. This was not measured.
- Models of 7B-8B were not tested and are not expected to fit next to the VM on this machine.
- Recommendation: use a free API as the main LLM for agent development and evaluation, keep a small local model as an option, and have each team member run the same smoke test on their own machine before the model is chosen.

## 8. Known limitations

- Ubuntu 20.04 is past its standard support period (the login banner says Hardware Enablement support ended April 2025). The VM is used only inside the isolated lab network. Do not run `do-release-upgrade`, so the baseline stays the same for everyone.
- The default Python is 3.8. The official MCP Python SDK needs a newer Python (3.10 or later; verify on the SDK page). If the MCP server runs inside the VM, a newer Python must be installed first.
- The Host-only address is assigned by DHCP and may change after the network is recreated. Check it with `ip -br a` inside the VM.

## 9. Acceptance criteria check

| Criterion | Status |
|---|---|
| Snapshot restore succeeds | Done (section 6) |
| No incident created on the host | Done (only the VM was modified) |
| VM and model concurrency determined | Done for `qwen2.5:3b` on this machine (section 7); 7B-8B not tested |
