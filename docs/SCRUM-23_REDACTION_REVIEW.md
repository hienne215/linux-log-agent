# SCRUM-23 — Khắc phục Request Changes của Trang

Ngày 02/11/2026. Phạm vi: bộ lọc dữ liệu, tests và tài liệu của Member B trên PR#4. Không MCP contracts, Agent Loop hoặc hard deadline.

## Root cause và tái hiện

Review QTrang2826 chỉ ra secret lọt qua HTTP payload. Regex cũ dùng word boundary trước tên khóa nhưng underscore là word character, nên không thấy thành phần nhạy cảm trong identifier. Danh sách khóa thiếu generic token và các biến thể prefixed/suffixed. Regex text cũng không duyệt JSON structure hoặc JSON-encoded strings; tool arguments chỉ so tập key chính xác. CLI short flag và prose `password is VALUE` chưa có ngữ nghĩa riêng.

Trước sửa:17 regression test methods cho các format ghi nhận37 assertion failures, dùng dữ liệu giả. Báo cáo chỉ ghi số lượng/loại lỗi, không plaintext credential. Không sử dụng credentials thật.

## Thay đổi

1. Tách `src/agent/llm/redaction.py`: phân loại toàn key theo separators/camelCase/NFKC và hậu tố phổ biến; parse JSON objects/lists, nested/encoded strings, Unicode-escaped keys và JSON fragments trước khi lọc text.
2. Parse đầy đủ value quoted/escaped/multiline, giữ valid JSON; không để empty assignment hút record tiếp theo. Hỗ trợ `key=value`, colon, quoted/JSON/escaped JSON, prose password-is và CLI long assignment.
3. `-pVALUE` phụ thuộc executable MySQL/MariaDB; context không biết/bare/shell compound bị DATA_POLICY. Known non-password flag contexts được giữ nguyên. `shlex` chỉ tokenize, không execute.
4. Adapter dùng cùng key predicate để từ chối native tool arguments chứa secret; không thay arguments. Text/log được redact trước HTTP. Lỗi chỉ code DATA_POLICY, không ghi nguyên giá trị.
5. Parser giới hạn1MiB/16 recursion steps, duplicate/malformed sensitive JSON, unclosed quote/private key bị fail-closed.

## Regression tests và kết quả

Lệnh từ gốc worktree:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests/llm -p 'test_*.py' -v
```

**57/57 PASS:32 tests gốc và25 regression tests mới**; hoàn toàn HTTP mock, không live inference. Các assertions mới che giá trị khi báo lỗi.

| Nhóm regression | Kỳ vọng |
|---|---|
| Reviewer keys + prefix/suffix/camelCase | Toàn bộ giá trị giả biến mất sau clean |
| Text assignments, password-is, quotes | Không leak value, không cắt thiếu escaped value |
| Nested JSON, JSON strings/escaped fragments, Unicode keys | JSON parse được; metadata không nhạy cảm giữ nguyên |
| HTTP payload | Secret không có trong bytes gửi transport, kể cả prose/CLI và JSON-encoded JSON |
| Tool arguments trước HTTP và native output | DATA_POLICY, không mutate; request bị chặn trước HTTP nếu secret trong history |
| CLI | Known password context được redact, context mơ hồ bị từ chối; port/profile không bị xem là password |
| False positives | Mentions thường, tokenizer/passwordless, empty values và next record được giữ nguyên |
| Safety boundaries | Multiline/Unicode/escapes, duplicate keys, malformed quote/JSON, depth và private-key marker fail closed |

## Giới hạn còn lại

- Pattern/structure filtering là lớp phụ; không bảo đảm mọi secret không nhãn, base64/ciphertext hay Unicode confusables. Không tự pseudonym IP/email trước khi có policy nhóm.
- Key classification có tính bảo thủ: sensitive-name metadata như token_count có thể bị redact/từ chối dù là số đếm. Không có allowlist tự động theo MCP schema.
- Chỉ grammar CLI đã liệt kê được nhận biết; unknown/bare short-p bị từ chối. Unquoted free-text value là scalar; nhiều từ cần quote/JSON. Nội dung tự nhiên mơ hồ có thể bị lọc nhiều hơn mong muốn.
-4/4 live smoke trước đó áp dụng mã trước bản sửa, được giữ nguyên; không chạy lại và không dùng thay kiểm thử redaction này.
- Adapter vẫn I/O timeout, không hard wall deadline; chưa MCP/Agent integration. Không chuyển Jira Done hoặc merge PR.

Reviewer cần kiểm tra patch và chấp thuận lại; tác giả không tự resolve thread hoặc thay thế review của Trang.
