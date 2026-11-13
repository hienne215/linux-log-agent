# SCRUM-27 — implementation review, 2026-11-13

**READY FOR DRAFT PR — synthetic MVP only.** Chưa commit/push/PR/Jira update.

## Git trước/sau và dependency

GitHub account hienne215 đã xác minh, origin đã fetch. PR #8 còn Open/Draft,
head `61cdff0d2674f14150a1e7df5940a1372b8c1d24`; PR #7 Open/Draft, head
`9c7fbc14e5526305e69ae4ca835153872211de28`. Tạo branch/worktree riêng
`feat/SCRUM-27-cli-report` / `linux-log-agent-scrum27` từ đúng PR #8 head.
Không coi đây là branch độc lập từ main; không sửa PR #6/#7/#8.

Bốn worktrees ban đầu giữ nguyên HEAD/status và hashes file untracked; repo chính
vẫn có 54 untracked. Worktree mới có thay đổi SCRUM-27 chưa stage/commit.
Không reset/stash/rebase, không đổi nhánh cũ. File reports/ phát sinh là synthetic,
được ignore, không thuộc source bàn giao.

## Đã đọc/đối chiếu

ARCHITECTURE.md và SECURITY.md (SCRUM-11), coordinator/renderer và tests/README/report
của PR #8; trusted Boundary/Client interface của PR #7; CLI_REPORT_SPEC.md bản local
chưa commit ở repo chính. Không có CLI code sẵn; renderer tái sử dụng, không viết
trùng. Các design docs lịch sử không tự cấp quyền triển khai production.

Jira SCRUM-27 đọc qua connector: To Do, P02-06, owner Hiền, reviewer Trang,
dependency P02-03/SCRUM-24. AC: không lộ secrets; kết luận có evidence hoặc ghi thiếu
dữ liệu; đọc được trace. Deliverables CLI/report/tests, LLM stub và output snapshots.
Chỉ phần synthetic được chứng minh; dependency P02-03 chưa nghiệm thu hoàn chỉnh.

## Files mới/sửa

- Mới `src/agent/cli/__init__.py`: package không startup side effects.
- Mới `src/agent/cli/__main__.py`: argparse, stdin/question, orchestration/cleanup,
  Vietnamese status/trace, reused renderer, exit policy.
- Mới `src/agent/cli/synthetic.py`: ba scenario nội bộ, scripted choices,
  schema/read-only validation, conservative demo redactor. Không MCP wire schema.
- Mới `src/agent/cli/report_store.py`: bounded FD-based safe no-overwrite publisher.
- Sửa `pyproject.toml`: thêm entrypoint `lla-demo`, không framework/dependency mới.
- Mới `.gitignore`: local reports/venv/Python build caches.
- Mới `tests/cli/test_cli.py`, `tests/cli/README.md`, ba files snapshots/*.txt.
- Mới hai tài liệu `docs/SCRUM-27_CLI_DEMO.md` và report này.

Không sửa coordinator, renderer, MCP Client/Bridge/Adapter hoặc code Thủy/Trang.

## Review và các điều chỉnh trong triển khai

- PASS: question không điều khiển tools/quyền, không echo/log question; write-keyword
  rejection chỉ là UI, enforcement nằm ở allowlist/schema/executor không system I/O.
- PASS: coordinator state machine/budgets được tái sử dụng, không tạo reasoning loop.
  Cancellation được propagate vào owned dispatch rồi join; no retry/autoapproval.
- PASS: report/terminal dùng renderer hiện có, evidence/citations/uncertainty và data
  escaping; trace có strict allowlist/size cap để không bypass renderer bằng raw IDs.
- PASS: partial permission-denied report không tạo journal evidence giả và không kết
  luận root cause. Active không khẳng định HTTP healthy.
- PASS: report writer reject traversal/symlink/group-writable root, private 0600,
  atomic exclusive publish; temp collision không được xóa file của người khác.
- PASS: argparse errors không echo bad arguments; fixed safe exceptions, không raw
  exceptions hoặc credentials. Demo redactor che cả field ở những dạng đã kiểm thử.
- FOLLOW-UP: real-log redactor/semantic evidence checks, real CLI IPC/UID separation,
  backend contracts/binding và live read-only diagnosis; không mở rộng trong task này.
- FOLLOW-UP: wheel build/install chưa chạy mới trong lượt này; module invocation,
  imports, entrypoint declaration và source-based commands đã được kiểm tra.

## Kiểm thử thực tế mới

Python **3.12.16**, MCP SDK **2.3.0** trong prepared runtime; không cài package hệ
thống hoặc tải model. Không suy diễn từ các kết quả cũ.

| Suite | PASS | FAIL | SKIP |
|---|---:|---:|---:|
| SCRUM-27 CLI | 35 | 0 | 0 |
| SCRUM-24 fixture/coordinator/renderer | 64 | 0 | 0 |
| Adapter composed | 57 | 0 | 0 |
| MCP Client composed | 46 | 0 | 0 |
| Boundary composed | 50 | 0 | 0 |
| **Tổng** | **252** | **0** | **0** |

Composed baseline `990f41f68c8ef13218bf825bd57ff233a8c82f1c` từ origin/main;
SDK stdio dùng synthetic server. Sau kiểm thử không có orphan mock server process.
No live inference/Linux diagnostic/sudo/restart/fault injection/Ollama configuration.

35 tests CLI gồm ba scenarios/snapshots, input/args/path invalid, thiếu evidence,
permission denied, error/disconnect/timeout/budget, cancellation thật, schema và
model/user write rejection, trace injection, secret variants/JSON/escaped JSON,
no-overwrite/symlink/ownership mode/temp cleanup và ba terminal module executions.

Ba lệnh demo đã thực sự chạy từ repository và lưu reports/nginx-*-demo.md:
active exit 0 (1 attempt), port conflict exit 0 (3 attempts), permission denied exit
1 (2 attempts). File chứa SYNTHETIC DEMO, bounded safe evidence/trace/uncertainty;
report permission denied vẫn được lưu thành công. Output đầy đủ ở snapshots và
trích đoạn ở CLI_DEMO. Report names có sẵn bị từ chối, không overwrite.

## Gate còn lại

Hiền review synthetic MVP trước commit/push. Thủy chốt PR #6/contracts; PR #7/#8
cần nghiệm thu theo quy trình nhóm. Trang review report path/permissions/data policy
và VM. Production CLI không được dùng same-process synthetic binding như thiết kế
triển khai thật; cần IPC/UID isolation đã duyệt, async Adapter/MCP binding và thực
nghiệm read-only trước khi xem SCRUM-27 đủ AC tích hợp. Jira giữ nguyên To Do.
