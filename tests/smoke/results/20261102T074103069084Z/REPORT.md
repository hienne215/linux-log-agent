# SCRUM-23 — Live smoke test Ollama LLM Adapter

Ngày 02/11/2026, 14:41 giờ Asia/Ho_Chi_Minh; run timestamp UTC `20261102T074103069084Z`.

**PASS 4/4 case, đúng4 inference requests, không retry.** Không phát hiện lỗi cần sửa Adapter từ phạm vi smoke này. Chưa đủ để tuyên bố hoàn tất SCRUM-23 hoặc tích hợp MCP.

## Môi trường và cấu hình

- Nhánh `feat/SCRUM-23-llm-adapter`, commit `ba5eb5d9f7f55f14e76ef1509bf2c740c94ffaf9`; PR#4 vẫn chưa merge.
- Ollama API version `0.40.13`; model có sẵn `qwen3:4b-instruct-2507-q4_K_M`, digest `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`.
- Endpoint `http://127.0.0.1:11434`; num_ctx4096, num_predict256, temperature0, num_thread4, stream=false, không gửi think. Không tải model, thay service/config hoặc warmup inference.
- Model chưa resident trước S1; S1 gồm khoảng6.12s load model. Latency khác điều kiện warm/cache là bình thường, không coi đây là benchmark so sánh tốc độ.
- Timeout Adapter45s là I/O timeout. Harness thêm parent-process wall guard45s và kiểm MemAvailable mỗi0.2s. Guard này **không sửa hoặc chứng minh Adapter đã có hard wall deadline**.

## Từng case

| Case | Kết quả | Latency (s) | Prompt tokens | Output tokens | Generation (s) | Token/s |
|---|---|---:|---:|---:|---:|---:|
| S1 | PASS — Text khớp SMOKE_OK | 7.20 | 18 | 5 | 0.346 | 14.44 |
| S2 | PASS — Native call hợp lệ, exact args | 11.28 | 278 | 22 | 1.970 | 11.17 |
| S3 | PASS — Native call đúng JSON Schema, exact args | 4.18 | 282 | 29 | 2.645 | 10.96 |
| S4 | PASS — Text-only bị OUTPUT_POLICY đúng kỳ vọng | 1.75 | 275 | 4 | 0.292 | 13.69 |

- S1: Completion.kind=text, không có native calls; text được so sánh trong bộ nhớ, không lưu raw response.
- S2: `mock_service_status`, arguments `{"unit":"lab.service"}`. unit string đúng enum, required đủ, không extra fields; Adapter schema validation pass.
- S3: `mock_listening_ports`, arguments `{"port":80,"protocol":"tcp"}`. port integer trong1–65535, protocol đúng enum, required đủ, không extra fields; Adapter schema validation pass.
- S4: model có0 native calls, content nonempty, thinking0, done=true/done_reason=stop; Adapter require_tool=True ném OUTPUT_POLICY. Đây là **thành công của case negative**, không phải lỗi smoke. Không diễn giải text thành JSON tool call.
- S4 không có Completion nên usage/HTTP latency lấy từ observation metadata của harness trước normalization; không giả đây là metrics được Adapter trả thành công. Cột latency S4 đo đến khi Adapter ném exception.

Cả4 HTTP responses đều done=true, done_reason=stop, thinking_chars=0. Không chạm output budget256, không timeout, API error, malformed response hoặc schema error.

## Tài nguyên và giới hạn

- MemAvailable minimum **2725.91 MiB ≈ 2.66 GiB**, luôn trên1024MiB. Đây là RAM toàn hệ thống, không phải RSS riêng Ollama.
- Tổng853 prompt tokens,60 output tokens; run khoảng25.29s gồm preflight/process overhead. Mỗi case đúng1 request, tổng4; không gọi tools thật.
- Đây là lần đầu Adapter được kiểm với Ollama thật;32 mock tests trước đó kiểm nhiều nhánh lỗi hơn. Live xác nhận text/native parsing và require_tool policy tương thích với model/version này; không kiểm live timeout, refused connection, malformed server, multi-turn lifecycle hoặc slow-drip.
- Chưa tích hợp MCP/SCRUM-12/SCRUM-22, Agent loop, approval/helper hoặc Linux tools. Mock definitions không phải contracts chính thức.
- Không thu thập raw reasoning, secrets hoặc logs Linux. Chỉ lưu metrics, exact synthetic tool arguments và lengths/flags. Chỉ model text cần kiểm được giữ tạm trong bộ nhớ.

## Quyết định trước merge PR#4

**Không cần sửa Adapter do kết quả4 case này.** Vẫn cần reviewer đánh giá các giới hạn đã công bố: I/O timeout chưa hard wall, schema dialect/registry mapping với SCRUM-12, Message/ID mapping và cancellation/budgets khi có SCRUM-22, redaction là lớp lọc phụ. Không biến smoke PASS thành xác nhận toàn bộ security/integration hoàn tất.

Artifacts: `results.json`, báo cáo này; script `../../ollama_adapter_smoke.py`, hướng dẫn `../../README.md`. Mỗi lần chạy script tạo thư mục mới và tối đa4 requests; không tự chạy lại sau review.

Git: chỉ thêm thư mục `tests/smoke/` ở local. Tất cả file tracked giữ nguyên SHA256, không commit/push/merge hoặc cập nhật Jira; không có thao tác thay đổi PR#3/PR#4, nhánh khác hoặc file untracked cũ.
