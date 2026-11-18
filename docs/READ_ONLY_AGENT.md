# Canonical read only Agent

Application-owned integration for `service_status` (`nginx.service` only) and
`disk_usage` (`root` mapped to `/` in the collector namespace). This branch is
based on PR #24 HEAD `1b183c2`, which includes main `af1c3f1`; PR #24 is still
unmerged. It does not establish VM acceptance or eleven real collectors.

The Agent binding/terminal view is brought forward from the existing local
service demo. The privacy patch is byte-identical to reviewed PR #30 HEAD
`3739fda`. Neither dependency is silently treated as already merged on main.

## Runtime and launch

Use Python 3.12 with MCP SDK 2.3.0 and the repository's pinned dependencies in an
isolated venv. Do not use global pip or change reader permissions to obtain PASS.
From the repository root with that runtime:

```bash
PYTHONPATH=src .venv/bin/python -B -m agent.read_only
```

Or set an explicitly trusted Python runtime and use the launcher:

```bash
LINUX_LOG_AGENT_PYTHON=/path/to/approved/venv/bin/python ./scripts/linux-log-agent
```

Ollama is the default; the model, endpoint and 45-second per-request timeout
remain application-owned. The default scope exposes both tools. `--scope service`
and `--scope disk` narrow it. Unknown tools, arbitrary paths/units, model metadata
and model-owned `include_inodes` are rejected. `include_inodes=true` is added by
the application to the canonical disk request.

On a TTY, enter another question after each completed session; `thoát`, `exit` or
Ctrl+D ends input. Each question creates fresh bounded owners and correlation,
not retained model conversation history. `--once` exits after one question.
`--question` or `--report name.md` also processes one question. Reports use the
existing safe 0600/no-overwrite writer, under `reports/` relative to the current
directory. Never place private data in a question.

Example questions (capability suggestions, not a reliability benchmark):

- Nginx có đang chạy không?
- Ổ đĩa root còn bao nhiêu dung lượng?
- Kiểm tra trạng thái nginx.service và dung lượng ổ đĩa root.

`--backend mock` explicitly uses scripted model responses. MCP still uses the
real Linux collectors unless a test replaces the clients with synthetic fixtures.
Mock success is not live model selection or diagnostic accuracy.

## Ownership and budgets

The Coordinator and ToolBridge are reused. Two fixed, separate MCP stdio profiles
preserve the server's mutually exclusive profile policy. They share application
session/trace IDs; each tool has unique logical/attempt IDs and is called at most
once. Discovery schemas must match the canonical schemas before dispatch.

