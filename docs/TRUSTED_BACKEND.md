# Trusted Ollama backend và First Native-Tool Policy

**Live integrated smoke v2 ngày 14/11/2026: PASS cho một scenario active.**
Đây là Live Ollama + Synthetic MCP, không phải Linux diagnostics thật hoặc nghiệm
thu production. Backend mặc định vẫn là mock; không tự fallback hoặc retry.

## Dependencies và interfaces

Stacked development từ PR #11 (`integration/cli-async-mcp-demo`), phụ thuộc chuỗi
#11 → #9 → #8 → #7 và Async Adapter #10. Các dependency còn Draft/Open tại lần kiểm
tra 14/11/2026; không coi code đó đã có trên main.

Application chọn `Backend.MOCK` hoặc `Backend.OLLAMA` qua `create_adapter()`.
Không nhận model, endpoint hoặc worker command từ câu hỏi/model/log. Unknown backend
bị từ chối. Construction không khởi chạy process hoặc gọi HTTP.

Ollama dùng AsyncOllamaAdapter/worker hiện có; subclass nhỏ chỉ thêm RAM gate trước
mỗi request. Không thay public API Adapter, Coordinator, MCP Client hoặc ToolBridge.
Giữ `AsyncDecisionSource`, `run_coordinator()`, SDK stdio và renderer hiện có.

## Policy và giới hạn

- Model cố định `qwen3:4b-instruct-2507-q4_K_M`, endpoint `http://127.0.0.1:11434`.
- `num_ctx=4096`, `num_predict=256`, `temperature=0`, `num_thread=4`, `stream=false`;
  không truyền `think` hoặc `tool_choice` chưa được kiểm chứng cho native API runtime.
- Live chỉ cho `active`: tối đa 2 LLM requests, 1 tool dispatch, 45 giây/request gồm
  worker startup, 120 giây diagnosis gồm initialize/discovery. Cleanup budget riêng.
- Chỉ expose/dispatch `service_status` với `{"unit":"nginx.service"}` theo trusted
  schema; discovery không cấp quyền. Không shell, write tools, restart hoặc approval.
- Mock giữ ba scenarios, tối đa 6 LLM turns và 8 tool attempts. Port-conflict và
  permission-denied chưa được cho phép ở live vì cần hơn 2 turns trong flow hiện tại.
- Khi chưa có evidence, `AsyncDecisionSource.choose()` yêu cầu native tool call và
  dùng `require_tool=True`. Đây là response validation cục bộ, không ép Ollama API
  chọn tool hoặc bảo đảm mọi request tương lai sinh native call. Text/prose hoặc
  JSON mô phỏng tool call bị từ chối bằng `OUTPUT_POLICY`, không chuyển thành proposal.
- Khi đã có evidence, giữ nhánh tool/conclusion và validation citations. Sai output,
  bịa citation hoặc chọn tool thêm ngoài cap thì dừng, không gửi request thứ ba để sửa.
- CLI in allowlisted `source.last_error` ra stderr sau cleanup; không in raw exception,
  provider response, prompts hoặc reasoning. Stdout/report dùng cùng renderer.

Evidence vẫn có provenance `synthetic:integration-demo-v1/...`; mọi report mang nhãn
SYNTHETIC DEMO và `conclusion_verified=false`. Citation hợp lệ chỉ chứng minh ID tồn
tại trong evidence, không chứng minh kết luận đúng về ngữ nghĩa hoặc Linux thật.

## Chạy mock và regression

Từ repository root, dùng Python 3.12 và dependencies `.[mcp]` (MCP SDK pin 2.3.0):

```bash
PYTHONPATH=src python -B -m agent.integration_demo --scenario active \
  --question 'Kiểm tra Nginx fixture' --report backend-mock-active.md
PYTHONPATH=src python -B -m agent.integration_demo --backend mock --scenario port-conflict \
  --question 'Tại sao fixture failed?' --report backend-mock-conflict.md
PYTHONPATH=src python -B -m agent.integration_demo --backend mock --scenario permission-denied \
  --question 'Đọc journal fixture' --report backend-mock-permission.md
PYTHONDONTWRITEBYTECODE=1 python -B tests/integration_demo/run_tests.py
```

Exit codes dự kiến: active 0, port-conflict 0, permission-denied 1 (limited). Default
label là MOCK LLM + SYNTHETIC MCP. Report chỉ ghi `reports/<basename>.md`, không theo
symlink hoặc overwrite, mode 0600. Dùng tên mới nếu đã chạy các lệnh trước đó.

