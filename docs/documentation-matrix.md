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
| MCP tool contracts (schemas) | `docs/mcp-tool-contracts.md` | Thủy; Hiền/Trang review | Existing (PR #6 merged; scoped reviews) |
| SCRUM-12 final contract review and collector handoff | `docs/SCRUM-12-contract-review-2026-11-14.md` | Thủy | Proposed |
| P01-01 draft scaffold design and evidence | `docs/P01-01-server-scaffold.md` | Thủy; Hiền review | In Progress |
| System architecture | `docs/architecture.md` | Team | Planned |
| Security design | `docs/security-design.md` | C | Planned |
| Evaluation scenarios and answer keys | `docs/evaluation-scenarios.md` | C | Planned |
| Setup and install guide | `docs/setup-guide.md` | C | Proposed |
| Test report | `docs/test-report.md` | C | Planned |
| P03-03 failed-logins design, fixtures and validation | `docs/P03-03-failed-logins.md` | Trang; Thủy review | In Progress |
| Final report (English) | `docs/report/` | Team | Planned |
| Slides (English) | `docs/report/` | Team | Planned |

## 4. Feature Status

Scope follows the agreed outline: 12 core tools, 5 required scenarios.
"Optional" items are built only if time remains.

### MCP tools

The tool statuses below refer to real Linux collection/execution, not the existing 12-tool mock/fixtures. PR #6 provides official SDK stdio contracts and a mock server; PR #6 has been merged with scoped schema/integration confirmations; this is not real Linux acceptance. In particular, the read-only Linux `service_status` collector has not been implemented in PR #6.

| Feature | Type | Owner | Status |
|---|---|---|---|
| `query_journal` | Core (read) | A | Planned |
| `search_log` | Core (read) | A | Planned |
| `service_status` | Core (read) | A | Planned |
| `list_failed_units` | Core (read) | A | Planned |
| `disk_usage` | Core (read) | A | Planned |
| `memory_oom_events` | Core (read) | A | Planned |
| `failed_logins` | Core (read) | Trang; Thủy review | In Progress |
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
| MCP server (real Linux collectors) | Core | A | Planned |
| MCP server scaffold (P01-01 draft; collectors unavailable) | Core | Thủy | In Progress |
| MCP mock server and 12-tool contracts (PR #6) | Core | Thủy | Existing (merged; not real Linux acceptance) |
| MCP client and agent loop | Core | B | Planned |
| CLI | Core | B | Planned |
| Markdown incident report | Core | B | Planned |
| Privileged helper and approval flow | Core | C | Planned |
| Audit log | Core | C | Planned |
| Test VM and snapshot | Core | C | In Progress |
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

## P00-03 review revision R2

[R2 review response and ADR](SCRUM-12-review-R2-2026-11-15.md): schema/runtime changes pending independent Hiền/Trang confirmation; NOT finalized. Hiền accepted Integration R1 only at d442479/917d9f0. Trang review requests fixes despite GitHub APPROVED state. No merge authorization.

## P00-03 review revision R3

[R3 review response](SCRUM-12-review-R3-2026-11-15.md) addresses Trang's second review. Pending independent confirmation; NOT finalized. Trang's proposed signoff scope is security/privacy of three tools, not all nine remaining tools or the complete transport/deployment.

## P01-01 R3 integration — 16/11/2026

[Scaffold compatibility and P03-02 branch guidance](P01-01-R3-integration-2026-11-16.md): merges R3 without rewriting downstream history; fixes error cap/schema behavior and accepted coverage correlation. Draft, pending independent acceptance; no real collector or VM smoke claimed.

## Dependency audit — 16/11/2026

[PR #6/#14 review readiness](dependency-review-readiness.md): scoped confirmations recorded; current integration, detailed schemas/transport and independent scaffold review remain pending. Not finalized or Done.

## Squash dependency adoption — 17/11/2026

PR #6 canonical 1.0.0/schema-hardening content was squash merged at `f9adf8f`.
The R2/R3/dependency entries above retain their historical review snapshots; see
[the current scoped review record](dependency-review-readiness.md). PR #14 remains
In Progress pending fresh integrated-HEAD tests and independent review. The
scaffold still has no default real collector; Agent/CLI work from main is retained.
No Jira status, production readiness or real Linux acceptance is asserted here.

## P01-03 service_status collector

[Read-only service_status handoff](P01-03-service-status.md): separate opt-in Linux backend, originally based on scaffold 656f8c3; local adoption now uses pinned main f2a8f71 with merged #6/#14/#13 dependencies. Local injected-API/SDK tests are not Linux smoke or independent acceptance; no other collector is included.

Dependency update: PR #14 was squash merged at `a0206a1`. Earlier pending/in-progress entries retain their historical scope; this collector integration is still a local review candidate, not production or Linux acceptance.
