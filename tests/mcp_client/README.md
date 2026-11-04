# SCRUM-22 — MCP Client độc lập contracts

Trạng thái: **In Progress, chờ review**. Python mục tiêu **3.12**, SDK pin
**mcp==2.3.0**, stdio. Không phụ thuộc nhánh LLM Adapter hoặc contracts Draft PR #6.

## Phạm vi và giao diện

`agent.mcp_client.MCPClient` là Protocol độc lập SDK:

- `connect()`, `disconnect()` và async context manager trên `StdioMCPClient`.
- `list_tools(cursor=None) -> ToolPage`: một trang, giữ nguyên tool descriptor /
  inputSchema / outputSchema; cursor discovery được truyền rõ ràng, không tự lặp.
- `call_tool(name, arguments) -> ToolResult`: content, structured_content, is_error.
  Giữ business envelope trong structured_content; không tự ánh xạ status dự án.
- `MCPClientError.code`: thông tin lỗi an toàn, không kèm payload hoặc stderr.

Config chỉ do ứng dụng/operator tin cậy cung cấp: absolute executable, fixed argv,
noncredential environment và allowlist tool mặc định rỗng. Tool phải vừa được
operator cho phép, vừa xuất hiện trong discovery. Discovery/annotations không cấp
quyền. Không truyền executable/argv/env/allowlist từ LLM hoặc log. Mock write trong
unit tests chỉ là tên giả, không có side effect. Client không thay approval layer.

Một owner task thực hiện enter/use/exit SDK trong cùng task, tránh lỗi AnyIO cancel
scope do teardown khác task. Mỗi client nhận tối đa một request; concurrent request
trả BUSY. Operation không retry/reconnect tự động; InputRequired không được replay.
Timeout/hủy sẽ đóng session; caller phải chủ động reconnect rồi discover lại.
Không diễn giải timeout thành “tool chưa chạy”: kết quả execution có thể unknown.

Binding v2.3.0 dùng `stdio_client`, `ClientSession`, `initialize`,
`list_tools(params=PaginatedRequestParams(...))`, `session.call_tool` với
`allow_input_required=True`. Đây là handshake path được v2 hỗ trợ, không dùng
high-level Client.call_tool có recovery/replay. SDK kiểm output schema đã discover;
input schema/business policy và external refs chờ SCRUM-12 chốt. Client không tự
resolve schema qua mạng. Không chứa schema của 12 tools.

## Timeout, giới hạn và dữ liệu nhạy cảm

Mặc định connect/request 10s; cleanup 8s; mỗi budget cấu hình trong (0,60]s.
Deadline caller là operation budget **cộng cleanup budget**, phụ thuộc event loop và
SDK hợp tác cancellation; không phải hard wall deadline/OS sandbox. SDK stdio có
teardown subprocess riêng. CLEANUP_TIMEOUT giữ owner reference và cấm reconnect
cho tới khi cleanup xong; không âm thầm bỏ task/process. Không có force kill do
wrapper tự viết. Sau caller cancellation, cleanup bình thường xong rồi re-raise
CancelledError; nếu cleanup thất bại, lỗi cleanup được ưu tiên để báo tài nguyên
chưa được xác nhận thu hồi. Không thể bảo đảm server đã dừng thực thi tác vụ chỉ
bằng cancellation.

Cap mặc định 64 KiB tính trên JSON normalized UTF-8 compact của **cả trang discovery
hoặc kết quả call**, sau SDK parse. Quá cap bị từ chối, không tự truncate/đổi partial.
Đây là cap cục bộ, chưa phải cách đếm wire bytes chính thức của contracts. Không
chặn được một frame quá lớn trước SDK parse; cần server collector cap và review
transport framing trước triển khai với dữ liệu thật. Tool arguments cùng budget,
chỉ chấp nhận JSON hữu hạn/key chuỗi. Không kiểm input schema nghiệp vụ ở bước này.

Wrapper không ghi logs/payload/reasoning; repr của kết quả che content. Child stderr
đi vào /dev/null. SDK baseline environment bị override rồi mới thêm biến allowlist;
không copy toàn bộ os.environ. Payload trả về vẫn untrusted và **không được coi là
đã redacted**; collector/LLM adapter/CLI giữ trách nhiệm redaction/rendering. Chưa
kiểm chứng logging nội bộ SDK với payload nhạy cảm/malformed peer; không bật SDK debug/trace hoặc
đưa dữ liệu nhạy cảm vào optional fixture. Hợp nhất logging policy với Trang trước
production; wrapper không thay đổi global logging configuration.

