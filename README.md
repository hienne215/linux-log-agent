# Linux Log Agent

**Làm việc trực tiếp trên máy:** lệnh `linux-log-agent` dùng source local hiện tại.
Không cần giải nén hoặc cài lại bản ZIP. Phạm vi hiện tại xem
[Diagnostic tools](docs/DIAGNOSTIC_TOOLS.md); tài liệu PR cũ giữ mốc lịch sử.

Trợ lý chẩn đoán Linux bằng ngôn ngữ tự nhiên. Ollama đề xuất tool; ứng dụng kiểm
tra quyền và arguments, gọi MCP qua official SDK stdio, rồi trả kết quả kèm
bằng chứng và báo cáo Markdown. Dữ liệu thiếu hoặc chưa xác minh được ghi rõ.

## Cài và chạy trên Ubuntu

Dùng Python 3.12 và một virtual environment mới. Không dùng global pip hoặc sudo
để cài ứng dụng. Từ source checkout đã được review:

```bash
python3.12 -m venv .venv-app
. .venv-app/bin/activate
python -m pip install -e '.[mcp]'
linux-log-agent --doctor
linux-log-agent
```

MCP SDK được pin `2.3.0`. Ollama phải có sẵn model
`qwen3:4b-instruct-2507-q4_K_M` tại `127.0.0.1:11434`; ứng dụng không tự tải model,
đổi endpoint hoặc sửa service. `--doctor` chỉ kiểm tra metadata/phần mềm, không
inference hoặc khởi động MCP. Có đủ executable không có nghĩa đủ quyền đọc.
Có thể kiểm tra riêng một phạm vi bằng `linux-log-agent --doctor --scope service`.
Doctor chỉ yêu cầu phần mềm của phạm vi đó: thiếu nginx/bubblewrap không chặn
kiểm tra trạng thái service. `NOT_VERIFIED` nghĩa là chưa chạy collector hay xác
minh quyền; exit 0 của doctor không phải kết quả chẩn đoán Linux PASS.

Lệnh được đóng gói cùng canonical schemas, MCP server và worker; có thể chạy từ
thư mục khác sau khi cài. Wheel cũng được hỗ trợ. Virtual environment thử nghiệm
không được đưa vào Git. Không ghi đè một venv hay checkout đang phục vụ PR khác.

## Hỏi gì?

Nhập tại dòng `Bạn muốn kiểm tra gì?`, gõ `thoát` để kết thúc:

- Nginx có đang chạy không? Kiểm tra thêm HTTP loopback.
- Ổ đĩa gốc còn bao nhiêu dung lượng và inode?
- Có unit nào đang failed không?
- Port TCP 22 hoặc UDP 53 có đang lắng nghe không?
- Xem lỗi journal Nginx trong một giờ gần đây.
- Tìm chuỗi upstream trong error log Nginx.
- Máy còn bao nhiêu RAM, gần đây có OOM không?
- Có đăng nhập thất bại trong 15 phút vừa qua không?
- So sánh tần suất lỗi Nginx giữa hai cửa sổ 15 phút.
- Kiểm tra cú pháp cấu hình Nginx.

Một câu hỏi có tối đa 5 dispatches, 2 requests AI, 45 giây/request và 120 giây
phiên. AI thực sự chọn tool; ứng dụng không coi model output là quyền thực thi.
Không retry hoặc fallback sang mock khi AI/backend lỗi. Đây là giới hạn local,
không phải cam kết mọi câu hỏi sẽ được trả lời đúng hoặc kịp thời.

Có thể chọn phạm vi hẹp và lưu report mới:

```bash
linux-log-agent --scope health --question 'Kiểm tra Nginx và HTTP loopback'
linux-log-agent --scope disk --question 'Kiểm tra dung lượng và inode' --report disk-check.md
```

Có thể hiển thị trace thực tế hoặc mở lại report đã lưu mà không gọi AI/MCP:

```bash
linux-log-agent --version
linux-log-agent --scope service --question 'Nginx đang ở trạng thái nào?' --show-trace --report nginx-check.md
linux-log-agent --view-report nginx-check.md
```

