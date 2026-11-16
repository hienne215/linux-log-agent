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