Regression gồm mock HTTP và official SDK synthetic stdio, không gọi Ollama thật.
Tests chọn nhánh factory live nhưng thay worker bằng mock trước complete(); metadata
preflight cũng được mock. Baseline sau sửa policy: 352 PASS, 0 FAIL, 0 SKIP trên
Python 3.12.16/MCP 2.3.0. Kết quả kiểm thử lại trước PR được ghi trong PR description.

## Preflight và giới hạn vận hành live

Chỉ chạy live khi được phê duyệt riêng; các lệnh dưới đây là hướng dẫn, không phải
quyền tự chạy inference hoặc retry sau lỗi:

```bash
PYTHONPATH=src python -B -m agent.integration_demo --backend ollama --scenario active --preflight-only
PYTHONPATH=src python -B -m agent.integration_demo --backend ollama --scenario active \
  --question 'Kiểm tra trạng thái nginx.service' --timeout 120 --report live-synthetic-active-v2.md
```

Preflight đọc RAM/dependency version và metadata `/api/version`, `/api/tags`,
`/api/show` (POST chỉ đọc metadata): 0 inference, 0 MCP subprocess. Endpoint cố định,
không proxy/redirect, 5 giây I/O mỗi endpoint, response tối đa 1 MiB. Pin Ollama
0.40.13, model/digest trong `preflight.py`, Q4_K_M và tools capability. Nếu mismatch
thì dừng để review, không pull model hoặc đổi config để pass. Metadata không chứng
minh thinking tắt hoặc backend idle. Operator phải kiểm tra tác vụ cạnh tranh.

RAM gate trước mỗi request tối thiểu 1 GiB; không continuous monitoring, không cam
kết bắt mọi transient dip. RAM_LOW/RAM_UNAVAILABLE dừng phiên, không mock fallback.
`run_coordinator()` và DemoSession giữ cleanup ownership của model/executor/client;
cleanup_pending giữ owner/event loop để rejoin, không tiếp tục reasoning. Không có
hard wall SLA: cleanup có thể dài hơn diagnosis budget. Timeout không chứng minh tool
chưa chạy; terminate/kill/reap worker không chứng minh Ollama backend dừng inference.

## Bằng chứng live lịch sử — không phải lần inference mới khi tạo PR

Ngày 14/11/2026, v1 trả text-only ở request đầu, không dispatch; policy sau đó được
sửa và regression offline bổ sung. V2 được phê duyệt riêng và chạy đúng một invocation:

| Quan sát v2 | Kết quả |
|---|---|
| Runtime | Python 3.12.16, MCP 2.3.0, Ollama 0.40.13 |
| Preflight | PASS; 0 inference, 0 MCP subprocess; model/digest/Q4_K_M đúng |
| Request 1 | Native service_status(unit=nginx.service); schema validation PASS |
| MCP | Official SDK stdio, 1 synthetic tool dispatch |
| Evidence/citation | active-service_status; synthetic:integration-demo-v1/active/service_status |
| Request 2 | Conclusion JSON được validate; citation tồn tại |
| HTTP latency | 16.052s / 10.032s |
| Prompt/output tokens | 278/21 và 291/28 |
| Tổng | 2 LLM requests, 1 dispatch, 28.188s; CLI exit 0 |
| Report | Stdout khớp từng byte; mode 0600; SYNTHETIC DEMO |
| Cleanup | Owners clear, 0 pending tasks; 2 workers và synthetic server exit 0 |

Trace quan sát: RECEIVED → ANALYZING → COLLECTING → ANALYZING → CONCLUDED → REPORTED.
Report chỉ diễn giải fixture active và HTTP health chưa được kiểm tra, không xác nhận
Linux thật. Bằng chứng raw/runtime được giữ ngoài Git; chỉ tổng hợp cần thiết ở đây.

**Một lần smoke không phải benchmark độ tin cậy**, không tách riêng tác động prompt
và policy bằng A/B. Chưa có Linux collector thật, production binder, finalized
SCRUM-12 contracts hoặc VM acceptance. Redaction pattern có giới hạn; chỉ dùng dữ
liệu synthetic đã review, không log cá nhân/secrets. Chưa có approval/helper,
remediation hoặc backend cancellation guarantee. Chưa Done/production-ready.
