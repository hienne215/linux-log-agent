# SCRUM-23 — LLM Adapter (phần độc lập)

Interface Python không phụ thuộc MCP SDK; backend hiện tại là Ollama `/api/chat` non-streaming. Một lần `complete` chỉ tạo một HTTP request, không có reasoning loop, retry, tool execution hoặc approval. Vị trí `src/agent/llm/` theo cấu trúc Member B trong `docs/project-structure.md`; không sửa module Thủy/Trang.

## API

- `LLMAdapter.complete(messages, tools=(), require_tool=False) -> Completion` là Protocol cho consumer; không cần kế thừa class cụ thể.
- `Message(role, content, tool_calls=(), tool_name=None)`: system/user/assistant/tool. Tool results trong history là text đã lọc, không phải MCP CallToolResult. Adapter không tự thêm prompt hoặc chuyển dữ liệu tool thành system instruction.
- `ToolDefinition(name, description, parameters)`: JSON Schema từ registry tin cậy. Adapter chỉ chuyển sang Ollama function definitions; không khám phá hay tự tạo MCP contracts. Dùng Draft2020-12; local refs được phép, external refs bị từ chối. Schema draft khác cần quyết định tích hợp, không tự đổi.
- `Completion`: `kind=text|tool_calls`, `text`, tuple `ToolCall(name, arguments, provider_id)`, `Usage`, `latency_seconds`, model, done_reason, redacted. Native call giữ object arguments; không parse JSON trong prose, không coercion JSON-string thành dict. Text và tool call cùng xuất hiện được giữ, nhưng chỉ native calls là đề xuất tool.
- `provider_id` là ID từ Ollama, có thể thiếu; không phải logical call/attempt/JSON-RPC ID của MCP. Không sinh approval hoặc action ID.
- Usage chuẩn hóa prompt/output tokens, generation seconds, provider total seconds, tokens/s. Thiếu metrics -> None; wall latency dùng monotonic quanh HTTP request, khác provider duration. Không xuất thinking/raw response. Một kết quả trả về chỉ có nghĩa đã validate, không có nghĩa được phép thực thi.

## Cấu hình mặc định

`qwen3:4b-instruct-2507-q4_K_M`, endpoint `http://127.0.0.1:11434`, num_ctx4096, num_predict256, temperature0, num_thread4, stream=false. **Bỏ hoàn toàn trường think**; không gọi `/api/show`, pull model hoặc thay service. Không gửi seed cố định vì đây là adapter, không phải benchmark replay. Model/endpoint chỉ do code/config tin cậy cấp, không lấy từ model/log.

`OllamaConfig` giới hạn context/output/threads không vượt cấu hình baseline; endpoint chỉ literal loopback 127.0.0.1 hoặc ::1 cổng11434, không proxy/redirect/DNS. Max request128KiB, response256KiB; đây là giới hạn HTTP của adapter, **không phải giới hạn64KiB MCP**. Timeout mặc định45s là timeout I/O của urllib, chưa phải hard wall deadline chống server gửi chậm liên tục. Caller cần budget/cancellation ở lớp điều phối sau; adapter không tự kill Ollama.

## Chính sách đầu vào/đầu ra và lỗi

Tool names phải thuộc tập tools consumer truyền, arguments validate đúng schema (kể cả nested/enum/required/additionalProperties). Consumer vẫn phải giới hạn registry/read-write theo policy và validate lại ở MCP Server. Cờ tool annotation hoặc lời model không thể cấp quyền.

Chấp nhận tối đa một native call/lượt. `require_tool=True` từ chối text-only; mặc định cho phép text. Từ chối done=false, done_reason khác stop (gồm length), thinking nonempty, marker think, output rỗng hoặc số tokens báo vượt cap. Đây là policy MVP cần review trước khi hỗ trợ parallel calls. Không trả danh sách call đã validate một phần khi cả response lỗi.

`AdapterError.code` có các nhóm:

