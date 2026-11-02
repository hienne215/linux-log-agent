# Documentation Matrix

## 1. Purpose

This matrix defines where project documents are stored, who owns them, and how their status is reported.
It also tracks the status of project features, so planned work is never presented as implemented.

## 2. Status Definitions

| Status | Meaning |
|---|---|
| Existing | Already in the repository before this workflow |
| Proposed | Suggested, waiting for team review |
| Planned | Agreed in the plan, not started |
| In Progress | Being worked on |
| Implemented | Completed and verified by a reviewer who is not the author |

## 3. Document Matrix

| Document | Location | Owner | Status |
|---|---|---|---|
| Project overview | `README.md` | Team | Existing |
| Repository structure and Git workflow | `docs/project-structure.md` | A | Proposed |
| Documentation matrix | `docs/documentation-matrix.md` | A | Proposed |
| Pull Request template | `.github/pull_request_template.md` | A | Proposed |
| Project outline (agreed scope) | `docs/proposal-v2.md` | Team | Planned |
| MCP tool contracts (schemas) | `docs/mcp-tool-contracts.md` | A and B | Planned |
| System architecture | `docs/architecture.md` | Team | Planned |
| Security design | `docs/security-design.md` | C | Planned |
| Evaluation scenarios and answer keys | `docs/evaluation-scenarios.md` | C | Planned |
| Setup and install guide | `docs/setup-guide.md` | C | Planned |
| Test report | `docs/test-report.md` | C | Planned |
| Final report (English) | `docs/report/` | Team | Planned |
| Slides (English) | `docs/report/` | Team | Planned |

## 4. Feature Status

Scope follows the agreed outline: 12 core tools, 5 required scenarios.
"Optional" items are built only if time remains.

### MCP tools

| Feature | Type | Owner | Status |
|---|---|---|---|
| `query_journal` | Core (read) | A | Planned |
| `search_log` | Core (read) | A | Planned |
| `service_status` | Core (read) | A | Planned |
| `list_failed_units` | Core (read) | A | Planned |
| `disk_usage` | Core (read) | A | Planned |
| `memory_oom_events` | Core (read) | A | Planned |
| `failed_logins` | Core (read) | A | Planned |
| `listening_ports` | Core (read) | A | Planned |
| `check_config` | Core (read) | A | Planned |
| `detect_anomaly` | Core (read) | A | Planned |
| `verify_fix` | Core (read) | A | Planned |
| `restart_service` (needs approval) | Core (write) | A and C | Planned |
| `build_timeline` | Optional (read) | A | Planned |
| `check_path_access` | Optional (read) | A | Planned |
| `rollback_change` | Optional (write) | A and C | Planned |

### System components

| Feature | Type | Owner | Status |
|---|---|---|---|
| MCP server | Core | A | Planned |
| MCP client and agent loop | Core | B | Planned |
| CLI | Core | B | Planned |
| Markdown incident report | Core | B | Planned |
| Privileged helper and approval flow | Core | C | Planned |
| Audit log | Core | C | Planned |
| Test VM and snapshot | Core | C | Planned |
| Evaluation runs (5 scenarios, 3 runs each) | Core | C | Planned |
| Unit tests | Core | A | Planned |
| Incident history (SQLite) | Optional | B | Planned |
| Watch mode (systemd timer) | Optional | B and C | Planned |
| Web dashboard | Optional | B | Planned |
| Second LLM comparison | Optional | C | Planned |

## 5. Documentation Rules

1. Keep project documents in the `docs/` directory.
2. Keep the original README content.
3. Do not report planned or proposed work as implemented.
4. Update this matrix in the same PR that changes a status.
5. A document or feature becomes Implemented only after review by someone other than its author.

## 6. Source of Truth

- Jira: task status, owner, priority and progress.
- GitHub: source code, documents, branches and Pull Requests.
- `docs/proposal-v2.md`: agreed scope, architecture and deliverables.