Error codes: INVALID_CONFIG, INVALID_STATE, NOT_CONNECTED, BUSY,
INVALID_ARGUMENTS, TOOL_NOT_ALLOWED, SDK_UNAVAILABLE, SDK_VERSION_MISMATCH,
TIMEOUT, DISCONNECTED, TRANSPORT_ERROR, PROTOCOL_ERROR, MALFORMED_RESULT,
UNSUPPORTED_RESULT, RESULT_TOO_LARGE, CLEANUP_TIMEOUT. MCP isError=true là kết quả
tool, không phải transport exception. Wrapper không xuất raw MCP error message.

## Chạy unit tests offline

Từ worktree SCRUM-22, không cần cài SDK hoặc chạy server:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests/mcp_client -p 'test_*.py' -v
```

`test_client.py` dùng fake server trong bộ nhớ; `test_sdk_binding.py` dùng API-shaped
module doubles. Kiểm tra transport wiring, timeout, env, stderr và same-task cleanup.
Các tests này không chứng minh tương thích runtime SDK thật.

## Optional stdio smoke với SDK thật

Chỉ chạy sau khi có môi trường Python 3.12 đã được chuẩn bị và `mcp==2.3.0` được
cài theo `requirements.txt`. Lệnh bên dưới không cài package:

```bash
RUN_MCP_STDIO_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.12 -m unittest discover -s tests/mcp_client -p 'test_stdio_optional.py' -v
```

Fixture `mock_stdio_server.py` do Hiền sở hữu, dùng MCPServer v2 qua stdio: echo
dữ liệu giả, chờ để test cancellation/timeout và tự thoát subprocess để giả lập
disconnect. Chỉ ghi marker cố định trong TemporaryDirectory của test launcher;
không nhận đường dẫn qua tool arguments. Không dùng server/contracts của Thủy,
không gọi Ollama, không đọc journal/file hệ thống, không có Linux commands. Fixture là test artifact, không
phải contract mới. Test này mặc định SKIP, không được tính PASS khi chưa chạy.

## Interfaces chờ SCRUM-12

1. Launch manifest: executable/argv, Python/SDK/protocol handshake, environment/UID,
   tool allowlist và cách xác minh server identity/version. VM Python 3.8 không
   đáp ứng SDK yêu cầu >=3.10; baseline nhóm vẫn Python 3.12.
2. Registry đủ 12 input/output schemas, JSON Schema draft, external $ref bundling,
   versioning và nơi validate input trước dispatch. PR #6 hiện là Draft.
3. Logical call ID, attempt ID, trace/session/environment IDs, nơi gắn metadata;
   JSON-RPC ID thuộc SDK. Không tự tạo field dự án trong client hiện tại.
4. Mapping ok/partial/error/approval_required sang MCP result; error codes/retry
   policy. Mọi write timeout/disconnect phải đối soát, không tự retry.
5. Business pagination cursor binding tool/filters/session/TTL, khác cursor tools/list.
6. Timestamp started/completed/evidence.observed_at, provenance shape và UTC.
7. Đếm wire output 64 KiB, collector cap riêng, truncated/partial semantics.
8. Cancellation acknowledgement và execution_unknown, filtered SDK logging.
9. Handoff SCRUM-22 → LLM Adapter: tool descriptors/results vẫn untrusted; adapter
   mapping/validation/redaction riêng. Chưa tích hợp SDK với LLM/Agent/approval.

## Tài liệu chính thức đã đối chiếu

- [SDK v2.3.0 session](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py)
- [SDK v2.3.0 stdio](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py)
- [SDK v2.3.0 high-level Client](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/client.py)
- [SDK v2.3.0 MCPServer fixture API](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py)
- [Exact release metadata](https://pypi.org/pypi/mcp/2.3.0/json)
- [PR #6 — read-only, Draft](https://github.com/hienne215/linux-log-agent/pull/6)

## Môi trường smoke tạm thời Python 3.14

Hiền cho phép kiểm chứng SDK runtime trên Python 3.14.4; đây không thay thế baseline
Python 3.12. Venv riêng nằm ở `.venv-scrum22/`, không dùng system site-packages.
Nếu Python của distro thiếu ensurepip, bootstrap pip bằng script chính thức PyPA
và **interpreter của venv**; không cài package hệ thống hoặc dùng global pip.

Chạy offline và stdio riêng để báo cáo đúng số PASS/SKIP:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-scrum22/bin/python -m unittest discover -s tests/mcp_client -p 'test_*.py' -v
RUN_MCP_STDIO_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-scrum22/bin/python -m unittest discover -s tests/mcp_client -p 'test_stdio_optional.py' -v
```

