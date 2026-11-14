# Bàn giao SCRUM-12 / P00-03 — MCP contracts 1.0.0

Ngày thực hiện: 13/11/2026 (Asia/Saigon). Repo `hienne215/linux-log-agent`, PR #6. Phạm vi chỉ contracts và mock, không production collectors. **Kết quả kỹ thuật cục bộ: 114 pytest PASS; official SDK stdio đủ 12/12 tools PASS. BUG 01–09 FIXED; BUG 10 PARTIAL vì còn review/finalization. Chưa READY TO MERGE.**

## A. Git và phạm vi thay đổi

- Checkout: `C:\Users\LENOVO\Desktop\linux-log-agent`.
- Branch: `task/P00-03-mcp-tool-contracts`.
- HEAD trước/sau: `380a0302a9a4c7b3badd857dd37b5d2c42cfd3d7` (không commit; branch không có commit mới sau audited HEAD).
- Remote PR #6 và branch remote được đối chiếu bằng `git ls-remote`, cùng HEAD `380a0302a9a4c7b3badd857dd37b5d2c42cfd3d7`.
- Git identity local: ThuThuy066 / thuthuynguyen.06112006@gmail.com. Remote origin đúng URL repository; không có thao tác push/authenticated mutation nên không tuyên bố đã xác minh tài khoản GitHub đang đăng nhập.
- Trước sửa: đúng một tracked modification `schemas/tools/query_journal.response.schema.json`; không có untracked được báo bởi Git. Description/formatting hiện hữu của file đó được bảo toàn về nội dung; không ghi đè bằng bản HEAD.
- `origin/main` local ở `7d3e21f012ae38324ab01dab4c33efe1138ba05e`. Đã xem diff so với ref này. Fetch không ghi được `.git/FETCH_HEAD` do vùng Git metadata được bảo vệ; không đổi config để né.
- Đã đối chiếu remote main bằng GitHub compare API read-only: `990f41f68c8ef13218bf825bd57ff233a8c82f1c`. Branch ahead 3, behind 2, trạng thái diverged. Không rebase/reset/stash/merge; reviewer cần xem lại base hiện tại.
- Không sửa ARCHITECTURE.md, SECURITY.md hay module triển khai của Hiền/Trang. Chỉ mock facade SCRUM-12 chứa đủ 12 tool contracts.
- Source, schemas, tests và docs còn ở working tree để Thủy review. Không commit, push, merge, đổi Jira, xóa dữ liệu hoặc restart thật. Scratch tự tạo được chuyển ra workspace Codex, không để trong repo.

Trạng thái trước khi tạo báo cáo này (báo cáo repo cũng là file mới chưa commit):

```text
M docs/mcp-tool-contracts.md
 M inspect_tool_metadata.py
 M mock_mcp_server.py
 M requirements.txt
 M schemas/tools/check_config.schema.json
 M schemas/tools/detect_anomaly.schema.json
 M schemas/tools/disk_usage.schema.json
 M schemas/tools/failed_logins.schema.json
 M schemas/tools/list_failed_units.schema.json
 M schemas/tools/listening_ports.schema.json
 M schemas/tools/memory_oom_events.schema.json
 M schemas/tools/query_journal.response.schema.json
 M schemas/tools/query_journal.schema.json
 M schemas/tools/restart_service.response.schema.json
 M schemas/tools/restart_service.schema.json
 M schemas/tools/search_log.schema.json
 M schemas/tools/service_status.schema.json
 M schemas/tools/verify_fix.schema.json
 M tests/mcp_smoke/test_all_tools_stdio.py
 M tests/test_response_limit.py
?? schemas/error-data.schema.json
?? schemas/manifest.json
?? schemas/tools/check_config.result.schema.json
?? schemas/tools/detect_anomaly.result.schema.json
?? schemas/tools/disk_usage.result.schema.json
?? schemas/tools/failed_logins.result.schema.json
?? schemas/tools/list_failed_units.result.schema.json
?? schemas/tools/listening_ports.result.schema.json
?? schemas/tools/memory_oom_events.result.schema.json
?? schemas/tools/query_journal.result.schema.json
?? schemas/tools/restart_service.result.schema.json
?? schemas/tools/search_log.result.schema.json
?? schemas/tools/service_status.result.schema.json
?? schemas/tools/verify_fix.result.schema.json
?? tests/mcp_smoke/adversarial_server.py
?? tests/test_contract_audit.py
```