| Nhóm | Codes |
|---|---|
| Input/config/schema | INVALID_CONFIG, INVALID_INPUT, INVALID_SCHEMA, INVALID_ARGUMENTS, INPUT_LIMIT |
| HTTP/provider | TIMEOUT, CONNECTION_ERROR, MODEL_NOT_FOUND (HTTP404 tại /api/chat), HTTP_ERROR, API_ERROR |
| Response/policy | MALFORMED_RESPONSE, OUTPUT_POLICY, OUTPUT_LIMIT, DATA_POLICY |

Lỗi không kèm raw HTTP body, headers, arguments hoặc credentials. HTTP404 giả định model không có tại API chuẩn đã cấu hình; nếu chạy sai server ở port11434 thì cần kiểm tra cấu hình thủ công. Không fallback sang model/provider khác và không retry.

Bộ lọc thứ hai loại control characters trước khi redact mẫu password/api_key/access_token/secret/Authorization/Bearer trong text; giữ chuỗi JSON quoted hợp lệ khi redact giá trị có escape, từ chối private-key marker. Tool arguments có secret bị **từ chối**, không thay giá trị rồi gọi nhầm tool. Bộ lọc pattern không phát hiện mọi loại secret hoặc IP; collector phải lọc trước, policy IP/pseudonym còn chờ nhóm. Không log request/response. CLI/report vẫn phải escape Markdown/HTML/terminal đúng ngữ cảnh, và không lưu reasoning. Không tuyên bố prompt injection đã được giải quyết chỉ nhờ redaction/schema.

## Unit tests offline

Python>=3.10 và `jsonschema` (khai báo trong `requirements.txt` cùng thư mục). Môi trường kiểm tra đã có jsonschema4.19.2; tác vụ này không cài package. Nếu môi trường khác thiếu, cần chuẩn bị dependency trước chạy.