Smoke quan sát process thật do AnyIO/SDK tạo, xác nhận một subprocess đã startup,
initialize xong trước discovery/call, rồi có returncode sau disconnect (đã được
reap). Không thay transport bằng double ở test này. Fixture không sinh process con.
Xem REPORT.md để biết kết quả runtime thực tế; có script không đồng nghĩa đã PASS.

Lượt runtime Python 3.14.4 đã PASS ngoài sandbox của phiên làm việc này; cùng test
và probe SDK trực tiếp timeout ở initialize trong sandbox. REPORT.md giữ cả hai
kết quả. Điều này chưa chứng minh deployment sandbox/VM của nhóm tương thích;
cần chạy lại trên môi trường Python 3.12 mục tiêu.


## Baseline Python 3.12 — đã kiểm chứng 04/11/2026

Môi trường riêng `.venv-scrum22-py312/`: CPython **3.12.16**, **mcp==2.3.0**.
Runtime do uv quản lý ở thư mục task ngoài repository (`../.scrum22-runtime/`),
không thay Python hệ thống, PATH hay shell profile. Venv 3.14 cũ vẫn giữ nguyên.
uv dùng bản phân phối Astral python-build-standalone; đây là kiểm chứng host Linux,
không phải nghiệm thu VM hoặc deployment hardening của Trang.

```bash
RUN_MCP_STDIO_SMOKE=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-scrum22-py312/bin/python -m unittest discover -s tests/mcp_client -p 'test_*.py' -v
RUN_MCP_STDIO_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-scrum22-py312/bin/python -m unittest discover -s tests/mcp_client -p 'test_stdio_optional.py' -v
```

Offline: **42 PASS, 4 opt-in SKIP**. Stdio thật ngoài sandbox của phiên này:
**4 PASS**, 0 FAIL/ERROR/SKIP. Hạn chế sandbox đã ghi ở lượt 3.14 vẫn áp dụng;
không tự thay profile sandbox để chạy tests.

- Normal call: startup, initialize, discovery, content/structuredContent/isError.
- Cancellation: marker chứng minh handler mock_wait đã bắt đầu; CancelledError
  được truyền về caller; marker wait_cancelled xác nhận server xử lý hủy; không
  có wait_completed. Client mất kết nối và SDK thu hồi subprocess.
- Timeout: request budget 0.5s không làm ngắn connect budget 10s; mock handler đã
  bắt đầu, trả TIMEOUT và subprocess được thu hồi.
- Disconnect: chỉ subprocess mock tự exit(23); client trả DISCONNECTED, không retry.
  Mọi test kiểm returncode để xác nhận không để lại orphan.

SDK default read timeout dùng max(connect_timeout, request_timeout) vì initialize
cũng chịu SDK default. Wrapper vẫn áp budget riêng cho connect/request; call_tool
vẫn truyền request_timeout xuống SDK. Các mã SDK 2.3.0 CONNECTION_CLOSED (-32000)
và REQUEST_TIMEOUT (-32001) được chuẩn hóa thành DISCONNECTED/TIMEOUT, không lộ
raw message; các MCPError khác giữ PROTOCOL_ERROR. Đây là mã transport MCP, không
phải error contracts của SCRUM-12.

Giữ nguyên các giới hạn: cooperative timeout, không hard wall deadline; cap sau
SDK parse; schema/business policy/manifest/logging cần review; chưa tích hợp
contracts hoặc LLM Adapter, chưa chạy Linux tools thật. Kết quả cancellation chỉ
áp dụng synthetic handler hợp tác trên asyncio, không chứng minh tác vụ ghi bất kỳ
đã được dừng hoặc rollback. Không được tự retry write khi kết quả chưa rõ.
