# SCRUM-12 / PR #6 — privacy và ID/frame follow-up

Ngày 14/11/2026 (Asia/Saigon). Baseline: `6d581f2eeaba87f22fd6aa33b1fc47a3afb4757d`, branch `task/P00-03-mcp-tool-contracts`. Đây là bản bàn giao bổ sung cho audit của Hiền, không thay báo cáo lịch sử 13/11.

## Kết quả và phạm vi

- **132 pytest PASS**, 0 FAIL, 0 SKIP. Gồm 114 tests hiện có trong checkout và 18 regression mới qua official SDK stdio. Không sửa hoặc bỏ các tests cũ.
- Official all-tools stdio: **12/12 tools PASS**, canonical discovery và native structuredContent không đổi.
- Existing mock integration, SDK ping stdio và git diff --check: PASS.
- Chỉ bốn files: `mock_mcp_server.py`, `tests/test_pr6_followup.py`, `docs/mcp-tool-contracts.md`, `docs/SCRUM-12-followup-2026-11-14.md`.
- Không đổi schemas, dependency baseline, signatures, implementation nghiệp vụ 12 tools, Linux collectors hay module của Hiền/Trang. Không merge PR, sudo, restart thật hoặc retry write.
- Hiền đã báo 140 pytest PASS và service_status/ToolBridge tương thích tại baseline (theo thông điệp review do người dùng cung cấp). Bộ 140 tests độc lập đó không có trong checkout; kết quả cục bộ 132 không được trình bày như đã chạy lại suite của Hiền.

## Privacy blocker: tái hiện và cách sửa

Trước sửa, official SDK stdio regression tái hiện **hai FAIL**: detect_anomaly với since > until và list_failed_units với invalid cursor đều trả INVALID_INPUT nhưng echo trace_id cùng metadata khác. Ba ID-policy rejection tests mới cũng FAIL vì server vẫn trả response thay vì đóng phiên (5 FAIL tổng ở lượt baseline chọn lọc).

Root cause: schema rejection đi qua safe_error, nhưng schema-valid semantic rejection do business function trả envelope trực tiếp rồi đi tới serialization. Metadata hợp lệ theo schema vẫn có thể là dữ liệu nhạy cảm.

Cách sửa tối thiểu: tại call_tool, mọi status=error/error_code INVALID_INPUT hoặc EXPIRED được dựng lại qua safe_error trước output validation/serialization. Không copy business message hoặc metadata của request vào error. Response dùng trace_id/logical_tool_call_id/attempt_id='invalid-input', session_id=null; data chứa message cố định, field rỗng và rule=semantic. JSON-RPC correlation ID của request được admission chấp nhận vẫn roundtrip nguyên vẹn.

Bằng chứng official SDK stdio:

| Ca | isError / error_code | Metadata marker trong structuredContent | Trong JSON text | Trong stdout/stderr |
|---|---|---|---|---|
| detect_anomaly: reversed window | true / INVALID_INPUT | Không | Không | Không |
| list_failed_units: invalid cursor | true / INVALID_INPUT | Không | Không | Không |
| query_journal: invalid cursor | true / INVALID_INPUT | Không | Không | Không |
| search_log: invalid cursor | true / INVALID_INPUT | Không | Không | Không |
| verify_fix: reversed window | true / INVALID_INPUT | Không | Không | Không |

Dùng synthetic marker, không credentials thật. Test assert cả native envelope, text content và captured stdout/stderr; marker được đặt trong cả bốn application metadata IDs, không chỉ trace_id. Các nhánh EXPIRED cũng đi cùng boundary; tests cursor cũ vẫn PASS.

## Policy 64 KiB và JSON-RPC ID đã chọn

**Server-side stdio admission**, không chỉ là lời khuyên cho client:

1. JSON-RPC ID tối đa **128 byte UTF-8 của JSON scalar đã serialization**, gồm quotes/escape bytes. Công thức: `len(json.dumps(id, ensure_ascii=False).encode('utf-8'))`. Numeric IDs cũng áp dụng giới hạn này.
2. IDBoundedReadStream bọc receive stream do official SDK cung cấp, kiểm trước dispatch mọi request, kể cả initialize. Không viết lại JSON-RPC parser/framing/transport.
3. ID hợp lệ roundtrip đúng nguyên bản. ID vượt giới hạn bị **đóng phiên stdio bằng kết thúc mock child process với exit code 2**, ghi một diagnostic cố định vào stderr; **không gửi response** cho request đó. Không truncate, thay thế, hoặc echo ID trong protocol-error reply.
4. Lý do: một protocol error reply bình thường vẫn phải echo ID, nên ID dài có thể tự vượt cap. Fail-closed session termination tránh response quá cap và tránh chờ blocked stdin worker khi client độc hại giữ pipe mở. Đây là mock process riêng, không chạm process ngoài, Linux service hoặc write helper.
5. Với ID được admission chấp nhận, total tools/call frame vẫn <=65,536 byte UTF-8, gồm native content/structuredContent, SDK metadata/resultType, actual ID và newline. Khi payload không fit, giữ whole-record partial hoặc bounded tool error theo contract hiện có.
6. Binder cần sinh ID nhỏ hoặc áp cùng giới hạn **trước gửi**; EOF/exit 2 là lỗi transport, làm fail outstanding requests, không coi là thành công và không tự retry write. Nếu mở session mới, cursor cũ không còn hợp lệ.

Boundary tests thực tế:

- PASS/roundtrip: 126 ASCII chars; 31 emoji bốn byte + 2 ASCII chars; 63 backslashes (JSON escapes); integer thông thường; integer 128 digits.
- REJECT/EOF/no echo: 127 ASCII chars; 32 emoji; 64 backslashes; integer 129 digits; ID chứa synthetic marker và hơn 70,000 chars.
- REJECT overlong initialize ID trước dispatch.
- Maximum allowed ASCII/Unicode IDs với near-cap payload: response bounded OUTPUT_LIMIT, schema hợp lệ, actual frame <=64 KiB, actual ID giữ nguyên.

Giới hạn được nói rõ: guard chạy **sau SDK parsing** nên không phải raw-input allocation/DoS cap trước parse; whitespace floods và memory admission thuộc SDK/deployment. Discovery/schema payloads không thuộc per-tool-result cap. HTTP/in-process adapters không tự kế thừa guard của entrypoint stdio. Không tuyên bố cap cho mọi loại transport/frame ngoài phạm vi này.

## Finalization và handoff

Contracts/schema_version vẫn **1.0.0**, revision tài liệu **14/11/2026**, trạng thái **finalization candidate; team signoff pending**. Không tự ghi APPROVED hoặc đã đủ điều kiện merge.

- Authoritative index: schemas/manifest.json; input *.schema.json; data *.response.schema.json; full output *.result.schema.json. Không đổi files này trong follow-up.
- Hiền: xin kiểm lại hai privacy probes, cùng boundary các semantic rejections; client EOF/exit-2 handling; maximum-ID/near-cap tests; service_status ToolBridge compatibility. Bộ 140-test audit của Hiền cần rerun độc lập trên commit mới.
- Trang: xin review cách fail-closed session, privacy/redaction scope, mock-only termination và production permission/approval constraints. Tài khoản GitHub cụ thể cần xác nhận để assign reviewer đúng người.
- Mock không có general-purpose production secret redactor. Valid free-text/metadata trên successful calls có thể được echo; không dùng mock để gửi credentials thật. Safe-error guarantee là không phản chiếu rejected input. ID admission là byte-bound policy, không phải cơ chế redaction ID hợp lệ.
- Approval vẫn chỉ facade propose/status, executed=false, không persist approvals/execute helper/restart Linux. Process exit làm mất cursors/pending read calls trong mock; binder phải biểu diễn failure rõ ràng.

**Reviewer/signoff:** chưa có approval của Hiền/Trang cho revision này. User đã cho phép commit và push PR #6. SHA mới và trạng thái review request được ghi trong comment PR sau push; không ghi SHA của chính file này để tránh self-reference. Chưa merge.

## Tái chạy và evidence

```text
.venv/Scripts/python.exe -m pytest -q tests/test_pr6_followup.py
.venv/Scripts/python.exe -m pytest -q tests
.venv/Scripts/python.exe tests/mcp_smoke/test_all_tools_stdio.py
.venv/Scripts/python.exe tests/test_mock_server.py
.venv/Scripts/python.exe tests/mcp_smoke/test_stdio_client.py
```

Logs và JUnit của lượt cuối lưu tại outputs/pr6-followup-2026-11-14 trong workspace Codex; không commit synthetic failing-baseline output hoặc log machine-specific vào repo. Technical changes đã kiểm cục bộ; independent audit mới và reviewer signoff vẫn pending.