Từ gốc worktree:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests/llm -p 'test_*.py' -v
```

57 tests dùng mocked HTTP bytes/errors và transport, không kết nối Ollama/MCP hoặc thực thi tools. Case `service_status/lab.service` chỉ là fixture nội bộ của test, không thay contracts SCRUM-12. Test validate request giữ baseline options và không có think; tests cũng kiểm cả adapter normalization lẫn HTTP transport layer (proxy, redirects, byte cap, timeout, refused connection,404/500, truncated HTTP).

## Cần thống nhất với Thủy

1. Tập schema/tool names chính thức, JSON Schema dialect, descriptions đáng tin cậy và mapping discovery MCP -> ToolDefinition. Thủy giữ contracts authoritative, không lấy fixture test này làm contract.
2. Chuyển MCP result sang Message(role=tool), encoding envelope/evidence, correlation với native provider_id và logical/attempt IDs; không đưa token/grant vào history.
3. Mapping AdapterError sang lỗi ứng dụng; quyền quyết định retry, cancellation/deadline và giữ budget ở caller.
4. Policy một call/lượt, text-only vs require_tool, extra arguments và format validation; không nới schema để chiều output model.
5. Redaction/pseudonym và nơi giữ provenance; prompt version/system policy thuộc lớp adapter consumer được review, không nhận policy từ log.

SCRUM-22 chưa được triển khai ở đây. SCRUM-23 chưa thể coi hoàn tất tích hợp: mapping MCP, stub integration, prompt/version config và live smoke sau khi contracts/client sẵn sàng vẫn còn. Benchmark SCRUM-14 là bằng chứng lựa chọn model, không phải test của adapter mới.

Tham khảo API: https://docs.ollama.com/api/chat. Cấu trúc response được xử lý theo fixtures và baseline đã đo, chưa xác minh tương thích adapter bằng inference thật.

## Lịch sử review ban đầu

Đã sửa lỗi redaction JSON có dấu nháy escape (lọc thiếu secret và phá cấu trúc JSON), bổ sung Authorization Basic/quoted credentials và chặn bypass bằng control characters. HTTP error response được đóng mà không đọc body; input containers sai trả AdapterError trước HTTP. Tổng32 tests, gồm6 regression tests mới; không thay cấu hình hoặc policy baseline. Giới hạn hard wall deadline, MCP và live inference vẫn giữ như trên.

## Khắc phục review blocker về redaction

Bộ lọc nằm trong `redaction.py`, được Adapter dùng cho cả text và kiểm tool arguments. Không dựa vào `\b` trước từ khóa bên trong tên biến: phân loại toàn identifier, tách underscore/hyphen/dot/camelCase, chuẩn hóa Unicode NFKC và nhận diện các hậu tố nhạy cảm phổ biến. Bao phủ password/passwd/secret/token/authorization/api_key, gồm db_password, client_secret, AWS_SECRET_ACCESS_KEY, secret_key, access_token và refresh_token.

- JSON object/list, JSON-encoded JSON strings, các fragment JSON trong log và Unicode-escaped keys được parse/duyệt có giới hạn. Giữ JSON hợp lệ và không thay nội dung không nhạy cảm; chỉ reserialize phần đã lọc. Các field text hỗ trợ `key=value`, `key: value`, quoted values, `password is value`; giá trị unquoted là một scalar tới delimiter/whitespace, giá trị nhiều từ phải quote hoặc dùng JSON.
- Text/log: redact trước HTTP; quoted escapes/multiline được tiêu thụ đầy đủ. Tool arguments: từ chối cả sensitive key (kể cả rỗng) hoặc chuỗi cần redact với DATA_POLICY, không tự sửa arguments.
- CLI `-pVALUE`: chỉ xem là password khi executable thuộc MySQL/MariaDB đã biết; lệnh curl/python/grep/nginx... có ý nghĩa flag khác được giữ nguyên. Bare `-pVALUE`, command chưa biết hoặc tổ hợp shell không phân tích chắc chắn bị DATA_POLICY; không chạy shlex output. Không coi mọi từ bắt đầu bằng -p là password. `--password=VALUE` cũng được lọc như assignment. Các CLI grammar khác chưa được khẳng định hỗ trợ.
- Empty text values/JSON empty string/null không bị hút sang record kế tiếp; mentions thông thường của token/password và keys tokenizer/passwordless giữ nguyên. Sensitive-key metadata có thể bị lọc dù không phải credential (ví dụ token_count); không tự tạo ngoại lệ theo schema chưa được nhóm duyệt.
- JSON lỗi/duplicate keys, quoted secret thiếu dấu đóng, private-key marker, vượt1MiB hoặc quá16 bước đệ quy bị DATA_POLICY. Không log body/exception chứa giá trị. Các formats/encoding ngoài phạm vi (base64, ciphertext, Unicode confusables tùy ý, unlabeled secrets) không có bảo đảm nhận biết; collector vẫn phải lọc trước.

Kết quả: **57/57 offline unit tests PASS**, gồm32 tests cũ +25 regression tests. Không inference mới;4/4 live smoke là bằng chứng lịch sử trước bản sửa này, không chứng minh redaction mới đã qua live inference. Hard wall deadline/cancellation và MCP/Agent integration vẫn ngoài phạm vi.

## Async integration candidate — 13/11/2026

Worktree/branch `linux-log-agent-async-llm` / `feat/SCRUM-23-async-adapter`, base
main `990f41f68c8ef13218bf825bd57ff233a8c82f1c`. Async PR độc lập vào main;
Coordinator/Bridge/CLI chỉ là dependency của composed tests và demo tương lai.
Main có Adapter, chưa có Coordinator/Bridge/CLI trong Draft PR #7/#8/#9. Composition
runner lấy **exact PR #9 commit ab05df010a5dc95faf2adbfa6ee997387d2dbc1e** vào vùng
Git archive tạm và overlay LLM code/tests mới; không sửa hoặc merge dependency.

Thêm ba modules, không thay API sync hoặc thêm dependency/framework:

- `async_adapter.AsyncOllamaAdapter.complete(...)`: await được, trả Completion cũ.
  Single-flight, tối đa6attempts/120s từ lúc khởi tạo; request timeout bao gồm startup.
  Text được lọc trước IPC; worker environment tối thiểu không kế thừa credentials.
- `_async_worker`: một request/process, Adapter sync cũ thực hiện HTTP/validation.
  Bounded JSON IPC tối đa1MiB mỗi chiều, không pickle/unbounded communicate/stderr
  logging. Default model/options giữ nguyên; test seam chỉ nhận trusted argv.
- `decision_source.AsyncDecisionSource.choose(question, observations)`: tương thích
  Protocol của Coordinator (lazy import dependency PR #8); native call → Decision,
  text phải là JSON đúng `{conclusion, citations}` với refs hiện có. Không dùng JSON
  trong text làm tool call, không tự gắn mọi refs hoặc xác minh semantic claim.

Registry/schema/read-only do ứng dụng tin cậy cấp, không lấy từ model. Source không
có MCP wire fields/IDs/binder/approval. Observation không chứa trusted tool_name/call
correlation; vì vậy truyền bằng user message có nhãn untrusted data, không tự bịa
role=tool từ source text. Khi contract/binder được chốt có thể dùng ToolMessage thật.

### Sử dụng seam khi đã compose dependencies

```python
from agent.llm.async_adapter import AsyncOllamaAdapter
from agent.llm.decision_source import AsyncDecisionSource, run_coordinator

