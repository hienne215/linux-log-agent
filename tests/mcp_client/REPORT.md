# SCRUM-22 — Báo cáo phần MCP Client độc lập

Ngày: 2026-11-03. Trạng thái: **In Progress — chờ Hiền review**.
Không kết luận toàn bộ SCRUM-22 hoặc MCP integration đã nghiệm thu.

## Git và đầu vào

- Fetch origin trước triển khai. `origin/main` vẫn là
  `7d3e21f012ae38324ab01dab4c33efe1138ba05e` (SCRUM-11).
- Tạo worktree `linux-log-agent-scrum22`, branch `feat/SCRUM-22-mcp-client`
  trực tiếp từ origin/main; không lấy code từ PR #3/#4/#6.
- Worktree benchmark trước/sau: branch `bench/SCRUM-14-llm-smoke`, HEAD
  `b2e6ac87210a52d3a650b0df52db13213fa4819f`, 53 untracked, không staged/modified.
- Worktree adapter trước/sau: branch `feat/SCRUM-23-llm-adapter`, HEAD
  `8f4c146b70c75345324eb1f51a0d059afbcb61ba`, clean.
- Đã đọc ARCHITECTURE.md / SECURITY.md trên main; không thay đổi hai tài liệu.
- PR #6 đọc read-only: OPEN, base main, head `task/P00-03-mcp-tool-contracts`,
  commit `1ac477e5f98b2f4e1065f26f5a060c5f5afe6bfa`. Contracts ghi Draft.
  Requirements pin mcp==2.3.0; có 12 mock functions nhưng mới 7 tool input schema
  files. Không coi đây là contracts finalized hoặc copy schema sang client.
- Công cụ tạo worktree của app trả “Not a git repository” vì chat ở thư mục cha;
  dùng Git worktree từ repository thật. Không reset/stash/rebase/checkout worktree cũ.

## File mới (10)

| File | Mục đích |
|---|---|
| src/agent/mcp_client/__init__.py | Public API |
| src/agent/mcp_client/client.py | Protocol, config, owner lifecycle, normalization |
| src/agent/mcp_client/sdk.py | Lazy exact-version stdio SDK binding |
| tests/mcp_client/test_client.py | Fake server tests |
| tests/mcp_client/test_sdk_binding.py | API-shaped SDK doubles |
| tests/mcp_client/test_stdio_optional.py | Opt-in real SDK/stdin-stdout smoke |
| tests/mcp_client/mock_stdio_server.py | Synthetic echo fixture của Hiền |
| tests/mcp_client/requirements.txt | SDK pin cho optional smoke |
| tests/mcp_client/README.md | API, cách chạy, giới hạn, interfaces chờ |
| tests/mcp_client/REPORT.md | Báo cáo này |

Tất cả còn untracked trong worktree mới; chưa stage/commit/push/PR/Jira.
Không sửa tracked files hoặc README gốc. Không thay đổi PR #3/#4/#6.

## Quyết định triển khai

Dùng ClientSession và stdio_client đúng API source tag v2.3.0, explicit initialize
handshake. SDK lifecycle sở hữu bởi cùng một task; không gọi enter/exit trong hai
wait_for tasks khác nhau. Không dùng high-level Client.call_tool có automatic
recovery/replay. Chỉ gửi một call, không tự retry kể cả tên tool mock_write.
InputRequired bị từ chối thay vì mở thêm vòng gọi.

Discovery giữ nguyên descriptors, một page mỗi lần; allowlist operator độc lập
và mặc định rỗng. Kết quả giữ content/structuredContent/isError; không diễn giải
business status, không tạo logical/attempt IDs hoặc approval fields. Timeout và
cancellation đóng connection, không kết luận tool chưa thực thi. Không có đường
gọi LLM/MCP production server/helper/Linux command trong unit tests.

Safe error code, hidden result repr, child stderr sink, environment allowlist.
UTF-8 normalized JSON cap cục bộ 64 KiB; reject, không tự truncate. Chi tiết và
các giới hạn an toàn ghi trong README.

## Kết quả kiểm thử

