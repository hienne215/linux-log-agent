# SCRUM-22 — Integration-boundary preparation report

Ngày: **06/11/2026**, Asia/Ho_Chi_Minh. Owner: Hiền. Phạm vi độc lập với contracts
SCRUM-12 chưa finalized. **Không tuyên bố MCP integration hoặc SCRUM-22 hoàn tất.**

**Cập nhật sau review R1/R2: READY FOR DRAFT PR UPDATE trong phạm vi boundary.**
Các bảng baseline bên dưới ghi lần triển khai ban đầu; kết quả sửa lỗi và kiểm thử
mới nhất ở mục cuối: **153 PASS**, chưa stage/commit/push hoặc cập nhật PR/Jira.

## Git trước/sau

GitHub CLI: tài khoản `hienne215`, xác thực thành công. Đã fetch origin, không đổi
branch/checkout/stash/reset/rebase/merge/commit/push hoặc trạng thái PR/Jira.

| Worktree | Nhánh / HEAD trước và sau | Trạng thái |
|---|---|---|
| Repo chính | bench/SCRUM-14-llm-smoke / b2e6ac87210a52d3a650b0df52db13213fa4819f | Tracked/staged sạch; 53 untracked cũ giữ nguyên SHA256 |
| SCRUM-22 | feat/SCRUM-22-mcp-client / 688534e2bb2a887dc01c2bd0f416a881d2c082c1 | Trước sạch; sau chỉ có 10 file mới thuộc boundary, chưa stage |
| SCRUM-23 | feat/SCRUM-23-llm-adapter / 8f4c146b70c75345324eb1f51a0d059afbcb61ba | Sạch, không đổi |