Nhóm files: `mock_mcp_server.py`; `inspect_tool_metadata.py`; `requirements.txt`; 12 input schema files; 12 full-envelope result schema files mới; `schemas/error-data.schema.json`; `schemas/manifest.json`; `restart_service.response.schema.json` bỏ error shape khỏi success data; smoke/all-tools và response-limit tests; `tests/test_contract_audit.py`, test-only fault launcher; docs contracts và báo cáo. File query_journal.response sửa sẵn của người dùng vẫn giữ.

## B. Root cause và bằng chứng theo finding

Baseline Python 3.12.7, mcp==2.3.0. Pytest trước sửa 57 PASS. Bản server HEAD được lưu và chạy riêng để tái hiện findings; không reset checkout. Official all-tools script ở snapshot HEAD FAIL query_journal. Các probe bổ sung xác nhận approved extra bị bỏ qua, outputSchema=None, output-limit fallback sai data, service evidence rỗng, historical timestamp dùng ngày chạy và enum error phản chiếu synthetic sentinel. Đây là tái hiện từ snapshot HEAD, không phải chạy bộ audit riêng của Hiền.

Ban đầu stdio treo trước event loop: stack ở Windows asyncio socketpair/loopback do sandbox network restriction. Sau khi được cấp network permission, baseline FAIL và bản sửa PASS được quan sát thực tế. Không thay SDK hay system Python. Một test raw-protocol lúc đầu cũng bắt lỗi constructor MCPError của phần sửa; đã sửa theo SDK 2.3.0 `(code, message)` và test xác nhận -32602. Startup deadline của test subprocess đặt 15 giây để chịu thời gian import Windows; timeout hành vi tool vẫn được kiểm riêng ở 0.05 giây.

### BUG 01 — FIXED (BLOCKER)