model = AsyncDecisionSource(AsyncOllamaAdapter(seconds=120), trusted_tools,
                            read_only=read_only_names)
# Coordinator/executor/interpret do ứng dụng cung cấp, theo dependency đã review.
coordinator = Coordinator(model, executor, read_only=read_only_names,
                          scope_allows=scope_allows, interpret=interpret, redact=redact)
report = await run_coordinator(coordinator, question)
```

Dùng `run_coordinator`, không chỉ gọi Coordinator.run rồi bỏ model: helper rejoin
cả model lẫn executor. Nếu cleanup chưa xác nhận, c.report.cleanup_pending được đặt,
exception safe CLEANUP_PENDING được trả; phải giữ coordinator/model và gọi
`await model.finish_cleanup(); await coordinator.finish_cleanup()` để rejoin rồi đọc
lại c.report. Không đóng event loop/service trong khi còn owner pending.

### Deadline và cancellation thực sự đảm bảo gì?

urllib sync không có external request abort handle. Không dùng to_thread/shield để
cam kết hủy inference. Dedicated async owner khởi chạy fixed worker, quản lý pipes,
request, terminate/grace/kill và wait; caller không cancel owner. Nếu cleanup budget
hết, owner/process reference còn nguyên và không nhận request mới. Late result bị
bỏ, không normalize/dispatch/MCP hoặc cập nhật decision history. Không tự retry.

Coordinator wait_for có thể cancel choose khi deadline nhỏ hơn adapter budget;
worker vẫn được join bằng cleanup budget riêng. No hard wall SLA: bounded CPU
validation/redaction, process startup và OS teardown có giới hạn thực tế; worker
không dừng được vẫn là cleanup_pending, không tuyên bố đã hủy. Test SIGTERM-ignore
đã kiểm kill+reap. Không chứng minh Ollama backend đã dừng inference khi client
worker bị dừng; usage latency vẫn là HTTP latency của Adapter, không gồm startup.

AsyncLLMError có code/layer an toàn: CONNECTION_ERROR/HTTP_ERROR/WORKER_FAILURE
(transport), MODEL_NOT_FOUND/API_ERROR/OUTPUT_POLICY (model), MALFORMED_RESPONSE
(protocol). LLMTimeout là TimeoutError cho TIMEOUT/DEADLINE_EXCEEDED; caller
cancellation vẫn là CancelledError. Source.last_error và adapter.last_error giữ code,
không raw messages/log/reasoning. Coordinator cũ gom lỗi LLM không phải timeout
thành data_policy; chưa thêm report status/API mới, UI sau này cần đọc safe code này.

### Tests và tái lập

Prepared Python3.12.16/MCP2.3.0/jsonschema4.26.0/referencing0.37.0. Từ root checkout,
với venv chứa dependencies hiện có; Git object PR #9 phải có sẵn (nếu clone mới,
fetch nhánh feat/SCRUM-27-cli-report trước). Không inference hoặc real Linux tools.

```bash
# Unit worker/Adapter, không cần Coordinator trong main
PYTHONPATH=src python -B -m unittest discover -s tests/async_llm -p test_async.py -v
# Async + Coordinator mock slice; immutable dependency composition
PYTHONDONTWRITEBYTECODE=1 python -B tests/async_llm/run_composed_tests.py --suite async_llm
# Tất cả regression; real SDK nhưng server vẫn synthetic
RUN_MCP_STDIO_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 python -B tests/async_llm/run_composed_tests.py
```

Kết quả lượt cuối: **276 PASS,0 FAIL,0 SKIP** =15 async unit/mock +9 Coordinator/
Bridge mock integration +57 Adapter +46 MCP Client +50 Boundary +64 SCRUM24 +35 CLI.
Tests có native tool/usage, conclusion/citations, invalid output no MCP dispatch,
model timeout/disconnect/not-found, startup cancel/repeated cancel, late result,
SIGTERM-ignore kill/reap, cleanup pending/rejoin, environment/redaction, six-turn
cap và lower Bridge budget. Fixture six-turn đã cấu hình Bridge8calls; có test riêng
chứng minh default4calls vẫn binding. Không tăng production budgets để làm tests pass.

Giới hạn: no live Ollama; no Linux collector; no production contracts/binder;
stateless prompt projection chưa là full model history/role=tool integration;
semantic truth và known real-data redaction follow-ups chưa giải quyết. Chờ Thủy
chốt service_status schemas/correlation/evidence; Trang+Hiền chốt target VM, UID/NNP,
permissions, topology loopback/stdio và RAM. Bước demo ngắn nhất sau gate:1native
LLM tool selection →1approved MCP service_status →source evidence thật →report,
không lấy fixture hoặc dựng evidence từ active_state thay provenance của server.

## Review update — async lifecycle và prepared live smoke

Trạng thái ở lần review trước live: **READY FOR DRAFT PR; Live Ollama: NOT RUN**.
Kết quả live mới và lần regression cuối nằm ở phần cập nhật bên dưới.
Review đã tái hiện trước sửa ba NEEDS CHANGE, nay regression PASS:

1. Teardown OSError có thể kết thúc owner trước khi process/I/O tasks được thu hồi.
   Owner nay giữ process/task references, ghi code CLEANUP_FAILED và chờ explicit
   finish_cleanup để thử lại teardown. Không bỏ owner hoặc log raw OSError.
2. Tool-call history arguments chưa được validate trước IPC. Parent nay dùng chính
   validator/_call của Adapter để từ chối arguments chứa secret trước spawn; không
   tự rewrite arguments. Tool descriptions được redact trước IPC.
3. Budget có thể hết trong normalization mà result vẫn được trả. Nay kiểm tra lại
   request/global deadline trước publish Completion, bỏ late result và dừng reuse.

Sync Adapter/HTTPTransport, worker protocol và Coordinator API không đổi. Baseline
trước review là276PASS; sau thêm3regression và6smoke-driver tests: **285PASS,0FAIL,
0SKIP** =18async unit/mock +9Coordinator synthetic +6driver mock +252regression.
Trong96Client/Boundary tests có real SDK stdio synthetic; không phải MCP Linux thật.
Các suites được chạy mới bằng Python3.12.16/MCP2.3.0. Source/tests không có hardcoded
personal paths hoặc import từ worktree khác; composition dùng pinned Git objects.

### Live smoke — cách chạy có kiểm soát

Script `tests/async_llm/live_smoke.py`: mặc định không HTTP/inference; preflight chỉ
đọc version/tags/show và RAM; không gọi chat/generate ở chế độ preflight.
Capabilities metadata không chứng minh thinking bật/tắt. Giữ không truyền think;
Adapter vẫn reject nonempty thinking/think tags.

Chỉ sau khi Hiền phê duyệt riêng, từ root repository với prepared venv:

```bash
# Chỉ metadata, zero inference (đã được kiểm chứng)
PYTHONPATH=src python -B tests/async_llm/live_smoke.py --preflight-only
# Mỗi lần chạy mới cần phê duyệt riêng; không tự retry
PYTHONPATH=src python -B tests/async_llm/live_smoke.py --run-live-two-requests
```

Điều kiện: xác nhận đúng version/model/digest ở preflight, Ollama idle và không
chạy benchmark khác, RAM>=1GiB, giữ terminal/service sống để rejoin owner. Không
đổi server/model, không pull model, không sửa endpoint. Metadata không chứng minh
server idle; cần người vận hành xác nhận trước khi phê duyệt.

Cấu hình: ctx4096,predict256,temp0,thread4,stream=false,omit think;45s/request gồm
worker startup;100s overall adapter budget;max2attempts. Request1 native selection
service_status(unit=nginx.service) synthetic, không thực thi tool. Request2 text JSON
conclusion/citations từ synthetic smoke-e1. Stop ngay nếu case/RAM/API/deadline lỗi,
không retry và không cố chạy request2 sau lỗi request1. Sample RAM mỗi~0.1s, không
thể bảo đảm bắt mọi transient memory dip. Không real logs/MCP/Linux/approval/restart.

JSON output chỉ lưu structural results/usage/latency/RAM và cleanup status, không
raw model content/reasoning. Driver giữ event loop và owner để rejoin nếu cleanup
pending, không thoát ngầm rồi bỏ process; điều đó có thể chờ lâu hơn request budget.
Ctrl+C/force exit không chứng minh server đã dừng inference. Worker reaped chỉ là
client-side cleanup; backend_inference_cancellation luôn NOT VERIFIED. Không tự
rerun nếu timeout; cần kiểm tra backend đã idle trước lần được duyệt tiếp theo.

### Bước tiếp gần demo nhất

Giữ CLI hiện tại là synthetic. Smoke2request đã PASS trong lượt được duyệt riêng
ngày13/11/2026; không tự chạy lại. Tiếp theo nối CLI→AsyncDecisionSource→
Coordinator bằng backend factory tin cậy và error-code presentation, giữ lifecycle
run_coordinator. Có thể kiểm mock path trước, không phát minh wire fields. Live MCP
vẫn chờ Thủy chốt service_status schema/correlation/evidence và collector thật,
Trang+Hiền chốt VM/UID/permissions/topology/RAM. Không đổi nhãn synthetic thành live
hoặc bỏ IPC/UID deployment gates chỉ vì async wrapper/tests đã PASS.


## Final validation — 13/11/2026

- Python3.12.16, MCP2.3.0, jsonschema4.26.0; không thêm runtime dependency.
- Regression mới thực chạy: **285 PASS, 0 FAIL, 0 SKIP** (kết quả chi tiết ở PR).
  Trong đó 8 tests dùng real SDK với synthetic stdio server; không phải Linux MCP.
- Live smoke ở lượt được Hiền duyệt riêng: **2/2 PASS**, đúng một invocation,
  tối đa2attempts, không retry; không chạy lại inference trong final PR review.
- Ollama0.40.13, qwen3:4b-instruct-2507-q4_K_M; ctx4096/predict256/temp0/thread4,
  stream=false, omit think,45s/request. Native service_status(unit=nginx.service)
  đúng schema; conclusion JSON có citation smoke-e1, semantic claim chưa được chấm.
- HTTP latency8.847s/4.616s; tổng wall8.966s/4.750s; prompt/output tokens177/21
  và75/47; generation14.54/13.92tokens/s. Không timeout/API/schema errors;
  worker cleanup confirmed, exit0, không thấy worker còn sót sau chạy.
- Không commit runtime artifacts, machine inventory, raw model content/reasoning
  hoặc log thật. Đây là bằng chứng live LLM trên dữ liệu synthetic, không phải
  end-to-end CLI+real MCP hoặc deployment theo UID/IPC của ARCHITECTURE.
- Client worker cleanup không chứng minh Ollama backend dừng inference khi kill.
  No hard wall SLA; cancellation live chưa thử. Chưa finalized contracts SCRUM-12,
  chưa production binder, approval/helper, auto-remediation hoặc real Linux tools.
  Chưa đủ điều kiện SCRUM-23 Done hay production-ready.