Report chứa session/trace IDs, thời điểm UTC, state transitions thực tế và ledger
canonical đã validate/redact; chỉ liệt kê những response thực sự nhận được.
Không có response không có nghĩa tool chưa thực thi. Terminal hiển thị thêm exit
status/signal gần nhất và số automatic restarts nếu systemd cung cấp; thiếu số
liệu giữ trạng thái chưa biết, không tự coi là 0. Các số này không chứng minh đã
sửa lỗi hoặc HTTP khỏe.

Report nằm trong `reports/` của thư mục hiện tại, quyền 0600, không ghi đè và
không nhận đường dẫn tùy ý. `--show-model-text` cho xem riêng diễn giải AI chưa
được xác minh. Mặc định terminal ưu tiên số liệu collector đã validate.

`--backend mock` là **model kịch bản cố định**, không hiểu câu hỏi; nó vẫn gọi
collector Linux thật trong CLI này. Demo hoàn toàn synthetic tách riêng:

```bash
python -m agent.integration_demo --backend mock --scenario port-conflict --question 'Vì sao Nginx không chạy?'
```

Các fixture đa bước bổ sung tương ứng S1/S2/S4 của kế hoạch gốc:

```bash
python -m agent.integration_demo --backend mock --scenario crash-loop --question 'Đọc fixture crash loop' --report s1.md
python -m agent.integration_demo --backend mock --scenario config-error --question 'Đọc fixture lỗi cú pháp' --report s2.md
python -m agent.integration_demo --backend mock --scenario backend-502 --question 'Đọc fixture HTTP 502' --report s4.md
```

Đây là kết quả mock HTTP và MCP SDK stdio synthetic có citations; không phải
suy luận của LLM thật hoặc fault injection trên Linux. Các scenario này không
được phép chọn live Ollama. Formal gate vẫn cần đủ run VM, ground truth và
review độc lập theo kế hoạch; tests offline không thay thế các điều kiện đó.

## Tìm code ở đâu?

```text
src/agent/
  read_only.py            CLI thật, scope và ghép các component
  diagnostics/           Collector read-only và binding canonical mới
  llm/                   Ollama đồng bộ, async worker, validation và redaction
  mcp_client/            Official SDK stdio và session lifecycle
  integration_boundary/  ToolBridge: allowlist, schema, budget và cleanup
  coordinator.py         State machine và vòng điều phối
  incident_report.py     Renderer báo cáo Markdown
  cli/                   Lưu report và giao diện xác nhận synthetic
  integration_demo/      Demo riêng với synthetic MCP
src/mcp_server/           MCP scaffold và collector lõi đã kế thừa
src/control_plane/        Approval, private handoff, job owner và hậu kiểm; runtime riêng trong lab VM
schemas/tools/           Canonical JSON schemas, không phải quyền dispatch
tests/canonical_agent/   Tests binding, collector, lifecycle và luồng local
tests/control_plane/     Tests expiry, tampering, replay và durable intent
```

Trong `diagnostics/`: `journal_reader.py` giữ subprocess owner;
`journal_policy.py` giữ phạm vi đọc và lọc dữ liệu; `system_diagnostics.py` xử lý
RAM/OOM, authentication và config; các file `*_binding.py` nối với Coordinator;
`health_verification.py`, `service_verification.py`, `journal_anomaly.py` xử lý
các phép kiểm tương ứng. Launcher/worker vẫn ở vị trí cũ để giữ entrypoint.
`read_gateway.py` và `read_gateway_worker.py` là gateway đọc/lọc riêng cho lab VM:
reader vẫn không có nhóm adm/journal/sudo; gateway chỉ nhận sáu profile cố định,
không nhận shell hay thao tác restart. Chi tiết tại [lab VM](deploy/lab/README.md).
Tests theo cùng tên chức năng; `test_process_owner.py` dành riêng cho cleanup.
Collector service/systemctl, failed-units, sockets và journal dùng chung phần
đóng/reap tại `src/mcp_server/process_cleanup.py`, chỉ nhận process do chính
collector sở hữu. Hủy nhiều lần hoặc lỗi pipe không được làm rơi owner. Timeout
của service-status bao gồm cả khởi tạo process; thời gian chờ cleanup tách riêng.

Sau khi cài dependencies, chạy tests local từ repository root, không cần Ollama:

```bash
PYTHONPATH=src:. python -B -m unittest discover -s tests/canonical_agent -v
```

Các fixture trong thư mục này dùng dữ liệu synthetic; kết quả không phải nghiệm
thu Linux thật. Runtime artifacts/report thử nghiệm không đặt trong source/tests.

