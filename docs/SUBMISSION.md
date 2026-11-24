# Bản bàn giao Linux Log Agent — 24/11/2026

## Bắt đầu tại đây

Đây là bản source hoàn thiện local được lưu riêng trên nhánh
`integration/hien-complete-20261124`, kế thừa nền
`59443adfc90d4f6fc2b530f1c41d76a9a25b46a4`. Nhánh này không thay thế `main`
hoặc các nhánh PR đã có trên GitHub. Runtime evidence, venv, private state và
các gói bàn giao cũ không nằm trong phần source mới đưa lên nhánh này.
Tài liệu cũ trong `docs/` giữ nguyên bối cảnh/ngày của các PR; trạng thái hiện tại
đọc ở tài liệu này, README và DIAGNOSTIC_TOOLS, không suy ra từ các báo cáo cũ.

## Cài ứng dụng

Yêu cầu Ubuntu, Python 3.12 và môi trường ảo riêng. Không cài bằng root và không
chạy Agent bằng root. Ví dụ sau tạo venv mới, không ghi đè venv đang dùng:

```bash
python3.12 -m venv .venv-submit
. .venv-submit/bin/activate
python -m pip install -e '.[mcp,control,test]'
linux-log-agent --help
linux-log-agent --doctor --backend mock --scope service
```

Các dependency trực tiếp được pin trong pyproject.toml. Cài đặt cần nguồn package
phù hợp; bộ source không chứa Python runtime, model, third-party packages hoặc VM.
Không tự tải model hay sửa dịch vụ để tạo kết quả PASS.

## Demo hoàn toàn synthetic, không cần Ollama hoặc quyền Linux

Chạy trong một thư mục làm việc mới để tên báo cáo không trùng. Cả ba lệnh dùng
mock HTTP, async worker, Coordinator, ToolBridge, official MCP SDK stdio, synthetic
MCP Server và renderer thật của ứng dụng. Các phản hồi model là fixture cố định,
không chứng minh AI hiểu câu hỏi hoặc hệ thống đã chẩn đoán Linux thật.

```bash
python -B -m agent.integration_demo --backend mock --scenario active --question 'Nginx có đang chạy không?' --report synthetic-active.md
python -B -m agent.integration_demo --backend mock --scenario port-conflict --question 'Vì sao Nginx không khởi động được?' --report synthetic-port-conflict.md
python -B -m agent.integration_demo --backend mock --scenario permission-denied --question 'Đọc journal Nginx' --report synthetic-permission-denied.md
```

Hai trường hợp đầu kết thúc exit 0. Permission-denied giữ trạng thái giới hạn,
exit 1; không biến thiếu quyền thành service inactive hoặc chẩn đoán thành công.
Stdout khớp nội dung Markdown trong `reports/`; file 0600 và không ghi đè. Nếu chạy
lại cùng tên report, writer phải từ chối — hãy dùng tên mới, không xóa dữ liệu cũ.

## Demo AI + Linux thật trên máy được phép

Ollama phải đang có `qwen3:4b-instruct-2507-q4_K_M` ở `127.0.0.1:11434`.
Ứng dụng không đổi model/endpoint. `--doctor` không inference và không gọi MCP:

```bash
linux-log-agent --doctor --scope service
linux-log-agent --scope service --question 'Nginx có đang chạy không?' --report nginx-read.md
```

Lệnh thứ hai **thực sự gọi Ollama và collector Linux**; chỉ chạy trên môi trường
được phép. Trên máy Hiền, `linux-log-agent` mặc định Ollama và cho nhập nhiều câu
hỏi độc lập; gõ `thoát` kết thúc. Mỗi câu tối đa 5 tool dispatches, 2 requests AI,
45 giây/request và 120 giây chẩn đoán, cleanup có budget/owner riêng. Không retry,
reconnect hay fallback sang mock. Worker bị kill không chứng minh backend Ollama
đã ngừng inference. Không có cam kết hard wall SLA.

Không nhập câu hỏi vào shell sau khi chương trình đã thoát. Nhập tại prompt
`Bạn muốn kiểm tra gì?` hoặc truyền `--question '...'`.

## 12 công cụ và phạm vi thực tế

