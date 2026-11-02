
# MCP Tool Contracts

**Project:** Linux Log Agent  
**Owner:** Thủy  
**SDK:** Official MCP Python SDK 2.3.0  
**Python:** 3.12  
**Transport:** stdio  
**Contract version:** 1.0.0  
**Status:** Draft

---

## 1. Purpose

This document defines the common request/response envelope,
input/output contracts, error handling, evidence format,
and safety constraints for the 12 MCP tools.

All tools must use the Official MCP Python SDK.
The MCP Server must not execute arbitrary shell commands,
use sudo, or perform privileged operations.

## 2. Common Request Envelope

Each tool request contains:

| Field | Type | Required | Description |
|---|---|---|---|
| schema_version | string | Yes | Contract version |
| session_id | string | Yes | Client session identifier |
| trace_id | string | Yes | Request tracing identifier |
| arguments | object | Yes | Tool-specific input arguments |

The MCP Client creates one logical tool call ID for the whole request.
Each retry uses a distinct attempt ID linked to the same logical call ID.
The SDK manages JSON-RPC request IDs.

## 3. Common Response Envelope

Every tool returns a normalized result with these fields:

| Field | Type | Description |
|---|---|---|
| schema_version | string | Contract version |
| trace_id | string | Request tracing identifier |
| logical_tool_call_id | string | Logical call identifier from Client |
| attempt_id | string | Identifier for this attempt |
| tool | string | Tool name |
| status | string | Execution status |
| started_at | string | RFC3339 UTC start time |
| completed_at | string | RFC3339 UTC completion time |
| unit | string/null | Affected service unit, if applicable |
| data | object | Tool-specific result |
| evidence | array | Supporting evidence |
| command_source | object/null | Collector provenance |
| error_code | string/null | Normalized error code |
| truncated | boolean | Whether output was truncated |
| next_cursor | string/null | Pagination cursor |
| duration_ms | integer | Execution duration in milliseconds |

### 3.1 Status values

- `ok`: Tool completed successfully.
- `partial`: Tool returned incomplete or truncated data.
- `error`: Tool execution failed.
- `approval_required`: An operation requires external approval.

### 3.2 MCP result mapping

| Contract status | MCP isError | Meaning |
|---|---|---|
| ok | false | Successful result |
| partial | false | Partial result; do not treat as complete |
| error | true | Tool execution error |
| approval_required | false | Approval is pending; operation was not executed |

The structured result preserves the contract status.
The content field contains a filtered summary for the model.

Unknown tools and invalid protocol requests are handled by the MCP SDK.

## 4. Timestamp Rules

All timestamps use RFC3339 UTC format.

- `started_at`: time when tool execution begins.
- `completed_at`: time when tool execution ends.
- `evidence.observed_at`: time when the underlying event was recorded.

Time-range queries use the half-open interval `[since, until)`.

## 5. Evidence Object

Each evidence item contains:

| Field | Type | Description |
|---|---|---|
| evidence_id | string | Unique evidence identifier |
| observed_at | string | Event observation time |
| source | object | Source identifier and location |
| unit | string/null | Related service unit |
| excerpt | string | Filtered evidence excerpt |
| redacted | boolean | Whether sensitive data was redacted |
| truncated | boolean | Whether the excerpt was shortened |

Evidence must identify its source.
Sensitive information must be redacted before returning results.

## 6. Error Codes

The common error code set is:

- `INVALID_INPUT`
- `PERMISSION_DENIED`
- `TIMEOUT`
- `OUTPUT_LIMIT`
- `NOT_FOUND`
- `UNSUPPORTED`
- `POLICY_DENIED`
- `APPROVAL_REQUIRED`
- `EXPIRED`
- `REPLAY`
- `HELPER_UNAVAILABLE`
- `EXECUTION_UNKNOWN`

An empty result is not the same as permission denied.
A partial or truncated result must never be presented as complete.

## 7. Initial Resource Limits

These are initial limits and must be validated during implementation:

| Limit | Initial value |
|---|---|
| Read-tool timeout | 10 seconds |
| Maximum response size | 64 KiB |
| Maximum events per call | 200 |
| Default query window | 24 hours |

The 64 KiB limit applies to the serialized contract response.
Collectors must also bound the amount of data they read.

Pagination cursors must be bound to:
- Tool name
- Normalized query filters
- Client session
- Expiration time

Expired or mismatched cursors must be rejected.

## 8. Tool Registry

Exactly 12 tools are included in this contract.

| No. | Tool | Access | Owner | Purpose |
|---:|---|---|---|---|
| 1 | query_journal | R | Thủy | Query journal events |
| 2 | search_log | R | Thủy | Search approved log sources |
| 3 | service_status | R | Thủy | Inspect service status |
| 4 | list_failed_units | R | Thủy | List failed systemd units |
| 5 | disk_usage | R | Thủy | Inspect disk and inode usage |
| 6 | memory_oom_events | R | Trang | Find OOM events |
| 7 | failed_logins | R | Trang | Inspect failed login events |
| 8 | listening_ports | R | Thủy | Inspect listening ports |
| 9 | check_config | R | Thủy | Validate supported configurations |
| 10 | detect_anomaly | R | Hiền | Detect log anomalies |
| 11 | verify_fix | R | Hiền | Verify whether an issue was fixed |
| 12 | restart_service | W facade | Trang | Create or read restart proposals |

`R` means read-only.
`W facade` means proposal/status operations only, not actual execution.

## 9. Security Constraints

- No arbitrary shell commands.
- No arbitrary executable paths.
- No sudo or privilege escalation.
- No direct systemctl restart from MCP.
- No access to privileged helper control sockets.
- No secrets or approval tokens exposed to the Agent.
- Service unit names must be canonical and allowlisted.
- Input schemas must reject unknown fields using `additionalProperties: false`.
- All collectors must use fixed, reviewed commands or approved APIs.

### 9.1 restart_service safety

The MCP tool may only:

- Create a restart proposal.
- Read proposal status.

It must not:

- Restart or stop a service.
- Execute systemctl.
- Invoke subprocess or a privileged helper.
- Accept `approved=true` or an approval token as authorization.

The mock implementation must never perform a real restart.

## 10. Tool-Specific Contracts

Detailed input schemas, output schemas, success examples,
and error examples for all 12 tools will be specified
in the following sections.

Each tool contract must define:
- Input fields and validation rules.
- Output data structure.
- Evidence requirements.
- Error conditions and error codes.
- Timeout and output limits.
- Pagination behavior, where applicable.

## 11. Validation Checklist

- [ ] All 12 tools have input schemas.
- [ ] All 12 tools have output schemas.
- [ ] Success examples validate against their schemas.
- [ ] Error examples validate against their schemas.
- [ ] Unknown input fields are rejected.
- [ ] Output size limit is tested.
- [ ] Pagination cursor binding is tested.
- [ ] `detect_anomaly` and `verify_fix` are confirmed by Hiền.
- [ ] Linux tool contracts are confirmed by Trang.
- [ ] `restart_service` mock never performs a real restart.
- [ ] MCP stdio discovery and tool calls pass integration tests.