Kiểm thử offline toàn bộ code hiện tại, không trộn source từ nhánh cũ:

```bash
python -m pip install -e '.[mcp,control,test]'
python -B scripts/test_offline.py
```

Runner tách từng suite để không trùng tên fixture, xuất log/JUnit vào một thư mục
tạm mới và in đường dẫn ở cuối. Không chạy benchmark hoặc inference. Trên Linux,
bốn test SDK cũ có guard tránh đọc collector thật sẽ giữ SKIP, được ghi riêng là
`PASS_WITH_SKIPS`; không được tính là bốn tests đã PASS. Skip khác, lỗi collection,
suite rỗng hoặc test lỗi đều làm runner thất bại. Số subtests được ghi riêng,
không biến số tests PASS thành accuracy hay nghiệm thu hệ thống.
Hai SDK tests legacy anomaly/verify dùng đúng mock R1 tại `d442479`, từ archive
đã ghim SHA256 trong `tests/fixtures/`, kiểm tra byte schema rồi giải nén riêng
trong output; không trộn Agent cũ vào code hiện tại. Nếu archive thiếu, runner
thử Git object có sẵn; không tự fetch hoặc thay contract. Artifact sai checksum
làm test dừng. Các tests `canonical_agent` vẫn dùng schemas hiện tại. Metrics
benchmark lịch sử chỉ đọc đúng hai artifact có SHA256 đã ghim, không chạy model.
Các script thủ công `tests/mcp_smoke` không phải pytest cases nên được tách khỏi
runner; official SDK stdio vẫn chạy trong MCP Client, Boundary, canonical Agent
và Integration Demo. Hash source/fixtures được đối chiếu đầu/cuối lần chạy.

## Phạm vi và giới hạn

Hiện có 11 profile read-only local. Nguồn, unit, đường dẫn, executable và HTTP
endpoint do ứng dụng allowlist, không do người dùng/model tự chọn. Không có shell
tool. Missing/permission denied/partial/unknown không được đổi thành success.

- Service active không chứng minh website khỏe. HTTP HEAD loopback không chứng
  minh truy cập từ Internet hay nội dung ứng dụng đúng.
- Journal/auth/OOM phụ thuộc quyền và độ phủ nguồn. Không thấy sự kiện không có
  nghĩa không có lỗi. IP/danh tính và log text được lọc trước model.
- `check_config` dùng sandbox chỉ đọc; có thể bị BLOCKED bởi quyền/runtime,
  không chạy lại ngoài sandbox để tạo PASS.
- `restart_service` là tool thứ 12, chỉ **tạo đề xuất/đọc trạng thái**, không
  execute. Chỉ xuất hiện khi reader trong VM nhận session do operator mở; mặc
  định Agent vẫn chỉ có 11 tool read-only. `linux-log-action` yêu cầu hai xác
  nhận TTY riêng cho duyệt và thực thi. Một restart VM đã được người dùng xác
  nhận và quan sát job done; hậu kiểm vẫn partial/unknown vì độ phủ journal,
  không chứng minh đã sửa lỗi. Cấu hình đã khóa lại `write_enabled=false`. Xem
  [control_plane](src/control_plane/README.md) và [lab VM](deploy/lab/README.md).
  CLI/Agent/MCP read-only không được cấp quyền hoặc tự gọi helper.
- Evidence/citation hợp lệ chỉ xác nhận tham chiếu tồn tại, không xác minh ngữ
  nghĩa của diễn giải. Kill worker không đảm bảo backend Ollama dừng inference.
- Với collector systemctl local, unit và trạng thái trong data phải khớp chính
  xác snapshot evidence, source, timestamp và flags. Dữ liệu mâu thuẫn bị từ
  chối trước AI/report. RAM chỉ nhận đúng hai số byte hợp lệ, khả dụng không
  vượt tổng. Kiểm tra nhất quán này không chứng minh nguồn đã được xác thực.
- Các thay đổi local này chưa đồng nghĩa đã nằm trên main, đạt VM acceptance,
  hoàn thành formal gate hoặc production-ready.

Chi tiết tool, chính sách dữ liệu, kết quả đã kiểm chứng và blocker:
[Diagnostic tools](docs/DIAGNOSTIC_TOOLS.md),
[Architecture](docs/ARCHITECTURE.md), [Security](docs/SECURITY.md).
