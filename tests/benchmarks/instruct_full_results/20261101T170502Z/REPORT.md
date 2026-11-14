# SCRUM-14 — Full benchmark Qwen3-4B-Instruct-2507

Ngày 02/11/2026, múi giờ Asia/Ho_Chi_Minh. Dữ liệu timestamp UTC: `20261101T170502Z`.

**Kết quả gate: ĐẠT. Đề xuất chọn Qwen3 4B Instruct làm LLM chính cho giai đoạn phát triển tiếp theo của dự án, trong phạm vi smoke benchmark mock tool calling.** Không suy rộng thành chứng nhận chẩn đoán thực tế hoặc an toàn vận hành.

## Cấu hình cố định

- Model `qwen3:4b-instruct-2507-q4_K_M`; digest `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`; Q4_K_M; Ollama API báo `0.40.13`.
- 6 cases × 3 repetitions = 18 lượt; theo thứ tự S1–S6 ở mỗi repetition, seed lần lượt 42/43/44.
- `num_predict=256`, `num_ctx=4096`, `temperature=0`, `num_thread=4`, `stream=false`, `keep_alive=5m`; **không gửi trường think**.
- Cả sáu tool schemas gửi mỗi request. Giữ nguyên system prompt, case prompts, expected outputs và scorer `benchmark_ollama.evaluate`. Không có tool dispatch hoặc MCP Server thật.
- 45 giây/request, budget toàn lượt chạy 900 giây. Kiểm tra MemAvailable trước mỗi request và minimum từ sampler sau mỗi request; dừng trước request kế tiếp khi dưới 1024 MiB. Request đang chạy vẫn chịu hard deadline 45 giây; không kill Ollama/server.
- Dừng chuỗi khi timeout, API/HTTP/JSON error. Không có retry tự động.
- Model đã resident khi bắt đầu, `size_vram=0`, context4096; không có warmup inference. Số latency này chủ yếu phản ánh warm model.
- 15/15 unit tests benchmark pass trước inference. Lệnh: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/benchmarks -p 'test_*.py' -v`.

## Acceptance gate giữ nguyên

| Tiêu chí | Kết quả | Đánh giá |
|---|---:|---|
| Hoàn tất 18/18, mỗi case 3 lượt | 18/18 | Đạt |
| Tool + exact arguments ≥17/18 | 18/18 | Đạt |
| Native structures 18/18 | 18/18 | Đạt |
| Không timeout/API/JSON errors | 0 lỗi | Đạt |
| Median ≤30 s | 3.17 s | Đạt |
| MemAvailable ≥1024 MiB | 3944.60 MiB | Đạt |

## Từng tình huống

| Case / tool | Đúng tool | Exact args | Valid structure | Latency 42 / 43 / 44 (s) | Mean (s) | Output tokens 42 / 43 / 44 |
|---|---:|---:|---:|---|---:|---|
| S1 / `service_status` | 3/3 | 3/3 | 3/3 | 2.62 / 2.85 / 2.58 | 2.68 | 21 / 21 / 21 |
| S2 / `query_journal` | 3/3 | 3/3 | 3/3 | 8.24 / 8.27 / 8.10 | 8.20 | 71 / 71 / 71 |
| S3 / `disk_usage` | 3/3 | 3/3 | 3/3 | 2.49 / 2.66 / 2.57 | 2.57 | 19 / 19 / 19 |
| S4 / `listening_ports` | 3/3 | 3/3 | 3/3 | 3.42 / 3.46 / 3.28 | 3.39 | 27 / 27 / 27 |
| S5 / `failed_logins` | 3/3 | 3/3 | 3/3 | 7.42 / 7.90 / 7.46 | 7.59 | 65 / 65 / 65 |
| S6 / `check_config` | 3/3 | 3/3 | 3/3 | 3.06 / 2.94 / 2.69 | 2.90 | 20 / 20 / 20 |

- Tool selection accuracy: **18/18 (100.00%)**; exact arguments: **18/18 (100.00%)**; joint success: **18/18**.
- Classifications: `{'correct': 18}`. Text-only: 0. Không biến JSON trong prose thành native call.
- Mean latency **4.56 s**, median **3.17 s**; elapsed **82.62 s**.
- Tổng output **669 tokens**; weighted generation **11.08 tokens/s** (tổng eval_count / tổng eval_duration). Prompt tokens: **11502**.
- Peak RSS cây Ollama **3148.84 MiB ≈ 3.08 GiB**; MemAvailable minimum **3944.60 MiB ≈ 3.85 GiB**; CPU trung bình có trọng số latency **24.88% toàn máy** (16 logical CPUs).
- Sampler `/proc` mỗi 0,5 s; RSS có thể đếm trùng shared pages, MemAvailable là toàn máy, có thể bỏ sót peak rất ngắn. Không phải peak RAM riêng weights và chưa đo khi đồng thời chạy VM/MCP.

## Thinking/template metadata

`/api/show` ghi `general.finetune=Instruct`, version2507, `thinking=null`; danh sách capabilities có chuỗi thinking chung nhưng không dùng nó để kết luận. Template có `<think>` ở nhánh lịch sử assistant; phần `add_generation_prompt` chỉ mở assistant, không mở `<think>`.

Ghi chính xác `template_contains_think_marker` và `template_generation_suffix`, **không còn heuristic template_always_opens_think**. Template SHA256 `40c21f34cf67d8c760ef72f8ad3ae5afad514299d4b06e91dd9a8d705af7b541`. Quan sát: thinking nonempty 0/18; content nonempty 0/18. Không gửi think=true hoặc think=false.

## So sánh các benchmark trước (không chạy lại)

| Model / cấu hình | n | Joint đúng | Mean s | Median s | Tokens/s |
|---|---:|---:|---:|---:|---:|
| Thinking-2507, 256, think=false | 18 | 2/18 | 25.62 | 24.88 | 10.58 |
| Thinking-2507, 256, think=true | 18 | 3/18 | 25.02 | 25.22 | 10.49 |
| Thinking-2507, 512, think=true (quick) | 6 | 3/6 | 42.81 | 46.38 | 10.20 |
| Instruct-2507, 256, think omitted | 18 | 18/18 | 4.56 | 3.17 | 11.08 |

Thinking và Instruct là hai bộ weights khác nhau, không phải cùng model bật/tắt thinking. Thinking digest `359d7dd4…`, Instruct `0edcdef3…`; template khác. Native defaults top_p Thinking0.95/Instruct0.8 được giữ nguyên; request override temperature0. Full 256 dùng cùng cases/scorer/context/threads/seeds và deadline45s, nhưng thứ tự baseline Thinking xen kẽ modes, tải máy/cache và thời điểm đo khác. Quick Thinking512 có 6 lượt, deadline90s và cold start; không so trực tiếp như mẫu 18 lượt tương đương. Thinking false trước đây vẫn sinh nội dung suy luận, không được diễn giải thành model non-thinking thật.

31/36 Thinking256 hết budget trước native call; Instruct giảm số token cần sinh rõ rệt. Kết quả này hỗ trợ chọn cấu hình Instruct cho mock tool selection, không chứng minh Thinking kém ở mọi bài toán.

## Quyết định và giới hạn

Nếu gate đạt, chọn `qwen3:4b-instruct-2507-q4_K_M` làm LLM chính ở cấu hình đã đo cho bước tích hợp tiếp theo. Giữ cơ chế giới hạn thời gian/output, kiểm tra schema và approval boundaries khi triển khai agent. Không tự thực hiện tích hợp trong task này.

Ba seed ở temperature0 không phải ba mẫu ngẫu nhiên độc lập mạnh; kết quả có thể trùng lặp. Sáu prompt tiếng Anh nêu rõ tác vụ; chưa đánh giá tiếng Việt, phân tích log thật, multi-step diagnosis, prompt injection, MCP Server, restart_service hoặc chạy đồng thời VM. Gate smoke đạt không thay thế các bài kiểm thử tích hợp/an toàn đó.

## Artifacts và tái lập

Runner mới: `../../full_benchmark_instruct.py`. Từ repository:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/benchmarks/full_benchmark_instruct.py
```

Mỗi lần chạy tạo thư mục timestamp mới, đúng 18 requests tối đa, không tải model hay sửa service. `results.json`/`results.csv` chứa dữ liệu từng lượt, `preflight.json` và `model-template.txt` chứa metadata. Giữ `script_sha256`, `suite_sha256`, `reused_harness_sha256` và hash request để audit. Các hashes của 18 requests đã được tái dựng xác nhận đúng cấu hình và không chứa think.

Toàn bộ 62 file trước phiên giữ nguyên SHA256. Git vẫn nhánh `bench/SCRUM-14-llm-smoke`, không stage/commit/push/merge hoặc cập nhật Jira. Dừng để Hiền review.
