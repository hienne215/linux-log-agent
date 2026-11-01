# SCRUM-14 / P00-05 — Ollama mock tool-calling smoke benchmark

Python 3 standard library; không cài package. Chỉ gọi Ollama loopback `localhost:11434`; không MCP Server, không thực thi Linux tools, không restart/fault injection, không tải hoặc unload model. Sáu tên tool trong `cases.json` là metadata/schema mock, chưa phải contracts đã chốt với Thủy.

## Baseline đã được Hiền duyệt

Chọn **`qwen3:4b-instruct-2507-q4_K_M`** cho giai đoạn tích hợp tiếp theo: full benchmark **18/18 đúng**, median **3,17 s**, mean **4,56 s**, không lỗi. Đây là quyết định smoke benchmark, chưa xác nhận năng lực chẩn đoán nhiều bước/MCP/VM.

Chạy từ thư mục gốc repository, chỉ cần Python standard library và model đã có sẵn:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/benchmarks -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 tests/benchmarks/full_benchmark_instruct.py
```

Runner Instruct chạy 6 cases × 3 repetitions, seeds42/43/44, num_predict256, num_ctx4096, temperature0, num_thread4, stream=false; **bỏ trường think**. Deadline45s/request, total budget900s. Dừng khi timeout/API/JSON error; kiểm RAM trước request và minimum sampler sau request, không gửi tiếp nếu dưới1024MiB. Không kill server. Mỗi lần tạo thư mục mới trong `instruct_full_results/`.

[Báo cáo cuối](instruct_full_results/20261101T170502Z/REPORT.md) · [JSON](instruct_full_results/20261101T170502Z/results.json) · [CSV](instruct_full_results/20261101T170502Z/results.csv).

Giữ JSON các lần đo Thinking256, Thinking512 quick và Instruct256 quick để kiểm tra so sánh. Không commit các CSV trùng lặp, preflight trùng metadata trong JSON, host sampling phụ hoặc báo cáo cũ có đường dẫn máy cá nhân. Chúng vẫn giữ nguyên tại máy thực hiện.

Metadata quick Instruct cũ có false positive `template_always_opens_think`; template chỉ chứa marker trong xử lý lịch sử, không mở thinking ở generation suffix. Full benchmark ghi đúng `template_contains_think_marker` và suffix; không có thinking content trong 18 lượt. Snapshot `executed-script.py.txt` giữ provenance của quick check; script hiện tại đã sửa heuristic. Không chỉnh số liệu cũ.

## Chạy Thinking baseline lịch sử (không phải baseline đã chọn)

```bash
python3 -m unittest discover -s tests/benchmarks -p 'test_*.py' -v
python3 tests/benchmarks/benchmark_ollama.py
```

Ollama cần đang chạy và có sẵn `qwen3:4b`. Script kiểm `/api/version`, `/api/tags`, `/api/ps`, `/api/show` rồi POST `/api/chat`. Nếu thiếu model/server hoặc lỗi compatibility, dừng và ghi kết quả chưa hoàn thành; không tự cài, pull model, đổi port/bind hay cấu hình server. HTTP client vô hiệu proxy/redirect và chỉ chấp nhận loopback port 11434.

Mặc định **36 lượt**: sáu case × ba lần × think=false/true. Temperature=0, context=4096, output cap=256 tokens, num_thread=4, seed=42/43/44 theo repetition. Thứ tự mode đổi theo case/repetition; không warmup riêng, loaded-before và load_duration được giữ. num_thread=4 là yêu cầu hạn chế tải inference, không phải hard CPU quota; xem CPU thực đo.

Deadline 45 giây/request, budget run 1500 giây; MemAvailable trước mỗi request cần ≥1 GiB. Hết deadline chỉ đóng process HTTP client của benchmark; không kill server/runner. Sau timeout/API/JSON error dừng run để không gửi tiếp khi chưa rõ server đã hủy request. Ctrl+C lưu partial results. Chưa đủ 18 lượt mỗi mode thì không tuyên bố hoàn tất/đạt.

Run cấu hình khác phải là thư mục kết quả mới, không gộp số liệu:

```bash
python3 tests/benchmarks/benchmark_ollama.py --num-predict 384 --timeout 60 --total-budget 2100
```

CLI cho phép tối đa 512 output tokens, context 4096, 8 inference threads, 90 giây/request, 2400 giây/run, 3 repetitions. `--repeats 1` chỉ là quick check 12 lượt, không đủ gate chọn model. Mỗi run tạo thư mục timestamp mới; `--output-dir` cũng phải chưa tồn tại. Không đổi prompt/cases giữa một run.

## Cases và chấm điểm

| Case | Bước đọc yêu cầu | Expected native call |
|---|---|---|
| S1 | Trạng thái Nginx failed | service_status, unit=nginx.service |
| S2 | Journal Nginx trong UTC window | query_journal, unit + since/until trong cases.json |
| S3 | Root filesystem gần đầy | disk_usage, path=/ |
| S4 | Xung đột TCP 80 | listening_ports, port=80, protocol=tcp |
| S5 | Failed SSH logins | failed_logins, since/until trong cases.json |
| S6 | Nginx config syntax | check_config, profile=nginx |

Cả sáu tool được quảng bá ở mọi request; expected chỉ dùng ở scorer, không gửi riêng tới model. Prompt tiếng Anh nêu rõ bước đọc; chưa đo tiếng Việt, root cause hoặc multi-step reasoning.

Không parse JSON trong content thành tool call. Native structure cần đúng một function call, name string và arguments object. Tool name, exact args và schema chấm độc lập; joint success cần tất cả và done=true. JSON-string arguments/multiple calls là invalid_structure; prose có JSON nhưng không native call là text_only; HTTP JSON lỗi là json_error; deadline là timeout. Mọi lỗi nằm trong denominator.

## Artifacts và metrics

`results/<UTC-timestamp>/` chứa `results.json`, `results.csv`, `REPORT.md` tiếng Việt. JSON lưu model digest, server version, script/suite/template hashes, options/seed, metadata máy, từng attempt và tổng hợp mode.

Wall mean/median gồm cả lỗi/timeout; JSON có response-only mean riêng. Token/s weighted = tổng eval_count / tổng eval_duration_s trên response có metrics; có thể gồm thinking tokens, không phải usable-tool throughput. Chỉ lưu độ dài content/thinking và marker, không lưu reasoning text. think=false là request, không mặc định server đã tắt reasoning.

Sampler read-only `/proc` mỗi 0,5 s: MemAvailable/swap toàn máy, tổng RSS/CPU Ollama process tree. CPU one-core 100% = một logical CPU; machine % chia số logical CPUs. RSS có thể đếm trùng shared pages, thiếu process không đọc được, bỏ lỡ peak ngắn. RAM trước/sau không phải mức nạp model thuần khi model đã resident. API ps.size không đồng nhất RSS. Script không dừng workload khác; inference khác sẽ ảnh hưởng số liệu.

## Gate và quyết định

Gate định trước: một mode đủ 18/18 lượt, mỗi case ba lần; joint success ≥17/18, structure 18/18, không timeout/JSON/API error, median ≤30 s, MemAvailable ≥1 GiB. Đây là gate smoke thực dụng, không phải root-cause accuracy cuối dự án. Không sửa gate để làm đẹp kết quả. Cả hai mode đạt thì đề xuất mode median thấp hơn; thiếu mẫu chỉ giữ ứng viên.

Runner Thinking lịch sử chỉ đánh giá qwen3:4b đã có; runner Instruct dùng model đã được duyệt nêu trên. Không thử API/provider khác; ngân sách API = 0, không gửi dữ liệu ra ngoài. Nếu local không đạt, API cần nhóm duyệt provider/redaction/secret storage/ngân sách riêng. Chưa có evidence P00-04/VM snapshot hoàn tất: Trang cần review RAM khi VM chạy cùng model trong giai đoạn tích hợp; quyết định baseline hiện tại dựa trên mock benchmark. Số liệu thử tay của Hiền không cộng vào 36 lượt này.

Nhánh SCRUM-14 từ main. PR chỉ chứa benchmark; 7 file kế hoạch được giữ ngoài commit. Không merge hoặc tự chuyển Jira sang Done.

Nguồn chính thức: [Chat API](https://docs.ollama.com/api/chat), [Thinking](https://docs.ollama.com/capabilities/thinking), [Running models](https://docs.ollama.com/api/ps). Tài liệu hiện hành không thay thế server version/capability thực đo.

## Đo host bổ sung khi chạy qua PID sandbox

Nếu benchmark chạy từ môi trường chỉ thấy process namespace riêng, các cột CPU/RSS có thể N/A dù Ollama vẫn hoạt động qua localhost. Khi đó, mở terminal thông thường trên host (không cần root) và chạy song song bộ đo chỉ đọc:

```bash
python3 tests/benchmarks/monitor_ollama.py tests/benchmarks/results/<run-directory>
```

Bộ đo không gửi inference hoặc tín hiệu tới process. Nó lưu `host-metrics-<timestamp>.json` riêng; không sửa điểm/results.json. Có tổng hợp toàn thời gian đo và từng trial; `monitor_covers_entire_trial=false` đánh dấu lượt đo thiếu thời gian. Chỉ so sánh CPU theo mode trên các lượt được bao phủ đầy đủ. Đây là bằng chứng bổ sung, không được điền ngược giá trị thiếu bằng phỏng đoán.

Lưu ý compatibility: đọc `metadata.show.thinking` trong JSON trước khi diễn giải mode. Ở preflight của lần chạy SCRUM-14 này, server 0.40.13 quảng bá `values=[true]`, `default=true`. Các lượt gửi think=false vẫn được ghi như phép thử request/compatibility; không được gọi chúng là bằng chứng non-thinking đã được hỗ trợ hoặc tắt thành công.