Lệnh:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests/mcp_client -p 'test_*.py' -v
```

Lượt offline ban đầu (trước khi cài SDK): **40 tests được discover: 39 PASS, 1 SKIP, 0 FAIL/ERROR**, 0.386 giây.
Môi trường thực chạy: **Python 3.14.4**, không có python3.12 hoặc distribution mcp.
Không cài package, không inference, không thực thi Linux diagnostic tools.

| Yêu cầu | Bằng chứng offline |
|---|---|
| Connect/disconnect | Idempotent, reconnect, concurrent disconnect |
| Lifecycle | Same-task enter/exit, nested cleanup order |
| Discovery | Schema passthrough, explicit cursor, bad/duplicate/stale entries |
| Mock call | Normalized content/structured/isError, SDK dump aliases |
| Server disconnect | EOF/connection exception; close transport |
| Timeout | Connect/call timeout, write-named mock không retry |
| Cancellation | Connect/call/idle, propagate cancellation sau cleanup |
| Malformed result | Wrong shape/types, non-JSON, SDK output-schema RuntimeError |
| Cleanup | Normal/cancel/error paths, cleanup failure và cleanup budget exhaustion |
| Authorization | Default deny, discovery không tự cấp quyền |
| SDK wiring | Exact-version gate, streams, env, stderr, request timeout |
| Không replay | Một call khi disconnect/timeout/InputRequired |

**SKIP ở lượt ban đầu:** test_local_mock_server. Tại thời điểm đó chưa có bằng chứng runtime stdio với SDK thật,
chưa kiểm chứng Python 3.12. Có sẵn echo fixture và hướng dẫn opt-in, nhưng không
báo “MCP integration PASS”. Unit doubles không thể chứng minh SDK runtime behavior.

## Còn chờ review và bàn giao

- Thủy chốt launch/handshake và registry đầy đủ input/output schemas + $ref bundling;
  correlation IDs, CallToolResult mapping, error/cancellation/retry semantics.
- Chốt cursor binding, timestamp/provenance, 64 KiB wire bytes/collector cap và
  truncated/partial (năm quyết định từ SCRUM-11 vẫn thuộc SCRUM-12).
- Trang xác nhận môi trường Python 3.12, UID/hardening/env, logging của SDK;
  SDK release yêu cầu Python >=3.10 nên VM Python 3.8 không phù hợp baseline.
- Hiền chạy optional SDK stdio test khi môi trường mục tiêu sẵn sàng, sau đó bổ
  sung adapter mapping/input validation theo contracts đã review. Chưa tích hợp
  SCRUM-23, reasoning loop, approval hoặc privileged helper.

Giới hạn: timeout cooperative, không hard wall deadline; cleanup timeout báo rõ
và giữ owner reference. Cap sau SDK parse không ngăn raw oversized frame; chưa
kiểm chứng SDK internal logging với malformed real peer. Không đảm bảo cancellation
đã dừng side effect phía server. Đây là phần độc lập chuẩn bị tích hợp, chưa phải
client sẵn sàng xử lý dữ liệu production. Xem README để đối chiếu official sources.

## Bổ sung — SDK runtime smoke trên Python 3.14.4

Hiền đã cho phép dùng Python 3.14.4 cho smoke tạm thời. **Không thay thế baseline
Python 3.12**, không xác nhận hoàn tất tích hợp contracts.

### Môi trường thực tế

- Python 3.14.4; venv riêng `.venv-scrum22/`, include-system-site-packages=false.
- mcp **2.3.0**, mcp-types **2.3.0**, anyio **4.16.0**, pydantic **2.14.2**,
  jsonschema **4.26.0**, pip trong venv **26.3**.
- Distro thiếu ensurepip: lệnh tạo venv ban đầu không bootstrap được pip. Đã dùng
  get-pip.py từ bootstrap.pypa.io, chạy bằng interpreter của venv rồi cài SDK từ
  pypi.org. Không cài uv, không dùng global pip để cài package, không sửa Python hệ
  thống. System pip vẫn 25.1.1; system interpreter vẫn không có distribution mcp.
- `pip check` trong venv: No broken requirements found.

### Kết quả các lượt mới

| Lượt | PASS | FAIL/ERROR | SKIP | Thời gian |
|---|---:|---:|---:|---:|
| Offline suite trong venv | 39 | 0 | 1 (opt-in) | 0.385s |
| Stdio smoke thật trong sandbox | 0 | 1 (TIMEOUT initialize) | 0 | 12.062s |
| Direct official SDK probe trong sandbox | — | initialize timeout | — | timeout 5s + cleanup |
| Cùng stdio smoke thật ngoài sandbox | 1 | 0 | 0 | 1.881s |

Ở môi trường cho phép stdio subprocess, **40 test cases phân biệt đã PASS**
(39 offline + 1 integration). Không che lượt thất bại trong sandbox. Không gọi
Ollama, diagnostic tools, server production hoặc contracts PR #6.

### Phân tích timeout

Client wrapper bị TIMEOUT trong sandbox. Probe tối thiểu dùng trực tiếp
stdio_client + ClientSession.initialize của SDK, không qua wrapper, cũng timeout.
Không thay source Client/fixture/budget, cùng integration test chạy ngoài sandbox
PASS. Vì vậy đã khoanh vùng khác biệt vào sandbox/stdio của môi trường thực thi;
chưa xác định syscall/policy cụ thể, không kết luận lỗi Python 3.14 hoặc SDK API.
Không sửa contracts hoặc tăng timeout để làm test PASS.

### Bằng chứng smoke thành công

- SDK tạo đúng một subprocess fixture; initialize xong mới cho `connected=True`.
- Discovery tìm thấy mock_echo và schema do SDK tạo từ fixture.
- Call mock_echo với dữ liệu giả, structured_content chuẩn hóa đúng
  `{"sample":"synthetic-only"}`; content có text block; is_error=false.
- Disconnect hoàn tất, connected=false; process có returncode (SDK đã reap).
- Snapshot /proc trước các lượt và kiểm tra sau cùng đều không còn process
  mock_stdio_server.py. Fixture không spawn process con.
- Không có lỗi lifecycle/cancel-scope/cleanup ở lượt thành công. Timeout sandbox
  được đóng session; không quan sát orphan sau các lượt lỗi. Cancellation logic
  vẫn được kiểm bằng offline tests; chưa có bài cancellation riêng với server thật.
- Asyncio debug báo cold import SDK làm owner task mất khoảng 0.7s trước await;
  không phải timeout hoặc lỗi cleanup. Không tuyên bố hard wall deadline.

### File thay đổi trong lần kiểm chứng này

1. tests/mcp_client/test_stdio_optional.py: bổ sung theo dõi **process thật**, kiểm
   connected, normalized content và returncode sau cleanup; không mock transport.
2. tests/mcp_client/README.md: hướng dẫn venv Python 3.14 tạm thời, offline/stdio.
3. tests/mcp_client/REPORT.md: giữ lịch sử, bổ sung kết quả và giới hạn runtime.

Không cần sửa source MCP Client hoặc fixture sau runtime smoke. Venv là artifact
local được Git ignore, không đưa vào PR. Git vẫn có đúng 10 file SCRUM-22 untracked,
không staged/modified tracked; không thay đổi worktree PR #3/#4.

### Readiness

Có thể chuẩn bị **Draft PR cho phần contract-independent** sau khi Hiền cho phép;
chưa tạo PR/commit/push trong nhiệm vụ này. Chưa đủ để tuyên bố toàn bộ SCRUM-22 Done.
Cần trên Python 3.12: tạo venv riêng đúng SDK pin, kiểm dependencies, chạy lại 39
unit tests + real stdio smoke (bao gồm startup/initialize/discovery/call/cleanup),
và bổ sung real timeout/cancellation/disconnect cases trước nghiệm thu đầy đủ.
Còn chờ contracts/manifest/output policy từ SCRUM-12; các giới hạn output cap sau
parse, logging SDK và hard wall deadline trong README chưa thay đổi.

## Chuẩn bị Draft PR SCRUM-22

Các trạng thái Git/untracked trong những mục trước là snapshot lịch sử trước khi
chuẩn bị commit. Review 10 file không phát hiện credentials hoặc đường dẫn cá nhân;
venv được ignore và nằm ngoài danh sách stage. Không phát hiện system mutation
trong implementation/fixture; executable stdio chỉ nhận từ trusted config.

Chạy lại offline trước commit trên Python 3.14.4 + mcp==2.3.0 trong venv:
**39 PASS, 1 optional SKIP, 0 FAIL/ERROR**, 0.374s. Đặt RUN_MCP_STDIO_SMOKE=0 để
không chạy thêm subprocess/inference trong lượt chuẩn bị PR. Bằng chứng integration
vẫn là lượt real stdio PASS 1.881s được ghi phía trên, không chạy lại ở bước này.

Draft chỉ bao gồm contract-independent foundation. Python 3.12 baseline,
real cancellation, SCRUM-12 contracts và LLM Adapter integration còn pending;
không có hard wall deadline, chưa chạy Linux tools thật. Giữ nguyên tất cả giới
hạn trong README; không đề xuất merge hoặc chuyển Jira Done trong nhiệm vụ này.

## Bổ sung — Baseline Python 3.12 (04/11/2026)

### Môi trường và bảo toàn

Worktree/branch đúng `linux-log-agent-scrum22` / `feat/SCRUM-22-mcp-client`, HEAD
vẫn `00fb81e36a80855e58c38b6cbcbdbcc458553ff2`. Working tree sạch lúc bắt đầu.
Không có Python 3.12 sẵn. Trước khi tải/cài đã thông báo phương án uv riêng,
ước lượng 60–100 MB tải, 300–600 MB đĩa. Cài uv **0.13.7** unmanaged vào thư mục
task ngoài repo, không modify shell/profile/PATH; cài CPython **3.12.16** với
`uv python install 3.12 --no-bin` (archive Python 32.9 MiB). Tạo venv riêng
`.venv-scrum22-py312/`, không system-site-packages; không sửa venv Python 3.14 cũ.

Packages thực đo: **mcp 2.3.0**, mcp-types 2.3.0, anyio 4.16.0, pydantic 2.14.2,
jsonschema 4.26.0. SDK cài bằng uv pip với explicit venv interpreter, không global
pip. System Python vẫn 3.14.4. Nguồn runtime: Astral python-build-standalone, được
uv quản lý; chưa kiểm chứng VM/hardening môi trường triển khai của Trang.

### Kiểm thử ban đầu và lỗi tái hiện

- Offline: 39 PASS, 4 opt-in SKIP, 0 FAIL/ERROR (43 discovered, 0.379s).
- Real SDK suite: normal call và real cancellation PASS; disconnect FAIL và timeout
  ERROR (4 tests, 6.947s). Tất cả subprocess đã được cleanup kể cả lượt thất bại.
- Các integration tests chạy ngoài sandbox đã biết chặn stdio của phiên làm việc;
  không đổi sandbox policy, không chạy production MCP server.

Hai root causes:

1. **Client error normalization:** SDK 2.3.0 dùng MCPError với code -32000 cho mất
   kết nối, nhưng wrapper trước đây đổi mọi MCPError thành PROTOCOL_ERROR.
   Sửa mapping -32000 → DISCONNECTED, -32001 → TIMEOUT; các code khác giữ
   PROTOCOL_ERROR. Đã đối chiếu constants/property từ SDK thật đã pin, không tự
   tạo error schema dự án và không lộ raw error text.
2. **Client timeout configuration:** ClientSession dùng request_timeout làm default
   nên initialize cũng bị giới hạn 0.5s dù connect_timeout=10s. Đây không phải lỗi
   API SDK hoặc fixture. Đổi SDK default sang max(connect_timeout, request_timeout),
   wrapper tiếp tục áp hai budget độc lập; call_tool vẫn có SDK timeout riêng.
   Không tăng request budget để vượt test, không thêm hard wall deadline.

### Kết quả sau fix

| Suite | PASS | FAIL/ERROR | SKIP | Thời gian |
|---|---:|---:|---:|---:|
| Offline toàn bộ, Python 3.12.16 | 42 | 0 | 4 opt-in | 0.415s |
| Real SDK stdio, Python 3.12.16 | 4 | 0 | 0 | 5.566s |

**46 test cases phân biệt đã PASS** qua hai lệnh. Không cộng SKIP thành PASS.

- Normal flow: subprocess startup, initialize, discovery, mock_echo arguments,
  structured/content/isError normalization, disconnect và process reap đều đạt.
- Real cancellation: chờ marker wait_started từ handler server thật trước khi
  cancel caller task; caller nhận CancelledError; server ghi wait_cancelled,
  không wait_completed; client disconnected và subprocess có returncode.
- Real timeout: handler wait_started, request 0.5s trả TIMEOUT, không hoàn tất tool;
  initialize vẫn dùng connect budget; subprocess được thu hồi.
- Real disconnect: mock tự exit(23), client trả DISCONNECTED, returncode=23,
  đúng một marker disconnect_started; không reconnect/retry.
- Từng integration test chỉ sinh một fixture process và kiểm returncode sau
  disconnect/cleanup; fixture không sinh children. Marker tạm không chứa payload,
  secret hoặc reasoning và được TemporaryDirectory tự thu hồi.
- Asyncio debug có cảnh báo cold SDK import ~0.7s, không phải lifecycle failure.

Regression offline thêm ba tests cho SDK CONNECTION_CLOSED, REQUEST_TIMEOUT và
protocol error khác; SDK binding test kiểm shared timeout ceiling không ngắn hơn
connect budget. Fixture chỉ thêm mock_wait và mock_disconnect cho test, không phải
contracts mới hoặc tools production.

### File thay đổi, review và trạng thái

8 file trong SCRUM-22:
- src/agent/mcp_client/client.py
- src/agent/mcp_client/sdk.py
- tests/mcp_client/test_client.py
- tests/mcp_client/test_sdk_binding.py
- tests/mcp_client/test_stdio_optional.py
- tests/mcp_client/mock_stdio_server.py
- tests/mcp_client/README.md
- tests/mcp_client/REPORT.md

Cần cập nhật PR #7 bằng hai fixes, regression tests và bằng chứng baseline này sau
khi Hiền review/cho phép commit-push. Chưa cập nhật PR trong lượt này. Không stage,
commit, push, merge hoặc đổi Jira; không sửa PR #6/contracts, worktree khác.

Không còn blocker “Python 3.12 baseline chưa chạy” hoặc “chưa real cancellation”
trong phạm vi synthetic stdio tests. Vẫn cần SCRUM-12 finalized contracts, LLM
Adapter integration, SDK logging/redaction review, pre-parse output bound, và
VM/deployment validation. Không thay đổi phạm vi mock thành nghiệm thu Linux tools
thật hoặc bảo đảm hủy side effect bất kỳ. Cooperative timeout/không hard wall
và không tự retry write vẫn giữ nguyên.

Kiểm tra cuối: không còn PID mock_stdio_server.py. Dung lượng file thường đo được
sau cài: task runtime/uv khoảng 134.1 MiB, venv 3.12 khoảng 33.8 MiB (không tính
symlink như bản sao, không phải filesystem allocated blocks). Không có đường dẫn
cá nhân trong 8 file thay đổi; index rỗng và HEAD chưa đổi. Benchmark worktree vẫn
b2e6ac8 với 53 untracked/0 tracked changes; Adapter vẫn 8f4c146, clean.
Marker wait_cancelled là quan sát handler bị hủy, không phải cancellation ACK
contract chính thức; semantics ACK/execution_unknown vẫn chờ SCRUM-12.

## Chạy lại trước cập nhật Draft PR #7 (04/11/2026)

Review đúng 8 file SCRUM-22, không phát hiện blocker mới, credentials hoặc đường
dẫn cá nhân; venv/cache/temp artifacts nằm ngoài tập stage. Python 3.12.16, MCP
SDK 2.3.0 trong venv riêng. Offline: **42 PASS, 4 optional SKIP**, 0 FAIL/ERROR,
0.397s. Real SDK stdio ngoài sandbox: **4/4 PASS**, 0 FAIL/ERROR/SKIP, 5.543s.
Initialize/discovery/call/normalization, cancellation/timeout/disconnect và cleanup
đều đạt; kiểm tra sau suite không còn PID fixture. Không chạy inference hoặc
Linux tools thật. Các mục chưa commit/push/PR trong các phần trên là snapshot
lịch sử của lượt triển khai/kiểm chứng; lần này Hiền đã cho phép commit và cập
nhật Draft PR. Không thay đổi giới hạn còn lại hoặc nghiệm thu toàn bộ SCRUM-22.