Local `main` còn ở `7d3e21f012ae38324ab01dab4c33efe1138ba05e`;
`origin/main` ở `990f41f68c8ef13218bf825bd57ff233a8c82f1c` (Adapter PR #4).
Nhánh SCRUM-22 có hai feature commits; origin/main có hai squash commits mới so
với base chung. Không tự merge/rebase/update branch vì yêu cầu giữ nguyên nhánh.

- [PR #6](https://github.com/hienne215/linux-log-agent/pull/6): Open, không Draft về
  trạng thái GitHub, head `1ac477e5f98b2f4e1065f26f5a060c5f5afe6bfa`; nội dung contracts
  vẫn **Draft**, unchanged từ review04/11. Base main.
- [PR #7](https://github.com/hienne215/linux-log-agent/pull/7): Open **Draft**, head
  `688534e2bb2a887dc01c2bd0f416a881d2c082c1`, base main. Không cập nhật PR.

Để test với Adapter đã merge, runner đọc exact Git archive main và ghép source
SCRUM-22 vào TemporaryDirectory. Không copy Adapter vào feature branch, không sửa
worktree khác và không tạo commit tích hợp giả.

## Tài liệu/source đã đối chiếu

- `docs/ARCHITECTURE.md`: ADR01–06, stdio/read-plane, registry, evidence, loop budget,
  coordinator/helper separation và interfaces chờ SCRUM-12.
- `docs/SECURITY.md`: trust boundaries, input validation, redaction, no sudo/approval
  qua Agent, timeout/output limits và giới hạn deployment.
- `src/agent/mcp_client/{__init__,client,sdk}.py`; toàn bộ ba test modules cùng
  `mock_stdio_server.py`, README và requirements của MCP Client.
- Bản đã merge trên main: `src/agent/llm/{__init__,interface,ollama,redaction}.py`,
  README, `tests/llm/test_ollama.py`, `test_redaction.py`; source được dùng trực tiếp
  trong composed test tree. Không thay các file này.
- PR #6 `docs/mcp-tool-contracts.md` (read-only), nhất là envelope/IDs/result mapping,
  §10 contracts chưa điền và checklist chưa hoàn tất. Không dùng schemas Draft làm
  trusted application registry, không chạy server của Thủy trong lượt này.
- Kết quả review PR5/6 ngày04/11 và giới hạn redaction đã ghi nhận sau PR4.

## Phần đã triển khai — PASS trong phạm vi độc lập

10 file mới, không chỉnh tracked files có sẵn:

1. `src/agent/integration_boundary/__init__.py`
2. `src/agent/integration_boundary/bridge.py`
3. `tests/integration_boundary/fixtures.py`
4. `tests/integration_boundary/test_bridge.py`
5. `tests/integration_boundary/test_adapter_optional.py`
6. `tests/integration_boundary/test_stdio_optional.py`
7. `tests/integration_boundary/run_composed_tests.py`
8. `tests/integration_boundary/requirements.txt`
9. `tests/integration_boundary/README.md`
10. `tests/integration_boundary/REPORT.md`

- Trusted registry snapshot, Draft2020-12 validation, local refs, no external schema
  retrieval, independent read-only policy, discovery/allowlist vẫn do MCP Client.
- Một proposal→một dispatch; bounded call/time budgets; không retry/reconnect/approve.
- Generic tool-role JSON message giữ text + structured data + isError; không parse
  text thành business envelope, không diễn giải partial như success hoàn chỉnh.
- Injected redactor bắt buộc, fail closed; secret arguments bị reject thay vì sửa.
- Error layer policy/transport/protocol; tool isError là result. Cancellation
  truyền về caller, dùng cleanup có sẵn của MCP Client. Không sửa SDK binding.
- Fixtures thuộc tests của Hiền, không phải schema12tools hoặc contracts chính thức.

## Interface compatibility matrix

| Interface | Kết quả | Bằng chứng / việc còn lại |
|---|---|---|
| Native ToolCall.name/arguments → ToolBridge | PASS | Mock HTTP Adapter → proposal → independent schema validation → one MCP call |
| TrustedTool → Adapter ToolDefinition | PASS cho schema tự chứa/local refs | Tests dùng registry do ứng dụng cấp; không tin discovery làm quyền |
| Invalid/unknown tool/arguments | PASS | Không dispatch; JSON-string args không được coercion; enum/type/required/extra fields theo schema |
| Read/write separation | PASS trong boundary | Write flag từ config tin cậy bị từ chối; chỉ read-only definitions ra Adapter; approved extra field bị schema từ chối |
| MCP ToolResult → Message(role=tool) | PASS generic | Preserve content/structuredContent/isError; không system instructions hoặc auto-fetch resources |
| Tool error / partial | PASS generic, BLOCKED business interpretation | isError=true là result; partial/truncated/cursor opaque vẫn giữ; chưa validate project envelope |
| Transport / protocol errors | PASS | TIMEOUT/DISCONNECTED/PROTOCOL_ERROR khác layers; không lộ raw exceptions; không retry |
| Timeout/cancellation/disconnect/cleanup | PASS synthetic cooperative handlers | Offline regression + SDK2.3 thật, marker xác nhận server handler đã nhận cancellation, process returncodes |
| Redaction before HTTP | PASS fixtures; FOLLOW-UP log thật | Dùng filter hiện có của Adapter, secret không trong mocked HTTP request; không chứng minh detector bao phủ mọi loại secret |
| Evidence/provenance | PASS bảo toàn dữ liệu mock; BLOCKED semantic evidence | Structured IDs/source giữ sau mapping/redaction; chưa authoritative evidence store/citation verification |
| Logical/attempt/session/trace IDs | BLOCKED wire binding | Thiết kế client-side sở hữu IDs; chờ vị trí metadata và echo contract. Không tự tạo fields/IDs thay SCRUM-12 |
| Raw PR6 schemas/discovery/schema version | NEEDS CHANGE trước tích hợp | Thủy cần registry finalized, schemas đủ/bundled nhất quán với server validation; chưa coi PR6 head hiện tại finalized |
| Output bytes/cursor/time mapping | BLOCKED chính thức | Boundary cap JSON nội bộ sau SDK parse; chờ wire policy, collector cap, cursor binding và timestamps/provenance |
| Sync LLM Adapter cancellation/total request SLA | FOLLOW-UP | Tool budget cooperative; chưa có hard wall deadline và không có async HTTP inference cancellation implementation |
| End-to-end đọc Linux thật | BLOCKED | Không chạy inference/MCP production/Linux tools, chưa finalized12contracts/VM permissions |

Logical call ID là client-side identity của một yêu cầu tool, attempt ID thay theo
lần gửi; future Coordinator/client contract binder của Hiền quản lý, SDK chỉ giữ
JSON-RPC ID. Quyền sở hữu này không được giao cho Ollama provider_id hoặc server
tự đổi. Chỗ gắn wire fields cần thống nhất với Thủy trước khi code.

## Kết quả kiểm thử baseline trước review R1/R2

Môi trường đã có, không cài thêm: CPython **3.12.16**, `mcp==2.3.0`,
`jsonschema==4.26.0`, `referencing==0.37.0` trong venv SCRUM-22.

| Suite | Offline mặc định trong composed tree | Lượt cuối bật synthetic stdio |
|---|---|---|
| Adapter đã merge | 57 PASS,0skip | 57 PASS |
| MCP Client hiện có | 42 PASS,4optional SKIP | 46 PASS =42offline+4SDK |
| Boundary mới | 35 PASS,4optional SKIP | 39 PASS =31unit+4Adapter/MCP mocks+4SDK |
| Tổng | **134 PASS,8optional SKIP** | **142 PASS,0FAIL,0ERROR,0SKIP** |

Trong worktree nguyên bản chưa có package Adapter, bốn Adapter-boundary tests skip
rõ ràng; runner ghép main giải quyết việc này mà không thay source branch. Không
tính skip là PASS. Kết quả bảng là từ composed tree đúng commit main990f41f.

Lượt kiểm chứng cuối:

```bash
RUN_MCP_STDIO_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 .venv-scrum22-py312/bin/python tests/integration_boundary/run_composed_tests.py --baseline-ref origin/main
```

Runner in exact commit main; ba suites lần lượt báo `Ran57 OK`, `Ran46 OK`,
`Ran39 OK`. SDK stdio chạy ngoài sandbox của phiên vì hạn chế IPC trước đây; không
thay sandbox config/VM/system services. Có asyncio slow-task diagnostics lúc import
SDK/startup (~0.7s); không timeout/failure hay payload nhạy cảm trong output.

Các tình huống được kiểm:

- Valid tool và arguments; unknown name; sai JSON schema; JSON-string/nonfinite/
  nonstring key/extra fields; registry bị caller mutate; local/external/broken refs.
- isError=true, partial + truncated + cursor, opaque text, provenance IDs/source;
  chỉ role tool, không tự diễn giải instruction hoặc auto-fetch resource links.
- Redaction cả content/structuredContent, malformed redactor fail closed, không
  đổi secret arguments, không rơi secret synthetic vào mocked HTTP transport;
  repr/error messages không chứa payload.
- Call limit, expired time budget, BUSY, timeout, disconnect, protocol errors,
  oversized multibyte JSON, no retry/write/approval.
- Mock HTTP roundtrip có **hai explicit Adapter calls**, **một MCP call**; không
  tự loop, không gọi endpoint Ollama.
- SDK thật:4 tests cũ +4 boundary tests; initialize/discovery/echo, handler-start
  rồi cancellation, request timeout, server self-exit(23), process reaping.
  **8/8 tests xác nhận subprocess có returncode; không orphan trong các runs này.**

Không kết luận hủy có thể rollback Linux operation; đây là synthetic asyncio handler
hợp tác. Cleanup có thể vượt request deadline trong giới hạn cleanup của MCP Client.
Không hard wall deadline, không pre-parse frame cap và không kiểm log production.

## NEEDS CHANGE / BLOCKED — chờ Thủy (SCRUM-12)

1. Đủ12 input/output schemas, examples valid/error/partial; registry version và
   server-side validation nhất quán discovery. Chốt trusted business schema dùng
   cho LLM và metadata schema chỉ ứng dụng cấp. Không để model tạo trace/session IDs.
2. Logical/attempt IDs, session binding, retry/correlation và JSON-RPC phân biệt rõ;
   client metadata binder chưa implement để tránh sai wire contract.
3. CallToolResult: structuredContent envelope, filtered content summary, isError
   mapping. Không sửa bridge để âm thầm parse text thay contract thiếu structured.
4. Tool business result validator/interpreter: status/error_code, evidence provenance,
   timestamps event vs observed_at, cursor/session/filter/expiry và output policies.
5. Cách tính64KiB (UTF8 whole result/envelope/overhead) và collector pre-read/frame
   caps. Hiện boundary/client từ chối quá cap sau parse, không thay server enforcement.
6. Hiền cùng Thủy chốt detect_anomaly/verify_fix; Trang chốt Linux tools và restart
   proposal/status. Phần write/approval thật vẫn ngoài boundary này.

## FOLLOW-UP và bước tới demo read-only

1. Hiền review10file mới. Chưa commit/push hay cập nhật PR7. Giữ Draft, không Done.
2. Thủy bàn giao contracts finalized kèm mock fixtures và owner confirmations;
   lúc đó mới code metadata binder/result interpreter/output validation.
3. Khi Hiền cho phép đồng bộ branch, tích hợp main mới theo phương án Git đã review;
   không đưa source copy từ temp composition vào commit.
4. Chạy frozen-contract tests12mock tools + negative cases từ client/boundary,
   đối chiếu schemas trên cả Adapter/Server. Đây vẫn chưa là Linux acceptance.
5. Trang chuẩn bị Python3.12 VM runtime, UID/journal access/NNP và lab endpoint,
   giữ Agent+MCP stdio cùng VM theo kiến trúc; xác nhận RAM an toàn trước inference.
6. Trước log thật: xử lý follow-up detector MYSQL_PWD/URL credentials và accuracy
   Bearer/token_count; thống nhất collector/SDK logging/redaction. Bộ lọc hiện có
   chỉ là secondary defense, injectable callable không tự chứng minh an toàn.
7. Với quyền chạy task tiếp theo: một explicit read-only lượt user→LLM→validated
   tool→MCP Linux read→evidence→LLM answer, có budget/citation/limits rõ ràng. Không
   dùng shell/restart/helper. ≥1 real server tool vẫn là acceptance cần kiểm chứng.

**Đã dừng tại nền tảng boundary để Hiền review; không mở rộng reasoning loop.**

## Sửa R1/R2 đã được Hiền chấp thuận — 06/11/2026

Đã đọc toàn bộ `SCRUM22_BOUNDARY_REVIEW_2026-11-06.md`, đối chiếu bridge, Client,
README và tests. Trước sửa đã tái hiện cả hai lỗi:

- **R1:** Client timeout trước; bridge deadline cancel vào lúc Client đang chờ
  owner cleanup. Bridge trả TIMEOUT khi owner còn chạy và peer chưa đóng. Owner
  không mất ngay nhưng caller không còn một giao thức bridge để quản lý việc chờ.
- **R2:** Sau nhận response và sanitize thành công, kiểm total deadline cuối hàm
  ném BUDGET_EXHAUSTED và bỏ safe result. Exception cũ chỉ có code/layer, không giữ
  evidence/provenance để viết báo cáo khi không còn budget reasoning.

### Thiết kế sửa

R1: giữ request task có tên/reference, chờ bằng `asyncio.wait`, request cancellation
tối đa một lần; wrapper rejoin cùng Client owner nếu wait bị ngắt. SDK context vẫn
enter/exit trong owner của Client, không đổi `client.py` hoặc SDK binding. Tách
`BridgeBudget.cleanup_seconds` khỏi request/total budget. Caller cancellation lặp
được trì hoãn trong bounded cleanup join, không cancel owner lại. Nếu chưa xong,
trả CLEANUP_TIMEOUT với pending/unconfirmed state, giữ reference và chặn dispatch.
Caller phải giữ bridge rồi `finish_cleanup()` để rejoin/acknowledge; không có retry
tool hoặc task chạy nền bị âm thầm bỏ qua. Timeout/disconnect không đồng nghĩa
operation chưa chạy: execution_state vẫn unknown khi chưa có response.

R2: thêm phase/outcome nội bộ (`not_dispatched`, `awaiting_result`, `result_received`,
`sanitized`; execution_state phân biệt not_dispatched/unknown/response_received).
Sau sanitize thành công, giữ bounded ToolMessage và safe_result dù hết time budget.
`budget_exhausted=True` ngăn mapping sang LLM; dispatch tiếp bị chặn. Chỉ dùng safe
content cho báo cáo. Không thêm trường wire contract, không truncate JSON, không
trả evidence khi validation/redaction/output cap thất bại. Safe result trước đó
không bị xóa chỉ vì thử gọi lại sau hết budget; một dispatch mới bắt đầu outcome mới.

### Files thay đổi trong lượt sửa này

Chỉ **5 trong 10 file boundary chưa stage** đã thay nội dung:

1. `src/agent/integration_boundary/bridge.py`: ownership/cleanup budget và safe outcome.
2. `tests/integration_boundary/test_bridge.py`: 11 regression tests mới; fake client
   lỗi transport bổ sung disconnect theo Protocol hiện có.
3. `tests/integration_boundary/test_stdio_optional.py`: tăng assertions owner done,
   owner/worker references cleared, cleanup confirmed, unknown outcome và process
   returncode cho timeout/cancellation/disconnect; teardown kiểm owner cleared.
4. `tests/integration_boundary/README.md`: API cleanup, trách nhiệm caller và safe outcome.
5. `tests/integration_boundary/REPORT.md`: báo cáo này.

Không sửa Client, Adapter, contracts SCRUM-12, VM hoặc test server của thành viên khác.
Branch/HEAD không đổi; tracked/staged vẫn sạch, đúng 10 untracked boundary. Worktree
khác và SHA256 của toàn bộ 53 untracked cũ được đối chiếu, giữ nguyên.

### Regression coverage

**R1 — 6 tests:** Client timeout trước bridge với cleanup chậm; bridge timeout trước
Client; caller cancellation lặp trong cleanup; bridge cleanup budget hết và explicit
rejoin; Client cleanup timeout báo unconfirmed rồi rejoin; cleanup kết thúc sau khi
bridge đã trả lỗi nhưng vẫn yêu cầu acknowledged join. Assert owner identity/done,
same-task context enter/exit, không bị cancellation lần hai trong teardown, worker
reference cleared và chỉ một dispatch, không chỉ kiểm connected=false.

**R2 — 5 tests:** hết budget trước dispatch; hết sau sanitize giữ evidence/provenance
và chặn model/dispatch; disconnect trước response với unknown outcome; redaction
failure fail closed; oversized result fail closed. Timeout trước response được phủ
bởi R1 và real stdio tests. Safe evidence test kiểm cả ID/source, secret synthetic
đã che, UTF-8 cap, safe_result trong lỗi mapping và retention sau call bị chặn.

### Kết quả mới nhất

Runtime xác minh: **Python 3.12.16, mcp 2.3.0**. Không cài package. Adapter lấy từ
exact origin/main `990f41f68c8ef13218bf825bd57ff233a8c82f1c` qua temporary composition.

| Suite | Offline | Bật real synthetic stdio |
|---|---|---|
| Adapter | 57 PASS | 57 PASS |
| MCP Client | 42 PASS, 4 SKIP | 46 PASS |
| Boundary | 46 PASS, 4 SKIP | 50 PASS = 42 unit + 4 Adapter mocks + 4 stdio |
| Tổng | **145 PASS, 8 optional SKIP** | **153 PASS, 0 FAIL/ERROR/SKIP** |

Lệnh chạy giữ nguyên ở README. Real stdio chạy ngoài sandbox IPC với synthetic
server; không Ollama inference/diagnostic tools/system changes. **8/8 real tests**
xác nhận subprocess returncode sau teardown; boundary lỗi/cancel còn assert owner
done, owner và worker reference cleared, cleanup confirmed trước test kết thúc.
Không orphan trong các lần chạy này. Slow-task startup diagnostics không phải lỗi.

### Kết luận và giới hạn

- **PASS R1/R2 — READY FOR DRAFT PR UPDATE**, chờ Hiền review và cho phép thao tác Git.
- Cleanup không hợp tác có thể còn pending/unconfirmed sau bounded join: caller
  phải giữ ownership và rejoin; không bảo đảm hard wall deadline hoặc kill tiến trình
  bất kỳ. Chỉ kết luận không orphan cho các synthetic runs đã kiểm chứng.
- Validation/redaction synchronous, cap sau parse và follow-up detector log thật
  vẫn như baseline; không mở rộng sửa redactor/Agent reasoning/approval.
- **BLOCKED tích hợp contracts:** chờ SCRUM-12 finalized, metadata binder/output
  semantic validators và kiểm thử server thật. Mock/SDK PASS không hoàn tất MCP
  integration hoặc Jira SCRUM-22.
- Không commit, push, merge, sửa Jira/PR hoặc worktree khác trong lượt này.

## Final pre-commit review and rerun — 06/11/2026

Hiền authorized committing exactly the 10 boundary files and updating Draft PR #7.
Final review found no BLOCKER/NEEDS CHANGE within this contract-independent scope.
Python 3.12.16 / MCP 2.3.0 rerun: Adapter 57 PASS, Client 46 PASS, boundary 50 PASS;
153 PASS, zero failures/errors/skips with real synthetic stdio enabled. All eight
stdio tests verify reaped children. No live inference or Linux diagnostic tools.
The earlier no-commit statements above describe previous review sessions. The
current update is authorized for Draft review only; integration gates and the
cleanup/redaction limitations remain unchanged.
