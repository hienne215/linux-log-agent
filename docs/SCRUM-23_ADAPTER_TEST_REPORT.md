# SCRUM-23 — Báo cáo phần LLM Adapter độc lập

Ngày 02/11/2026; Hiền, Member B. Bản phục vụ PR phần adapter độc lập, chưa tích hợp MCP.

## Git và phạm vi

- Nhánh mới `feat/SCRUM-23-llm-adapter`, worktree riêng, base `origin/main` tại `7d3e21f012ae38324ab01dab4c33efe1138ba05e`.
- Khi bắt đầu: PR#1/#2 đã merge, PR#3 vẫn Open. Không cherry-pick benchmark hoặc chuyển nhánh checkout SCRUM-14.
- Đọc ARCHITECTURE, SECURITY trên main và báo cáo full Instruct SCRUM-14 (18/18, median3.17s). Không lấy schemas benchmark làm MCP contracts.
- Source thuộc Member B ở `src/agent/llm/`; API provider-neutral, không import MCP SDK; HTTP non-streaming loopback bằng standard library, jsonschema cho validation.

## Files tạo mới

- `src/agent/__init__.py`: package, không startup side effects.
- `src/agent/llm/interface.py`: Message/ToolDefinition/ToolCall/Completion/Usage, LLMAdapter Protocol và AdapterError.
- `src/agent/llm/ollama.py`: transport, schema validation, filtering, normalization và policy.
- `src/agent/llm/__init__.py`: public API.
- `src/agent/llm/requirements.txt`: dependency jsonschema, không cài package trong tác vụ.
- `src/agent/llm/README.md`: cách chạy, API, defaults, limitations và handoff với Thủy.
- `tests/llm/test_ollama.py`: mocked HTTP/transport tests.
- Tài liệu báo cáo này.

## Kiểm thử

Lệnh từ gốc worktree:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests/llm -p 'test_*.py' -v
```

**Kết quả sau review: 32/32 tests PASS**, Python3.14.4, jsonschema4.19.2. Chạy hoàn toàn offline, không gọi inference hay MCP; không đo accuracy/latency thật của adapter. Elapsed unittest dưới1s, không phải thời gian model.

| Nhóm | Bằng chứng |
|---|---|
| Request và history | Defaults đúng, không think, schema không bị thay đổi; assistant/tool history được chuyển đúng |
| Native/tool/text | Validate đúng native call, giữ provider ID; text JSON không bị nâng thành tool call; require_tool từ chối text-only |
| Validation | Unknown tool, extra/missing/wrong-type/enum args, nested bool-vs-int, duplicate names, schema sai và external refs bị chặn; local refs hoạt động |
| Normalization/usage | Missing metrics -> None; tokens/s chia đúng nanoseconds; zero-duration không chia0; metrics sai/overflow bị từ chối |
| Output policy | Incomplete/length/thinking/empty/multiple calls/overbudget bị chặn |
| HTTP | Request POST đúng, bounded read, proxy/redirect bị tắt;404/500/refused/timeout/incomplete HTTP được chuẩn hóa, không retry |
| Malformed/data | Bad JSON/duplicate keys/nonfinite/UTF-8 sai, invalid unicode; redact JSON/plaintext/Bearer và reject key material/secret arguments |
| Limits/config | Request UTF-8 bytes và response caps; không public endpoint, URL credential hoặc cấu hình vượt baseline |

## Giới hạn và bàn giao

- Timeout urllib là timeout I/O, không phải hard wall deadline; chưa kiểm slow-drip server, cancellation hoặc transport lifecycle async. Cần phối hợp lớp budget khi tích hợp.
- Chỉ một native call/lượt; metrics không có thì không bịa; không parse prose để cứu tool call. Không tự thực thi tool, cấp approval hoặc retry.
- Redaction là pattern filter thứ hai, chưa bảo đảm phát hiện mọi secret/IP; terminal/Markdown escaping thuộc renderer. Không lưu raw response hoặc thinking.
- JSON Schema Draft2020-12 đã được chọn cho interface ứng dụng; chờ Thủy xác nhận dialect và khả năng mapping. Tên/args trong test chỉ là mock fixture.
- Cần chốt với Thủy: registry/schema mapping; Message tool-result và IDs; error/retry/deadline; output policy; redaction/provenance và prompt policy. Chi tiết trong README.
- Chưa tích hợp MCP Client, prompt/version config, Agent loop, privileged helper hoặc live smoke. Không tuyên bố toàn bộ SCRUM-23 Done.

PR chỉ chứa8 file SCRUM-23; không sửa tài liệu Thủy/Trang hoặc README gốc. Không merge hoặc chuyển Jira sang Done. Giữ nguyên checkout gốc và mọi file chưa commit của các nhiệm vụ trước.

## Review trước PR và regression fixes

26 tests ban đầu được chạy lại và pass. Review tái hiện lỗi password JSON có escaped quote khiến redaction bỏ sót phần giá trị và phá JSON. Đã sửa để tiêu thụ đầy đủ escaped string, giữ quoted value; chuẩn hóa control characters trước lọc để không bypass secret/private-key marker; bổ sung Authorization Basic và quoted header. HTTPError body được đóng mà không đọc/echo; malformed input containers được chuẩn hóa thành INVALID_INPUT trước network.

6 regression tests mới kiểm: escaped JSON secret ở cả input/output; Basic/quoted Authorization; control-character bypass; invalid input containers; bounded HTTP read + đóng response; đóng HTTP error body. Tổng32/32 tests offline pass. Rà soát xác nhận source không chứa dispatcher/exec/shell/subprocess hay logging raw secrets/reasoning. Redaction vẫn là lớp lọc phụ, không bảo đảm nhận biết mọi secret.
