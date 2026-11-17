# SCRUM-24 — Branch preparation review, 2026-11-13

**READY FOR DRAFT PR — dependent synthetic foundation only.** Không Ready for Merge,
không tuyên bố SCRUM-24 Done, không xác nhận production MCP/LLM integration.

## Git và phạm vi

Đã xác minh GitHub CLI tài khoản hienne215 và fetch origin. Main hiện tại:
`990f41f68c8ef13218bf825bd57ff233a8c82f1c`.

Worktree mới: `linux-log-agent-scrum24`, branch `feat/SCRUM-24-agent-loop`,
HEAD/base `9c7fbc14e5526305e69ae4ca835153872211de28` của Draft PR #7.
Đây là stacked dependency chưa merge; không phải main. Base này chưa chứa squash
Adapter/benchmark trên main. Regression runner đọc Adapter từ origin/main ở vùng
composition tạm; coordinator không import Adapter từ worktree khác.

Ba worktrees cũ giữ nguyên HEAD, status và nội dung untracked; kiểm tra SHA-256
cả 54 file untracked ở repo chính, không có thay đổi. SCRUM-22 và SCRUM-23 sạch.
Không stage/commit/push/PR/Jira update. Không thay đổi PR #5/#6/#7.

## Review và sửa tối thiểu

| Phân loại lúc phát hiện | Phát hiện | Sửa và bằng chứng |
|---|---|---|
| NEEDS CHANGE → PASS | Safe result đến sau cleanup timeout bị consume nhưng không đưa lại vào report | Dùng chung `_safe_observation` cho normal/late result; rejoin cập nhật snapshot, không gọi model tiếp |
| NEEDS CHANGE → PASS | Late evidence mất nếu bước xác nhận executor cleanup lỗi | Validate/sanitize và giữ evidence trước cleanup confirmation; lỗi vẫn báo cleanup pending; rejoin sau giữ nguyên evidence |
| NEEDS CHANGE → PASS | Renderer chấp nhận concluded claim không có evidence/citation | Fail closed INVALID_CITATION, kể cả caller tự gắn conclusion_verified |
| NEEDS CHANGE → PASS | Source nhiều dòng có thể thoát vùng quoted data | Source và excerpt đều được escape và quote từng dòng, heading do ứng dụng tạo |

Đã tái hiện regression trước sửa: ba lỗi đầu review ban đầu gồm 2 FAIL/1 ERROR;
cleanup-confirmation regression bổ sung có 1 FAIL. Sau sửa thêm tổng cộng **5 tests**:
late safe result, late redaction fail-closed, late cleanup confirmation failure,
concluded-without-evidence, multiline source isolation.

Không đổi Client/Bridge/Adapter, contracts, fixture expected outputs hoặc approval.
Không tự retry write/read, reconnect, restart hay cấp approval. Read-only registry,
schema/scope checks và lifecycle owner vẫn thuộc trusted application/Bridge/Client.
Report không coi timeout là chưa thực thi, không biến tool output thành system role.

## Kiểm thử mới trong lượt này

Runtime: **Python 3.12.16**, MCP **2.3.0**, jsonschema **4.26.0**,
referencing **0.37.0**. Không tải/cài thêm dependency trong lượt này.

| Suite | PASS | FAIL | SKIP |
|---|---:|---:|---:|
| Fixture validation | 4 | 0 | 0 |
| Coordinator synthetic / in-memory Client + Bridge | 39 | 0 | 0 |
| Coordinator isolated units | 7 | 0 | 0 |
| Incident Report unit + synthetic report-flow | 14 | 0 | 0 |
| **SCRUM-24 total** | **64** | **0** | **0** |
| Adapter composed regression | 57 | 0 | 0 |
| MCP Client composed (real stdio enabled) | 46 | 0 | 0 |
| Boundary composed (real stdio enabled) | 50 | 0 | 0 |
| **Composed regression total** | **153** | **0** | **0** |

SCRUM-24 runner chạy từ /tmp bằng isolated mode sau khi chuẩn hóa package; source,
fixtures và imports chỉ lấy từ worktree mới. Composed baseline là main SHA ở trên.
Real SDK tests chỉ dùng synthetic stdio server, bao gồm cancellation/disconnect/
timeout/cleanup. Sau suite, scan process không có mock_stdio_server.py đang chạy.
Điều này là bằng chứng synthetic, không là SLA hard wall hoặc production cleanup.

Reproduce từ repository với venv/dependencies đã chuẩn bị:

```bash
python -B -I tests/agent_loop/run_tests.py
RUN_MCP_STDIO_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 python tests/integration_boundary/run_composed_tests.py --baseline-ref origin/main
```

Không inference, Linux diagnostics, sudo, restart, fault injection hoặc đổi Ollama.

## Files tiếp nhận (16, tất cả chưa stage)

- `src/agent/{__init__,coordinator,incident_report}.py`
- `pyproject.toml`
- `docs/{SCRUM-24_MINIMAL_LOOP_DESIGN,SCRUM-24_AGENT_LOOP,SCRUM-24_BRANCH_REVIEW}.md`
- `tests/agent_acceptance/{README.md,scenarios.json,test_scenarios.py}`
- `tests/agent_loop/{README.md,run_tests.py,test_candidate.py,test_review_units.py,test_incident_report.py}`
- `examples/INCIDENT_SYNTHETIC_BUDGET.md`

Package init trùng byte-for-byte với main đã merge; không copy implementation Adapter.
Bốn handoff files giữ nguyên SHA-256. Không copy venv/audit dump/snapshot/raw log.
Example dùng synthetic data. Các legacy design/fixture docs được giữ như lịch sử;
`SCRUM-24_AGENT_LOOP.md` và report này mô tả trạng thái implementation hiện hành.

## Compatibility / dependencies / Jira

- PASS: application-owned states; caps 8 tool attempts / 6 turns / 120s; lower Bridge
  budget remains binding; owned cleanup task and explicit pending state; bounded
  sanitized evidence and resolvable citations; safe Markdown renderer.
- BLOCKED production: PR #6 head `380a0302a9a4c7b3badd857dd37b5d2c42cfd3d7` chưa finalized.
  Chờ Thủy chốt schemas, IDs/correlation, CallToolResult/business status, cursor,
  timestamps/provenance và exact output-limit semantics. Không tự phát minh metadata.
- BLOCKED merge/main integration: PR #7 vẫn Draft/open. Cần review/merge dependency
  và kế hoạch cập nhật base được Hiền cho phép, không đưa pending code vào main ngầm.
- FOLLOW-UP: async production LLM binding, semantic claim checking, reviewed real-log
  redactor, authoritative evidence store, runtime/VM/permissions và RAM với Trang.
  Scripted model và sentinel redactor không chứng minh live model/log safety.
- FOLLOW-UP: no hard wall deadline; cooperative cancellation required; caller phải giữ
  Coordinator và gọi finish_cleanup khi pending, rồi đọc lại coordinator.report.

Jira SCRUM-24 đọc trong lượt này: **To Do**, phụ thuộc P02-02/SCRUM-23. Ba scenarios
active/failed/permission-denied và controlled termination/no autoapprove được kiểm
synthetic; chưa có actual LLM-driven multi-step Linux diagnosis để nghiệm thu toàn bộ.
Thủy bàn giao contract/binder decisions; Trang xác nhận VM/read permissions/security;
Hiền review nhánh, sau đó cho phép commit/push Draft foundation nếu phù hợp.