- Vị trí sau sửa: `mock_mcp_server.py:207` (`register_tool`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: Required-only query_journal qua snapshot HEAD FAIL; optional None xuất hiện dù wire không có trường.
- Root cause: SDK FuncMetadata.validate_arguments gọi Pydantic model_validate rồi model_dump_one_level lấy mọi model field, gồm defaults; inspect.signature.bind trong wrapper chỉ nhận giá trị đã thêm.
- Cách sửa: Low-level Server nhận raw arguments; canonical schema chạy trước fn(**arguments); Python defaults chỉ áp dụng bên trong lời gọi hợp lệ.
- Bằng chứng: runtime_raw_contract_matrix; official_sdk_stdio_contract_matrix; sdk_optional_values_and_protocol_negatives.
### BUG 02 — FIXED (BLOCKER)

- Vị trí sau sửa: `mock_mcp_server.py:237` (`call_tool`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: HEAD nhận approved=true ở service_status và trả isError=false; discovery khác file input.
- Root cause: Pydantic signature model bỏ extra fields trước wrapper; high-level discovery dùng signature thay canonical schema.
- Cách sửa: Dispatch low-level chính thức; descriptors đọc file canonical self-contained; reject extra/type/null/version/traversal trước thực thi.
- Bằng chứng: official_sdk_stdio_contract_matrix; raw_protocol_error_does_not_reflect_invalid_arguments.
### BUG 03 — FIXED (BLOCKER)

- Vị trí sau sửa: `mock_mcp_server.py:168` (`bound_response`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: Snapshot HEAD trả partial/OUTPUT_LIMIT cho search_log nhưng data chỉ có message/max_response_bytes, thiếu 5 required fields.
- Root cause: Chỉ đo envelope trước native content/structuredContent; fallback không theo tool-data schema.
- Cách sửa: Đo JSONRPCResponse UTF-8 gồm native result, SDK metadata/resultType, actual ID và newline. Giữ whole records nếu fit, còn lại error strict; không xé JSON/UTF-8/evidence.
- Bằng chứng: sdk_faults_and_cleanup[near/huge/unicode/nested/partial]; all_tools_bounded_error_schema; test_response_limit.
### BUG 04 — FIXED (BLOCKER security)

- Vị trí sau sửa: `mock_mcp_server.py:221` (`safe_error`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: Snapshot HEAD: detect_anomaly.signals chứa synthetic sentinel bị phản chiếu trong error enum. Lần thử pattern service_status không phản chiếu sentinel do lỗi unevaluatedProperties đứng trước; không dùng ca đó làm bằng chứng leak.
- Root cause: Wrapper dùng ValidationError.message; message validator có thể chứa submitted values/property names.
- Cách sửa: Không tạo raw validator/exception text; field chỉ lấy identifier schema cho phép; invalid metadata dùng marker cố định; protocol guard chặn malformed arguments trước lỗi SDK model.
- Bằng chứng: official_sdk_stdio_contract_matrix + capfd; raw_protocol_error_does_not_reflect_invalid_arguments; service_provenance_permission_and_unique_ids.
### BUG 05 — FIXED (BLOCKER integration)

- Vị trí sau sửa: `mock_mcp_server.py:485` (`service_status`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: Snapshot HEAD success có evidence=[].
- Root cause: Mock chỉ tạo data trạng thái, không tạo provenance.
- Cách sửa: Evidence ID ngẫu nhiên duy nhất, source synthetic.mock.service_status, observed_at lúc snapshot, excerpt khớp active/sub/load states. PermissionError giữ PERMISSION_DENIED, không đổi thành inactive.
- Bằng chứng: service_provenance_permission_and_unique_ids; all-tools-stdio.
### BUG 06 — FIXED (NEEDS CHANGE)

- Vị trí sau sửa: `mock_mcp_server.py:753` (`detect_anomaly`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: Snapshot HEAD nhận window 01–02/11 nhưng observed_at lấy ngày chạy 13/11.
- Root cause: Nhầm collection time và underlying synthetic event timestamp.
- Cách sửa: Fixture marker cố định 02/11/2026 00:00Z; chỉ tạo findings khi marker trong [since,until); missing timestamp => insufficient_signals/partial/no fabricated evidence. Snapshot times và execution times tách nghĩa.
- Bằng chứng: historical_half_open_window; missing_event_timestamp_and_timezone; official SDK smoke.
### BUG 07 — FIXED (NEEDS CHANGE)

- Vị trí sau sửa: `mock_mcp_server.py:74` (`validate_cursor`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: Positive cursor qua SDK trước đây bị optional None chặn; negative tests chưa chứng minh roundtrip.
- Root cause: Lỗi input layer; thiếu bằng chứng SDK positive, bound TTL/cache/budget.
- Cách sửa: Roundtrip 3 pageable tools; normalize UTC filters; reject modified/expired/cross-tool/session/process/filter; bounded state/offset, không reset.
- Bằng chứng: expired_modified_cross_tool_cursor; cursor_cache_budget; SDK matrix + faults[expired].
### BUG 08 — FIXED (BLOCKER)

- Vị trí sau sửa: `mock_mcp_server.py:229` (`list_tools`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: Snapshot HEAD discovery outputSchema=None dù có 12 data schema files.
- Root cause: Không đăng ký output schemas; chưa định nghĩa rõ scope envelope/data và chưa validate output độc lập.
- Cách sửa: 12 *.result.schema.json phủ toàn structuredContent; data success riêng, error-data riêng; runtime validate mọi status. Bỏ generic error branch khỏi restart success-data để message-only không thể giả success.
- Bằng chứng: all_schema_syntax_and_result_composition; output_validator_catches_corruption (12 tools); discovery equality.
### BUG 09 — FIXED (BLOCKER acceptance)

- Vị trí sau sửa: `mock_mcp_server.py:287` (`run`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: Snapshot HEAD official all-tools stdio FAIL query_journal trong khi baseline pytest 57 PASS.
- Root cause: Offline/schema tests không thực thi đầy đủ SDK signature parsing và discovery.
- Cách sửa: Official smoke hiện yêu cầu native structuredContent và canonical descriptor equality; không skip tool. SDK tests thật cho normal/negative/limits/cursors/lifecycle.
- Bằng chứng: all-tools-stdio 12/12; mock-integration; sdk-ping; pytest SDK matrix.
### BUG 10 — PARTIAL (NEEDS CHANGE)

- Vị trí sau sửa: `mock_mcp_server.py:207` (`register_tool`); BUG 10 chủ yếu tại `docs/mcp-tool-contracts.md:221` và checklist §11.
- Tái hiện/baseline: Docs trước sửa §10 placeholder và §11 chưa hoàn tất.
- Root cause: Chưa có manifest/scope output/error/limits/timestamp/handoff và reviewer evidence.
- Cách sửa: Hoàn thiện §10–11, manifest, dependency pins, error ownership/policies và requests cho Hiền/Trang. Không tự APPROVED/finalized; independent rerun và team signoff vẫn chờ.
- Bằng chứng: docs/mcp-tool-contracts.md:221; manifest composition test; báo cáo này.

## C. Test matrix và loại bằng chứng

| Test group | PASS | FAIL | SKIP | Loại kiểm thử |
|---|---:|---:|---:|---|
| Bộ schema gốc (input/common/data output) | 54 | 0 | 0 | Offline JSON Schema |
| Bộ output-cap gốc, expectation đổi partial sai data thành bounded error | 3 | 0 | 0 | Runtime mock |
| Audit schema syntax/composition/manifest | 1 | 0 | 0 | Offline JSON Schema |
| Audit input/output/evidence/time/cursor/budgets/cap | 45 | 0 | 0 | Runtime mock độc lập |
| Audit SDK matrix/optional/protocol/fault lifecycle | 11 | 0 | 0 | Official SDK stdio, subprocess thật |
| Tổng pytest | 114 | 0 | 0 | 57 tests baseline + 57 regression tests |
| Official all-tools script (12 invocations, 12 discovered tools) | 1 | 0 | 0 | Official SDK stdio |
| Existing mock-integration script (normal + selected negatives) | 1 | 0 | 0 | Official SDK stdio |
| Existing SDK ping smoke | 1 | 0 | 0 | Official SDK stdio |
| git diff --check | 1 | 0 | 0 | Kiểm tra patch whitespace |
| Bộ audit độc lập của Hiền | 0 | 0 | 1 | Chưa có suite/report tại máy này; chưa được reviewer xác nhận |

Các con số của pytest là test cases, không phải số requests: một SDK matrix chạy nhiều required-only/missing/null/type/extra/version/traversal calls cho toàn 12 tools, rồi cursor roundtrips. Không cộng 12 tool invocations vào số pytest tests. Fixture không bị bỏ qua hoặc nới schema để tránh FAIL.

Lệnh tái chạy trong venv hiện có:

```text
.venv/Scripts/python.exe -m pytest -q tests
.venv/Scripts/python.exe tests/mcp_smoke/test_all_tools_stdio.py
.venv/Scripts/python.exe tests/test_mock_server.py
.venv/Scripts/python.exe tests/mcp_smoke/test_stdio_client.py
git -c core.bare=false diff --check
```

Dependency pins: Python 3.12; mcp==2.3.0; jsonschema==4.26.0; referencing==0.37.0; pytest==9.2.0. Không cài/thay package hoặc system Python trong lượt sửa. Đây là direct pins, không phải transitive lock.

## D. Contract consistency matrix

Paths bên dưới tương đối với `schemas/tools/`. `*.response.schema.json` vẫn là schema data; `*.result.schema.json` mới là outputSchema của toàn structured envelope. Nhánh error strict tách biệt, không nới success contracts.

| Tool | Canonical input | Discovery inputSchema | Runtime input | Canonical output envelope | Discovery outputSchema | Runtime output | Normal / error / partial |
|---|---|---|---|---|---|---|---|
| check_config | `check_config.schema.json` | = canonical, PASS | PASS | `check_config.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| detect_anomaly | `detect_anomaly.schema.json` | = canonical, PASS | PASS | `detect_anomaly.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| disk_usage | `disk_usage.schema.json` | = canonical, PASS | PASS | `disk_usage.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| failed_logins | `failed_logins.schema.json` | = canonical, PASS | PASS | `failed_logins.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| list_failed_units | `list_failed_units.schema.json` | = canonical, PASS | PASS | `list_failed_units.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| listening_ports | `listening_ports.schema.json` | = canonical, PASS | PASS | `listening_ports.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| memory_oom_events | `memory_oom_events.schema.json` | = canonical, PASS | PASS | `memory_oom_events.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| query_journal | `query_journal.schema.json` | = canonical, PASS | PASS | `query_journal.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| restart_service | `restart_service.schema.json` | = canonical, PASS | PASS | `restart_service.result.schema.json` | = canonical, PASS | PASS | approval_required / status=ok; error PASS; cap error PASS |
| search_log | `search_log.schema.json` | = canonical, PASS | PASS | `search_log.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| service_status | `service_status.schema.json` | = canonical, PASS | PASS | `service_status.result.schema.json` | = canonical, PASS | PASS | ok; error PASS; cap error PASS |
| verify_fix | `verify_fix.schema.json` | = canonical, PASS | PASS | `verify_fix.result.schema.json` | = canonical, PASS | PASS | partial/unknown; error PASS; cap error PASS |

Mọi tool đều có deliberate-output-corruption test, bounded-error test, canonical discovery equality, required-only và negative raw-input tests. Partial có dữ liệu dùng được được chứng minh bằng query_journal ở SDK fault test; verify_fix luôn partial/unknown; anomaly có partial khi findings bị giới hạn hoặc thiếu event timestamp. Không tuyên bố mọi tool đều có mọi nhánh partial nghiệp vụ.

## E. Security, giới hạn và cleanup

- Synthetic sentinels, không credentials thật. Invalid input không phản chiếu submitted values, secret-like property names, metadata, text/native structured content, exception messages của application hoặc captured stderr/stdout. Raw malformed tools/call arguments cũng có probe protocol riêng. Không dùng regex redaction để che ValidationError.message.
- Python defaults chỉ dùng sau validation; explicit null không được đánh đồng missing. Trừ ba optional nullable fields của verify_fix đã có contract rõ, null bị reject.
- SDK/stdio chính thức xử lý transport; không tự viết JSON-RPC server. Test raw protocol chỉ là client probe với official server.
- Đo response UTF-8 qua actual SDK stdout lines trước client parsing, gồm native content/structuredContent và metadata; có nhỏ, gần cap >65,500 bytes, quá cap, Unicode, nested evidence và whole-record partial. Không truncate JSON string/UTF-8/record giữa chừng. Partial do output cap không resumable (`next_cursor=null`), client phải ghi incomplete.
- Cursors TTL 300s, bound tool/session/normalized filters; cross-process không dùng lại được. State cap 256, offset cap 200; mock datasets hữu hạn. Session_id là application metadata, chưa là cơ chế authentication production.
- Native status/isError/text/structuredContent nhất quán. Output failures fail closed, không convert thành business ok. Restart success data không chấp nhận generic message-only error object.
- Mock không gọi Linux collectors, shell, sudo, systemctl hoặc privileged helper. Shell được dùng ở môi trường phát triển để đọc Git/chạy Python tests; điều này không tạo shell tool trong MCP.
- restart_service chỉ propose/status deterministic mock, executed=false; không retry write, không approval token/control channel hay tác dụng hệ thống.
- Timeout, task cancellation, disconnect và process returncode sau SDK teardown đều được kiểm bằng test-only fault launcher. Không mở console Windows tương tác hay chạy Linux side effects.
- Production auth/allowlists/redaction/collector timeouts/hardening/coordinator/helper chưa được triển khai hoặc kiểm trong VM. Valid free-text request không được coi là đã qua production secret redaction.
- Discovery payload không thuộc cap tool-call; JSON-RPC IDs ngoài application schema vẫn phải được binder giới hạn. Không tuyên bố cap chống arbitrary unbounded transport ID. API malformed JSON/negotiation trước application guard do SDK quản lý.

## F. Quyết định bàn giao

1. **Hiền có thể tích hợp binder với mock theo manifest và contracts đã kiểm cục bộ.** Cần review policy partial không resumable, invalid metadata placeholders, evidence refs và verify unknown. Chưa coi đây là production collector hoặc team signoff.
2. **Đủ để Thủy/nhóm review bản sửa; chưa READY TO MERGE PR #6.** Còn independent audit rerun, Hiền/Trang xác nhận, và kiểm base remote main đã tiến thêm hai commit. Không tự merge hoặc đánh dấu APPROVED/finalized.
3. **Trang cần xác nhận** unit/source/profile allowlists thật, permission/session ownership, coordinator/helper semantics, status/proposal persistence, output budgets và security gate. Mock hiện không chứng minh các quyền Linux thật.
4. **Authoritative:** `schemas/manifest.json` index; canonical inputs `*.schema.json`; data `*.response.schema.json`; full outputs `*.result.schema.json`; common/error schemas. Discovery dùng đúng file canonical; composition test chặn drift. `schemas.zip` chỉ là snapshot cũ, không authoritative.
5. **Hạn chế:** dữ liệu synthetic, không authenticated sessions/production approval persistence/collectors/hardening; no transitive lock; output-cap partial không có resume; protocol-level IDs phải bounded ở binder; bộ audit độc lập chưa có tại máy; chưa có reviewer approvals.

Đề nghị Hiền review SCRUM-22 binder/anomaly/verify/evidence và Trang review Linux/approval/security contracts. Requests đã soạn trong docs; chưa gửi messages hoặc cập nhật PR reviewers. Dừng tại working tree để Thủy review; chưa commit/push.