| Tool | Khả năng local | Giới hạn cần nói rõ |
|---|---|---|
| service_status | Đọc systemd của service allowlist | Active không chứng minh website khỏe |
| list_failed_units | Liệt kê failed units nhìn thấy | Không restart |
| disk_usage | Dung lượng byte và inode của root | Namespace/user làm thay đổi dung lượng khả dụng |
| listening_ports | Snapshot socket TCP/UDP | Không kiểm tra truy cập từ Internet |
| query_journal | Journal Nginx theo cửa sổ/priority cố định | Có thể partial do quyền/độ phủ |
| search_log | Literal search trong nguồn Nginx allowlist | Không arbitrary file/regex/shell |
| detect_anomaly | So hai cửa sổ lỗi Nginx | Heuristic local, không phải accuracy đã nghiệm thu |
| verify_fix | Service, loopback HTTP và lỗi mới | Partial/unknown không có nghĩa verified repair |
| memory_oom_events | RAM hiện tại và OOM events nếu nguồn đọc được | Không có log không chứng minh không có OOM |
| failed_logins | Authentication events từ nguồn cố định | Pseudonym hóa; có thể thiếu coverage |
| check_config | nginx -t trong sandbox chỉ đọc | Không fallback chạy ngoài sandbox |
| restart_service | Canonical propose/status trong VM có operator session | Không có MCP approve/execute mode |

Mặc định chỉ expose 11 tool đọc. Tool thứ 12 cần session do operator mở và reader
đúng UID trong VM đã cấu hình. Model chỉ đề xuất business arguments; không tạo
metadata, evidence, approval token hoặc quyền thực thi. Cả 12 tên tool không đồng
nghĩa 12 câu hỏi cố định; quyền dispatch luôn thuộc ứng dụng.

## Approval, thực thi và hậu kiểm

Các account reader, approval và operator tách riêng. Chỉ operator xác nhận TTY;
approved không đồng nghĩa executed. Helper chỉ nhận grant riêng qua socket xác
thực UID; Agent không có key hoặc helper/control socket. Triển khai đặc quyền
chỉ áp dụng VM lab có snapshot và đã được cho phép, không cài trên laptop.
Xem `deploy/lab/README.md` và `src/control_plane/README.md`.

Một lần restart Nginx trong VM ngày 23/11 đã được người dùng xác nhận hai lần;
quan sát đúng một intent/job done, Nginx active/running và HTTP loopback 200.
Độ phủ journal chưa đủ, nên hậu kiểm vẫn partial/unknown và
`repair_verified=false`. Đây là kiểm thử restart một service đang khỏe, không
phải bằng chứng đã sửa một lỗi. Restart đã khóa lại; không reset ledger, khôi
phục snapshot/grant hoặc retry để lặp lại hành động. Bản nộp không chứa key,
private state, VM disk hay thao tác tự bật write.

## Chạy tests

Từ source root, sau khi cài các extras ở trên:

```bash
python -B scripts/test_offline.py
```

Runner tách suite để tránh import collisions; không chạy inference, benchmark,
collector host hoặc restart. Xuất logs/JUnit/summary vào thư mục tạm mới. Fixture
R1 phục vụ hai test SDK legacy được ghim checksum và tách khỏi canonical hiện tại.
Source ZIP không cần Git history. Metrics lịch sử chỉ đọc đúng artifact checksum
đã ghim; không sửa số liệu hoặc chạy lại model.

Bốn SKIP trên Linux là guard cũ cấm đọc collector thật trong offline tests. Chúng
không được tính là PASS. Subtests ghi riêng, không coi pytest PASS là accuracy.
Mỗi lần chạy xuất `summary.json`, logs và JUnit trong thư mục evidence riêng
do runner tạo; chúng không được đưa vào Git. Không dùng kết quả lịch sử thay
cho lần kiểm thử của checkout đang chạy.

## Những điều chưa nghiệm thu

- Chưa có benchmark chẩn đoán Linux đủ ground truth/S1/S2/S4 và manual review;
  formal Week 2 gate vẫn BLOCKED.
- Chưa chứng minh mọi tool được LLM lựa chọn tin cậy hoặc mọi hệ thống đều được
  đọc đủ log. Thiếu evidence phải giữ limited/partial/unknown.
- Citation ID tồn tại không xác minh diễn giải đúng ngữ nghĩa.
- Scoped VM tests không thay thế independent security review hoặc deployment
  acceptance trên một máy khác.
- Không tuyên bố production-ready, automatic remediation hoặc sửa lỗi thành công.