The selected scope allows at most one call per tool: at most two tools and three
LLM turns in a 120-second diagnosis session, with 45 seconds per LLM request and
15 seconds per MCP request. Single-tool scope allows two LLM turns. A model response may contain at most two native proposals in this mode (the
Adapter's general default remains one). All proposals are validated before the
first dispatch. The DecisionSource queues them for sequential Coordinator turns;
no additional model request is invented for a queued proposal. Queue state is
bound to the question/observation count and discarded on error or cleanup.
There is no retry, reconnect, mock fallback, restart or approval generation. Live calls remain
subject to the existing RAM gate. No new live inference is implied by these caps.

`run_coordinator()` and the existing retained full-session cleanup owner close
model, Bridge and all SDK clients. Cleanup budgets are separate. If pending, the
owner is retained and rejoined; it is not silently detached. Worker exit does not
prove Ollama backend inference has stopped. No hard wall SLA is claimed.

The binding validates full request/result schemas, correlation, text/native
consistency, `isError`, timestamps, source and unit. Results are copied before
returning so external mutation cannot modify the internal evidence ledger.
Canonical TIMEOUT/EXECUTION_UNKNOWN and transport loss keep unknown execution
semantics. Partial/truncated results remain limited; missing/invalid evidence
fails closed. Evidence provenance is preserved and redacted before model use.
Citation existence is checked, but semantic correctness still needs evaluation.

## Verification boundaries

Tests under `tests/canonical_agent/` are synthetic, including the test-only disk
API replacement. Fixed collector source strings in those fixtures are protocol
simulations, not proof of a real Linux acquisition. Do not use them as experiment
records. No Linux logs, services or system permissions are modified by tests.

The Windows child-environment fix retains only SYSTEMROOT and a fixed System32
PATH, not the parent's credential environment. Mock environment regression is
available on Linux; Windows runtime confirmation must be performed on Windows.

The VM TIMEOUT on PR #24 is still unresolved by this Agent change. A laptop
positive result does not close that gate. `scripts/probe_disk_worker_startup.py`
is an import-only diagnostic (no statvfs/systemctl), intended to measure startup
before choosing a fix; do not increase the worker timeout just to obtain PASS.

Journal/search, additional real collectors, complete anomaly/verification inputs,
approval/helper, eleven-tool acceptance and formal Week 2 gate are not completed
by this integration. No private journal read or remediation is authorized by the
CLI's presence.

## Current execution evidence

The final combined affected regression passed **479 cases, two optional skips,
zero failures/errors**, with 301 subtests reported separately (49.04 seconds,
Python 3.12.16/MCP 2.3.0). The count includes native-batch admission and safe policy
diagnostics. Do not sum earlier overlapping runs or treat test count as accuracy.
Mock/SDK synthetic success is not live model reliability.

One bounded mock-LLM + real-Linux two-tool session on the laptop completed with
service active/running, disk counters and two evidence records; cleanup completed.
These are actual observations in the laptop namespace, not VM reader acceptance.

The first live multi-tool attempt used one inference request, returned
OUTPUT_POLICY before any MCP dispatch, and closed all observed owners. It is
FAIL for the requested two-tool live scenario. The original generic code cannot
distinguish text-only, excess calls, unfinished generation or reasoning; do not
assert one cause without evidence. Fixed-enum diagnostics now preserve that
reason through IPC without content or raw reasoning. Bounded native-batch
admission was then verified offline and in the subsequent live run documented
below. The original one-tool checkout remains untouched and available as fallback.

## Focused test command

With the pinned test dependencies installed in the isolated venv, from repository
root (no live inference or Linux collector acquisition):

```bash
PYTHONPATH=src:.:tests/integration_boundary:tests/agent_loop:tests/async_llm:tests/integration_demo \
PYTHONDONTWRITEBYTECODE=1 RUN_MCP_STDIO_SMOKE=1 \
.venv/bin/python -B -m pytest -q --import-mode=importlib -p no:cacheprovider \
  tests/mcp_client tests/integration_boundary tests/llm tests/agent_loop \
  tests/agent_acceptance tests/cli tests/action_cli tests/async_llm \
  tests/integration_demo tests/anomaly tests/verify_fix tests/canonical_agent \
  tests/test_local_service_binding.py
```

The explicit test paths support the legacy fixtures' imports without importing
source from another checkout. Native SDK tests use local synthetic workers.

## Live two tool verification at commit 523b959

After the native-batch patch, one bounded live run on the laptop passed:
Qwen3 returned native service_status(unit=nginx.service) and
 disk_usage(filesystem=root) proposals in request one. The application validated
and executed both sequentially via separate official SDK stdio profiles. Request
two produced a conclusion referencing both existing evidence IDs. Coordinator
used three decision turns; actual model requests were two. Diagnosis completed
in 37.252 seconds, both model workers exited zero, SDK/model owners were released
and cleanup_pending=false. Minimum sampled available RAM was 3.484 GiB with
0.1-second sampling; transient dips between samples are not ruled out.

Sources were collector.service_status.systemctl.v1 and
collector.disk_usage.statvfs.v1. This verifies one read-only two-tool question on
the laptop. It is not a reliability benchmark, semantic citation proof, positive
HTTP health check or accepted VM deployment. The original failed live attempt
remains FAIL; its exact rejection subreason was not captured. No third inference
request, automatic retry, service mutation or new model was used in the successful
session. Full runtime output remains local and is not committed.
