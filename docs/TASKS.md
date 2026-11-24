# Danh mục task toàn dự án

Bản đề xuất ngày 01/11/2026, chờ nhóm phê duyệt. Tất cả task khởi tạo TODO; tài liệu này không phải bằng chứng tính năng đã triển khai. Tuần 1–4 là tuần tương đối từ ngày khởi động được nhóm xác nhận.

Nguồn: đề cương v2 ngày 01/11/2026 do người dùng cung cấp và yêu cầu phân công chính thức. Repository https://github.com/hienne215/linux-log-agent tại commit 1f594f132bb7b2b585766342cb7efc729b0086d6 chỉ có README.md (`# linux-log-agent`); chưa có ARCHITECTURE, ROADMAP, TASKS hay đề cương trong repo. Checkout mới sạch trước khi lập kế hoạch; không thể suy ra trạng thái những checkout khác trên máy. README được giữ nguyên.

## Quy ước ước lượng

Giờ là person-hours thực hiện của owner, gồm unit tests và tài liệu đi kèm. Review/hỗ trợ của người khác tính trong P04-12/13/14, không cộng lại vào task được review. Story Points tương đối trong nhóm, không quy đổi cố định sang giờ. Các task kiểm thử cấp hệ thống có đầu ra riêng, không thay thế unit tests. Không bao gồm 3 tool mở rộng hoặc S6/S7.

## Toàn bộ task

| ID | Epic | Task | Owner | Hỗ trợ/review | Ưu tiên | SP | Giờ | Tuần | Phụ thuộc | Trạng thái |
|---|---|---|---|---|---|---:|---:|---|---|---|
| P00-01 | EPIC-00 | Chuẩn hóa repo và quy trình PR | Thủy | Trang | High | 2 | 2 | 1 | Không | TODO |
| P00-02 | EPIC-00 | Chốt kiến trúc và ranh giới tin cậy | Hiền | Thủy, Trang | Critical | 3 | 6 | 1 | Không | TODO |
| P00-03 | EPIC-00 | Contracts cho 12 tool và mock MCP | Thủy | Hiền, Trang | Critical | 5 | 6 | 1 | P00-02 | TODO |
| P00-04 | EPIC-00 | VM snapshot và kiểm kê tài nguyên | Trang | Thủy | Critical | 3 | 6 | 1 | Không | TODO |
| P00-05 | EPIC-00 | Chọn một LLM bằng smoke benchmark | Hiền | Trang | High | 3 | 6 | 1 | P00-04 | TODO |
| P01-01 | EPIC-01 | MCP server và xử lý lỗi dùng chung | Thủy | Hiền | Critical | 5 | 6 | 1 | P00-03 | TODO |
| P01-02 | EPIC-01 | query_journal và search_log | Thủy | Trang | Critical | 5 | 8 | 1 | P01-01, P03-01 | TODO |
| P01-03 | EPIC-01 | service_status và list_failed_units | Thủy | Trang | High | 3 | 4 | 1 | P01-01, P03-01 | TODO |
| P01-04 | EPIC-01 | disk_usage | Thủy | Trang | High | 2 | 4 | 1 | P01-01 | TODO |
| P01-05 | EPIC-01 | listening_ports | Thủy | Trang | High | 3 | 6 | 2 | P01-01, P03-01 | TODO |
| P01-06 | EPIC-01 | check_config | Thủy | Trang | High | 3 | 6 | 2 | P01-01, P03-01 | TODO |
| P01-07 | EPIC-01 | Tích hợp registry đủ 11 read-only tools | Thủy | Hiền, Trang | Critical | 3 | 6 | 2 | P01-02, P01-03, P01-04, P01-05, P01-06, P03-02, P03-03, P02-04, P02-05 | TODO |
| P02-01 | EPIC-02 | MCP client và mock smoke test | Hiền | Thủy | Critical | 3 | 6 | 1 | P00-03 | TODO |
| P02-02 | EPIC-02 | LLM adapter và tool selection | Hiền | Thủy | High | 3 | 4 | 2 | P00-05, P02-01 | TODO |
| P02-03 | EPIC-02 | Reasoning loop nhiều bước có giới hạn | Hiền | Thủy | Critical | 5 | 10 | 2 | P02-02 | TODO |
| P02-04 | EPIC-02 | detect_anomaly | Hiền | Thủy | High | 3 | 6 | 2 | P00-03, P01-02 | TODO |
| P02-05 | EPIC-02 | verify_fix read-only | Hiền | Trang | High | 3 | 6 | 2 | P00-03, P01-03 | TODO |
| P02-06 | EPIC-02 | CLI và báo cáo sự cố Markdown | Hiền | Trang | High | 3 | 4 | 2 | P02-03 | TODO |
| P02-07 | EPIC-02 | UI đề xuất và xác nhận hành động | Hiền | Trang | Critical | 3 | 6 | 3 | P02-06, P03-04 | TODO |
| P02-08 | EPIC-02 | Luồng restart rồi verify và incident report | Hiền | Trang, Thủy | Critical | 3 | 6 | 3 | P02-07, P03-05, P02-05 | TODO |
| P03-01 | EPIC-03 | Quyền đọc và fixture Linux nền tảng | Trang | Thủy | Critical | 3 | 6 | 1 | P00-04, P00-02 | TODO |
| P03-02 | EPIC-03 | memory_oom_events là tool thứ sáu tuần 1 | Trang | Thủy | High | 3 | 6 | 1 | P03-01, P01-01 | TODO |
| P03-03 | EPIC-03 | failed_logins | Trang | Thủy | High | 3 | 6 | 2 | P03-01, P01-01 | TODO |
| P03-04 | EPIC-03 | Chính sách approval và giao thức helper | Trang | Hiền, Thủy | Critical | 5 | 10 | 3 | P00-02, P00-03, P03-01 | TODO |
| P03-05 | EPIC-03 | Privileged helper và restart_service | Trang | Thủy | Critical | 5 | 10 | 3 | P03-04 | TODO |
| P03-06 | EPIC-03 | Hardening systemd và audit | Trang | Thủy | Critical | 3 | 6 | 3 | P03-05 | TODO |
| P03-07 | EPIC-03 | Lab S1–S5 và bộ đáp án chuẩn | Trang | Hiền | High | 5 | 10 | 2 | P00-04, P03-01 | TODO |
| P04-01 | EPIC-04 | Gate tuần 1 với sáu read-only tools | Thủy | Hiền, Trang | Critical | 2 | 4 | 1 | P01-02, P01-03, P01-04, P03-02, P02-01 | TODO |
| P04-02 | EPIC-04 | Bộ test contract và hồi quy MCP | Thủy | Trang | High | 3 | 6 | 3 | P01-07 | TODO |
| P04-03 | EPIC-04 | Gate tuần 2 và chẩn đoán đa bước | Hiền | Thủy, Trang | Critical | 2 | 4 | 2 | P01-07, P02-06, P03-07 | TODO |
| P04-04 | EPIC-04 | Security tests và gate đủ 12 tools | Thủy | Trang, Hiền | Critical | 5 | 10 | 3 | P04-02, P02-08, P03-06, P03-07 | TODO |
| P04-05 | EPIC-04 | Khóa giao thức đánh giá và thu 15 lượt | Trang | Hiền | Critical | 5 | 10 | 4 | P04-04 | TODO |
| P04-06 | EPIC-04 | Tính metric và phân tích lỗi | Hiền | Thủy | High | 3 | 6 | 4 | P04-05 | TODO |
| P04-07 | EPIC-04 | Ổn định MCP và hướng dẫn chạy | Thủy | Trang | High | 3 | 6 | 4 | P04-04 | TODO |
| P04-08 | EPIC-04 | Ổn định Agent và báo cáo kỹ thuật tiếng Anh | Hiền | Thủy | High | 3 | 6 | 4 | P04-06 | TODO |
| P04-09 | EPIC-04 | Phần MCP trong báo cáo và slide tiếng Anh | Thủy | Hiền | High | 3 | 6 | 4 | P04-07 | TODO |
| P04-10 | EPIC-04 | Phần Security Evaluation và runbook demo | Trang | Thủy | High | 3 | 6 | 4 | P04-05, P03-06 | TODO |
| P04-11 | EPIC-04 | Diễn tập bảo vệ và nghiệm thu cuối | Trang | Hiền, Thủy | Critical | 2 | 4 | 4 | P04-08, P04-09, P04-10 | TODO |
| P04-12 | EPIC-04 | Review chéo và điều phối của Thủy | Thủy | Trang | High | 3 | 8 | 1–4 | Không | TODO |
| P04-13 | EPIC-04 | Review chéo và điều phối của Hiền | Hiền | Thủy | High | 3 | 8 | 1–4 | Không | TODO |
| P04-14 | EPIC-04 | Review chéo và điều phối của Trang | Trang | Hiền | High | 3 | 8 | 1–4 | Không | TODO |

## Chi tiết và subtask

### P00-01 — Chuẩn hóa repo và quy trình PR

- Epic: EPIC-00; owner duy nhất: Thủy; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 2; ước lượng: 2 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: Không.
- Mô tả: Kiểm kê repo, đề xuất cấu trúc thư mục, template PR và quy tắc branch theo Task ID. Giới hạn ước lượng: tái sử dụng official SDK, một envelope chung và fixtures tối thiểu; không xây framework hoặc UI tùy biến.
- Deliverables: Quy ước repo, PR checklist, ma trận tài liệu.
- Acceptance Criteria: README hiện có được bảo toàn; phân biệt tài liệu kế hoạch và tính năng chưa triển khai.
- Phương pháp kiểm thử: Review chéo cấu trúc và thử tạo PR mẫu cục bộ.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P00-01.1`: Chốt thiết kế/đầu vào cho **Chuẩn hóa repo và quy trình PR**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: README hiện có được bảo toàn; phân biệt tài liệu kế hoạch và tính năng chưa triển khai.
2. `P00-01.2`: Tạo và bàn giao: Quy ước repo, PR checklist, ma trận tài liệu. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P00-01.3`: Kiểm chứng bằng: Review chéo cấu trúc và thử tạo PR mẫu cục bộ. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P00-02 — Chốt kiến trúc và ranh giới tin cậy

- Epic: EPIC-00; owner duy nhất: Hiền; hỗ trợ/review: Thủy, Trang.
- Ưu tiên: Critical; SP: 3; ước lượng: 6 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: Không.
- Mô tả: Vẽ CLI–Agent–MCP–Linux và đường approval–helper riêng; chốt transport, cấu trúc module, dữ liệu không tin cậy.
- Deliverables: ADR kiến trúc và sơ đồ luồng đọc/ghi.
- Acceptance Criteria: Không có đường shell tự do từ LLM; MCP reader không có sudo; cả ba review.
- Phương pháp kiểm thử: Walkthrough một luồng đọc và một yêu cầu restart bị từ chối.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P00-02.1`: Chốt thiết kế/đầu vào cho **Chốt kiến trúc và ranh giới tin cậy**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Không có đường shell tự do từ LLM; MCP reader không có sudo; cả ba review.
2. `P00-02.2`: Tạo và bàn giao: ADR kiến trúc và sơ đồ luồng đọc/ghi. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P00-02.3`: Kiểm chứng bằng: Walkthrough một luồng đọc và một yêu cầu restart bị từ chối. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P00-03 — Contracts cho 12 tool và mock MCP

- Epic: EPIC-00; owner duy nhất: Thủy; hỗ trợ/review: Hiền, Trang.
- Ưu tiên: Critical; SP: 5; ước lượng: 6 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-02.
- Mô tả: Chốt schema request/response, timestamp, unit, evidence, command_source, error_code; phân trang/giới hạn, timeout, lỗi quyền; mock cả 12 tool. Giới hạn ước lượng: tái sử dụng official SDK, một envelope chung và fixtures tối thiểu; không xây framework hoặc UI tùy biến.
- Deliverables: 12 contracts, ví dụ thành công/lỗi và mock endpoint.
- Acceptance Criteria: 11 R + 1 W; hợp đồng detect_anomaly/verify_fix do Hiền xác nhận, hai tool Linux do Trang xác nhận; mock write không ghi thật.
- Phương pháp kiểm thử: Schema validation cho từng ví dụ; Hiền gọi mock qua client.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P00-03.1`: Chốt thiết kế/đầu vào cho **Contracts cho 12 tool và mock MCP**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: 11 R + 1 W; hợp đồng detect_anomaly/verify_fix do Hiền xác nhận, hai tool Linux do Trang xác nhận; mock write không ghi thật.
2. `P00-03.2`: Tạo và bàn giao: 12 contracts, ví dụ thành công/lỗi và mock endpoint. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P00-03.3`: Kiểm chứng bằng: Schema validation cho từng ví dụ; Hiền gọi mock qua client. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P00-04 — VM snapshot và kiểm kê tài nguyên

- Epic: EPIC-00; owner duy nhất: Trang; hỗ trợ/review: Thủy.
- Ưu tiên: Critical; SP: 3; ước lượng: 6 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: Không.
- Mô tả: Đo RAM/CPU/đĩa và khả năng chạy VM; tạo Ubuntu LTS VM, snapshot sạch, mạng lab và giới hạn tài nguyên.
- Deliverables: Biên bản tài nguyên, snapshot ID, hướng dẫn restore.
- Acceptance Criteria: Thử restore thành công; không tạo sự cố trên Ubuntu host; xác định khả năng VM và model chạy đồng thời.
- Phương pháp kiểm thử: Khôi phục snapshot và kiểm tra dịch vụ lab sạch.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P00-04.1`: Chốt thiết kế/đầu vào cho **VM snapshot và kiểm kê tài nguyên**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Thử restore thành công; không tạo sự cố trên Ubuntu host; xác định khả năng VM và model chạy đồng thời.
2. `P00-04.2`: Tạo và bàn giao: Biên bản tài nguyên, snapshot ID, hướng dẫn restore. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P00-04.3`: Kiểm chứng bằng: Khôi phục snapshot và kiểm tra dịch vụ lab sạch. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P00-05 — Chọn một LLM bằng smoke benchmark

- Epic: EPIC-00; owner duy nhất: Hiền; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-04.
- Mô tả: So sánh lựa chọn local nhỏ hoặc API theo máy thực tế; đo tool calling với mock, chi phí và lọc dữ liệu.
- Deliverables: ADR model chính, phiên bản, cấu hình, kết quả smoke test.
- Acceptance Criteria: Chọn đúng một model cho baseline; không cam kết 7B–8B khi chưa đo; ghi điều kiện API và ngân sách.
- Phương pháp kiểm thử: Chạy bộ câu hỏi cố định trên mock; ghi latency và tool call hợp lệ.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P00-05.1`: Chốt thiết kế/đầu vào cho **Chọn một LLM bằng smoke benchmark**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Chọn đúng một model cho baseline; không cam kết 7B–8B khi chưa đo; ghi điều kiện API và ngân sách.
2. `P00-05.2`: Tạo và bàn giao: ADR model chính, phiên bản, cấu hình, kết quả smoke test. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P00-05.3`: Kiểm chứng bằng: Chạy bộ câu hỏi cố định trên mock; ghi latency và tool call hợp lệ. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P01-01 — MCP server và xử lý lỗi dùng chung

- Epic: EPIC-01; owner duy nhất: Thủy; hỗ trợ/review: Hiền.
- Ưu tiên: Critical; SP: 5; ước lượng: 6 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-03.
- Mô tả: Dựng MCP server ít quyền; wrapper timeout, giới hạn output, validate tham số và lỗi chuẩn hóa. Giới hạn ước lượng: tái sử dụng official SDK, một envelope chung và fixtures tối thiểu; không xây framework hoặc UI tùy biến.
- Deliverables: Server scaffold và bộ test contract.
- Acceptance Criteria: Client mock/real handshake được; không shell=True; lỗi không lộ secret.
- Phương pháp kiểm thử: Unit test invalid input, timeout, output lớn và kết nối client.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P01-01.1`: Chốt thiết kế/đầu vào cho **MCP server và xử lý lỗi dùng chung**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Client mock/real handshake được; không shell=True; lỗi không lộ secret.
2. `P01-01.2`: Tạo và bàn giao: Server scaffold và bộ test contract. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P01-01.3`: Kiểm chứng bằng: Unit test invalid input, timeout, output lớn và kết nối client. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P01-02 — query_journal và search_log

- Epic: EPIC-01; owner duy nhất: Thủy; hỗ trợ/review: Trang.
- Ưu tiên: Critical; SP: 5; ước lượng: 8 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P01-01, P03-01.
- Mô tả: Hai tool đọc journal/file allowlist; lọc unit/thời gian/mức độ; chặn traversal và regex tốn tài nguyên. Giới hạn ước lượng: tái sử dụng official SDK, một envelope chung và fixtures tối thiểu; không xây framework hoặc UI tùy biến.
- Deliverables: Hai tool và fixture journal/file.
- Acceptance Criteria: Có timestamp/evidence/source; xử lý thiếu quyền, file xoay vòng và giới hạn đầu ra.
- Phương pháp kiểm thử: Unit tests hợp lệ, không có kết quả, traversal, regex quá hạn; smoke trong VM.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P01-02.1`: Chốt thiết kế/đầu vào cho **query_journal và search_log**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Có timestamp/evidence/source; xử lý thiếu quyền, file xoay vòng và giới hạn đầu ra.
2. `P01-02.2`: Tạo và bàn giao: Hai tool và fixture journal/file. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P01-02.3`: Kiểm chứng bằng: Unit tests hợp lệ, không có kết quả, traversal, regex quá hạn; smoke trong VM. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P01-03 — service_status và list_failed_units

- Epic: EPIC-01; owner duy nhất: Thủy; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 3; ước lượng: 4 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P01-01, P03-01.
- Mô tả: Đọc trạng thái, exit code, restart count và danh sách unit lỗi; chuẩn hóa trạng thái không tồn tại. Giới hạn ước lượng: tái sử dụng official SDK, một envelope chung và fixtures tối thiểu; không xây framework hoặc UI tùy biến.
- Deliverables: Hai tool trạng thái systemd.
- Acceptance Criteria: Unit không tồn tại khác trạng thái failed; parser không phụ thuộc màu/TTY.
- Phương pháp kiểm thử: Fixture active/failed/missing và smoke dịch vụ lab.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P01-03.1`: Chốt thiết kế/đầu vào cho **service_status và list_failed_units**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Unit không tồn tại khác trạng thái failed; parser không phụ thuộc màu/TTY.
2. `P01-03.2`: Tạo và bàn giao: Hai tool trạng thái systemd. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P01-03.3`: Kiểm chứng bằng: Fixture active/failed/missing và smoke dịch vụ lab. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P01-04 — disk_usage

- Epic: EPIC-01; owner duy nhất: Thủy; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 2; ước lượng: 4 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P01-01.
- Mô tả: Đọc filesystem và inode, chuẩn hóa đơn vị, hạn chế phạm vi mount.
- Deliverables: Tool disk_usage và fixtures.
- Acceptance Criteria: Phân biệt dung lượng với inode; không ghi hoặc tạo đầy đĩa.
- Phương pháp kiểm thử: Fixture mount có khoảng trắng, inode gần đầy và smoke read-only.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P01-04.1`: Chốt thiết kế/đầu vào cho **disk_usage**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Phân biệt dung lượng với inode; không ghi hoặc tạo đầy đĩa.
2. `P01-04.2`: Tạo và bàn giao: Tool disk_usage và fixtures. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P01-04.3`: Kiểm chứng bằng: Fixture mount có khoảng trắng, inode gần đầy và smoke read-only. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P01-05 — listening_ports

- Epic: EPIC-01; owner duy nhất: Thủy; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P01-01, P03-01.
- Mô tả: Đọc cổng/protocol/address; PID chỉ khi có quyền; hỗ trợ IPv4/IPv6.
- Deliverables: Tool listening_ports và test parser.
- Acceptance Criteria: Thiếu quyền PID không làm hỏng toàn bộ kết quả; nguồn lệnh được ghi rõ.
- Phương pháp kiểm thử: Fixtures IPv4/IPv6, thiếu PID và cổng xung đột trong VM.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P01-05.1`: Chốt thiết kế/đầu vào cho **listening_ports**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Thiếu quyền PID không làm hỏng toàn bộ kết quả; nguồn lệnh được ghi rõ.
2. `P01-05.2`: Tạo và bàn giao: Tool listening_ports và test parser. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P01-05.3`: Kiểm chứng bằng: Fixtures IPv4/IPv6, thiếu PID và cổng xung đột trong VM. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P01-06 — check_config

- Epic: EPIC-01; owner duy nhất: Thủy; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P01-01, P03-01.
- Mô tả: Kiểm tra cú pháp qua executable/arguments allowlist; chốt ranh giới với Linux hardening.
- Deliverables: Tool check_config và cấu hình allowlist.
- Acceptance Criteria: Nginx hợp lệ/sai cấu hình trả kết quả phân biệt; không reload/restart; chặn executable tùy ý.
- Phương pháp kiểm thử: Mock exit codes, timeout, injection; nginx -t trong VM.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P01-06.1`: Chốt thiết kế/đầu vào cho **check_config**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Nginx hợp lệ/sai cấu hình trả kết quả phân biệt; không reload/restart; chặn executable tùy ý.
2. `P01-06.2`: Tạo và bàn giao: Tool check_config và cấu hình allowlist. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P01-06.3`: Kiểm chứng bằng: Mock exit codes, timeout, injection; nginx -t trong VM. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P01-07 — Tích hợp registry đủ 11 read-only tools

- Epic: EPIC-01; owner duy nhất: Thủy; hỗ trợ/review: Hiền, Trang.
- Ưu tiên: Critical; SP: 3; ước lượng: 6 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P01-02, P01-03, P01-04, P01-05, P01-06, P03-02, P03-03, P02-04, P02-05.
- Mô tả: Đăng ký tools từ ba owner, thống nhất envelope và phân loại read/write.
- Deliverables: Manifest 11 R và compatibility report.
- Acceptance Criteria: 11 tool discoverable/gọi được; mỗi tool có schema, timeout, giới hạn và unit tests.
- Phương pháp kiểm thử: Contract suite toàn bộ 11 R và lỗi quyền trên VM.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P01-07.1`: Chốt thiết kế/đầu vào cho **Tích hợp registry đủ 11 read-only tools**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: 11 tool discoverable/gọi được; mỗi tool có schema, timeout, giới hạn và unit tests.
2. `P01-07.2`: Tạo và bàn giao: Manifest 11 R và compatibility report. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P01-07.3`: Kiểm chứng bằng: Contract suite toàn bộ 11 R và lỗi quyền trên VM. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P02-01 — MCP client và mock smoke test

- Epic: EPIC-02; owner duy nhất: Hiền; hỗ trợ/review: Thủy.
- Ưu tiên: Critical; SP: 3; ước lượng: 6 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-03.
- Mô tả: Client discovery/call tool, quản lý kết nối, timeout và lỗi; gọi mock trước server thật.
- Deliverables: MCP client, test adapter.
- Acceptance Criteria: Gọi được mock 12 tools; gọi ít nhất một tool server thật để qua gate tuần 1.
- Phương pháp kiểm thử: Unit test disconnect, lỗi schema và handshake end-to-end với P01-01.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P02-01.1`: Chốt thiết kế/đầu vào cho **MCP client và mock smoke test**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Gọi được mock 12 tools; gọi ít nhất một tool server thật để qua gate tuần 1.
2. `P02-01.2`: Tạo và bàn giao: MCP client, test adapter. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P02-01.3`: Kiểm chứng bằng: Unit test disconnect, lỗi schema và handshake end-to-end với P01-01. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P02-02 — LLM adapter và tool selection

- Epic: EPIC-02; owner duy nhất: Hiền; hỗ trợ/review: Thủy.
- Ưu tiên: High; SP: 3; ước lượng: 4 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-05, P02-01.
- Mô tả: Tích hợp một model; chuyển tool schemas, validate structured output và lọc dữ liệu trước API. Giới hạn ước lượng: tái sử dụng official SDK, một envelope chung và fixtures tối thiểu; không xây framework hoặc UI tùy biến.
- Deliverables: Adapter LLM và prompt/version config.
- Acceptance Criteria: Không nhận tên tool/arguments ngoài schema; log không thể thay đổi policy.
- Phương pháp kiểm thử: LLM stub sai JSON/tool/arguments; smoke model đã chọn.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P02-02.1`: Chốt thiết kế/đầu vào cho **LLM adapter và tool selection**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Không nhận tên tool/arguments ngoài schema; log không thể thay đổi policy.
2. `P02-02.2`: Tạo và bàn giao: Adapter LLM và prompt/version config. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P02-02.3`: Kiểm chứng bằng: LLM stub sai JSON/tool/arguments; smoke model đã chọn. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P02-03 — Reasoning loop nhiều bước có giới hạn

- Epic: EPIC-02; owner duy nhất: Hiền; hỗ trợ/review: Thủy.
- Ưu tiên: Critical; SP: 5; ước lượng: 10 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P02-02.
- Mô tả: Vòng lặp tool call–observation–diagnosis; budget bước/token/thời gian, xử lý lỗi và thiếu bằng chứng.
- Deliverables: Agent loop và trace có evidence IDs.
- Acceptance Criteria: Chẩn đoán nhiều bước trên ít nhất 3 tình huống; kết thúc có kiểm soát; không tự duyệt write.
- Phương pháp kiểm thử: Mock tool lỗi/lặp; fixtures S1/S2/S4 và prompt injection từ log.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P02-03.1`: Chốt thiết kế/đầu vào cho **Reasoning loop nhiều bước có giới hạn**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Chẩn đoán nhiều bước trên ít nhất 3 tình huống; kết thúc có kiểm soát; không tự duyệt write.
2. `P02-03.2`: Tạo và bàn giao: Agent loop và trace có evidence IDs. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P02-03.3`: Kiểm chứng bằng: Mock tool lỗi/lặp; fixtures S1/S2/S4 và prompt injection từ log. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P02-04 — detect_anomaly

- Epic: EPIC-02; owner duy nhất: Hiền; hỗ trợ/review: Thủy.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-03, P01-02.
- Mô tả: So sánh tần suất lỗi giữa hai cửa sổ; xác định baseline rỗng, timezone và ngưỡng.
- Deliverables: Tool read-only detect_anomaly và tests.
- Acceptance Criteria: Kết quả có số đếm, cửa sổ, ngưỡng, evidence; không khẳng định nguyên nhân chỉ từ anomaly.
- Phương pháp kiểm thử: Dữ liệu tổng hợp tăng/ổn định/zero baseline và biên thời gian.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P02-04.1`: Chốt thiết kế/đầu vào cho **detect_anomaly**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Kết quả có số đếm, cửa sổ, ngưỡng, evidence; không khẳng định nguyên nhân chỉ từ anomaly.
2. `P02-04.2`: Tạo và bàn giao: Tool read-only detect_anomaly và tests. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P02-04.3`: Kiểm chứng bằng: Dữ liệu tổng hợp tăng/ổn định/zero baseline và biên thời gian. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P02-05 — verify_fix read-only

- Epic: EPIC-02; owner duy nhất: Hiền; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-03, P01-03.
- Mô tả: Kiểm chứng trạng thái service, health endpoint allowlist và log mới theo mốc hành động; giai đoạn này dùng mock action.
- Deliverables: Tool verify_fix và test windows.
- Acceptance Criteria: Không ghi; phân biệt pass/fail/unknown; service active chưa đủ chứng minh health tốt.
- Phương pháp kiểm thử: Fixtures active nhưng HTTP lỗi, log cũ, timeout và trạng thái không xác định.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P02-05.1`: Chốt thiết kế/đầu vào cho **verify_fix read-only**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Không ghi; phân biệt pass/fail/unknown; service active chưa đủ chứng minh health tốt.
2. `P02-05.2`: Tạo và bàn giao: Tool verify_fix và test windows. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P02-05.3`: Kiểm chứng bằng: Fixtures active nhưng HTTP lỗi, log cũ, timeout và trạng thái không xác định. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P02-06 — CLI và báo cáo sự cố Markdown

- Epic: EPIC-02; owner duy nhất: Hiền; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 3; ước lượng: 4 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P02-03.
- Mô tả: CLI nhận câu hỏi, hiển thị bằng chứng, thoát khi hủy; xuất incident report có nguồn và uncertainty. Giới hạn ước lượng: tái sử dụng official SDK, một envelope chung và fixtures tối thiểu; không xây framework hoặc UI tùy biến.
- Deliverables: CLI, mẫu báo cáo sự cố và tests.
- Acceptance Criteria: Không lộ secrets; mỗi kết luận có evidence hoặc ghi thiếu dữ liệu; đọc được trace.
- Phương pháp kiểm thử: CLI tests với LLM stub, output snapshot và kiểm tra redaction.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P02-06.1`: Chốt thiết kế/đầu vào cho **CLI và báo cáo sự cố Markdown**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Không lộ secrets; mỗi kết luận có evidence hoặc ghi thiếu dữ liệu; đọc được trace.
2. `P02-06.2`: Tạo và bàn giao: CLI, mẫu báo cáo sự cố và tests. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P02-06.3`: Kiểm chứng bằng: CLI tests với LLM stub, output snapshot và kiểm tra redaction. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P02-07 — UI đề xuất và xác nhận hành động

- Epic: EPIC-02; owner duy nhất: Hiền; hỗ trợ/review: Trang.
- Ưu tiên: Critical; SP: 3; ước lượng: 6 giờ; tuần: 3; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P02-06, P03-04.
- Mô tả: CLI hiển thị action/unit/impact/expiry; chỉ người tương tác duyệt; gửi approval tới coordinator tin cậy.
- Deliverables: Approval interaction và integration tests.
- Acceptance Criteria: Nội dung log hoặc LLM không tạo approval hợp lệ; cancel/expiry từ chối; không tự cấp token.
- Phương pháp kiểm thử: Test hủy, hết hạn, action bị thay tham số và input không tương tác.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P02-07.1`: Chốt thiết kế/đầu vào cho **UI đề xuất và xác nhận hành động**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Nội dung log hoặc LLM không tạo approval hợp lệ; cancel/expiry từ chối; không tự cấp token.
2. `P02-07.2`: Tạo và bàn giao: Approval interaction và integration tests. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P02-07.3`: Kiểm chứng bằng: Test hủy, hết hạn, action bị thay tham số và input không tương tác. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P02-08 — Luồng restart rồi verify và incident report

- Epic: EPIC-02; owner duy nhất: Hiền; hỗ trợ/review: Trang, Thủy.
- Ưu tiên: Critical; SP: 3; ước lượng: 6 giờ; tuần: 3; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P02-07, P03-05, P02-05.
- Mô tả: Nối action ID, kết quả helper và verify_fix; cập nhật báo cáo bằng evidence sau hành động.
- Deliverables: Trace end-to-end và report sau restart.
- Acceptance Criteria: Restart thất bại không bị ghi là đã sửa; verify dùng mốc mới; không tự retry ghi.
- Phương pháp kiểm thử: E2E duyệt/từ chối/helper lỗi/health fail trong VM.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P02-08.1`: Chốt thiết kế/đầu vào cho **Luồng restart rồi verify và incident report**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Restart thất bại không bị ghi là đã sửa; verify dùng mốc mới; không tự retry ghi.
2. `P02-08.2`: Tạo và bàn giao: Trace end-to-end và report sau restart. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P02-08.3`: Kiểm chứng bằng: E2E duyệt/từ chối/helper lỗi/health fail trong VM. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P03-01 — Quyền đọc và fixture Linux nền tảng

- Epic: EPIC-03; owner duy nhất: Trang; hỗ trợ/review: Thủy.
- Ưu tiên: Critical; SP: 3; ước lượng: 6 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-04, P00-02.
- Mô tả: Tạo mcp-log-reader, quyền journal/file tối thiểu, fixture systemd/journal/RAM; rà log nhạy cảm.
- Deliverables: Permission matrix, fixtures và runbook.
- Acceptance Criteria: Có thể đọc dữ liệu cần thiết mà không sudo; chỉ rõ dữ liệu không đọc được; không nới quyền host.
- Phương pháp kiểm thử: Chạy probe bằng user ít quyền trong VM, kiểm tra file ngoài allowlist bị chặn.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P03-01.1`: Chốt thiết kế/đầu vào cho **Quyền đọc và fixture Linux nền tảng**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Có thể đọc dữ liệu cần thiết mà không sudo; chỉ rõ dữ liệu không đọc được; không nới quyền host.
2. `P03-01.2`: Tạo và bàn giao: Permission matrix, fixtures và runbook. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P03-01.3`: Kiểm chứng bằng: Chạy probe bằng user ít quyền trong VM, kiểm tra file ngoài allowlist bị chặn. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P03-02 — memory_oom_events là tool thứ sáu tuần 1

- Epic: EPIC-03; owner duy nhất: Trang; hỗ trợ/review: Thủy.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P03-01, P01-01.
- Mô tả: Đọc journal/kernel OOM và tình trạng RAM; dùng fixture OOM thay vì gây OOM tuần 1.
- Deliverables: Tool memory_oom_events và test parser.
- Acceptance Criteria: Trả tiến trình/thời gian/bằng chứng khi có; phân biệt không có OOM và thiếu quyền.
- Phương pháp kiểm thử: Unit fixtures OOM/không OOM/permission denied; smoke RAM read-only.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P03-02.1`: Chốt thiết kế/đầu vào cho **memory_oom_events là tool thứ sáu tuần 1**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Trả tiến trình/thời gian/bằng chứng khi có; phân biệt không có OOM và thiếu quyền.
2. `P03-02.2`: Tạo và bàn giao: Tool memory_oom_events và test parser. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P03-02.3`: Kiểm chứng bằng: Unit fixtures OOM/không OOM/permission denied; smoke RAM read-only. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P03-03 — failed_logins

- Epic: EPIC-03; owner duy nhất: Trang; hỗ trợ/review: Thủy.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P03-01, P01-01.
- Mô tả: Đọc journal hoặc auth.log, thống kê theo cửa sổ/IP pseudonym ổn định, chống đếm trùng.
- Deliverables: Tool failed_logins và fixtures.
- Acceptance Criteria: Fallback nguồn rõ ràng; IP gốc chỉ giữ cục bộ; không sinh brute-force trên host.
- Phương pháp kiểm thử: Fixtures journal/auth.log, trùng sự kiện, rotation và pseudonym ổn định.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P03-03.1`: Chốt thiết kế/đầu vào cho **failed_logins**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Fallback nguồn rõ ràng; IP gốc chỉ giữ cục bộ; không sinh brute-force trên host.
2. `P03-03.2`: Tạo và bàn giao: Tool failed_logins và fixtures. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P03-03.3`: Kiểm chứng bằng: Fixtures journal/auth.log, trùng sự kiện, rotation và pseudonym ổn định. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P03-04 — Chính sách approval và giao thức helper

- Epic: EPIC-03; owner duy nhất: Trang; hỗ trợ/review: Hiền, Thủy.
- Ưu tiên: Critical; SP: 5; ước lượng: 10 giờ; tuần: 3; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-02, P00-03, P03-01.
- Mô tả: Thiết kế/triển khai backend phê duyệt độc lập: action ID, tham số chuẩn hóa, danh tính, expiry, single-use, peer authentication.
- Deliverables: Policy, protocol và bộ test replay/tampering.
- Acceptance Criteria: Agent/MCP không tự mint approval; đổi unit/params, replay, expiry hoặc sai caller đều bị từ chối.
- Phương pháp kiểm thử: Threat-model walkthrough và negative tests không cần LLM.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P03-04.1`: Chốt thiết kế/đầu vào cho **Chính sách approval và giao thức helper**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Agent/MCP không tự mint approval; đổi unit/params, replay, expiry hoặc sai caller đều bị từ chối.
2. `P03-04.2`: Tạo và bàn giao: Policy, protocol và bộ test replay/tampering. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P03-04.3`: Kiểm chứng bằng: Threat-model walkthrough và negative tests không cần LLM. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P03-05 — Privileged helper và restart_service

- Epic: EPIC-03; owner duy nhất: Trang; hỗ trợ/review: Thủy.
- Ưu tiên: Critical; SP: 5; ước lượng: 10 giờ; tuần: 3; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P03-04.
- Mô tả: Helper riêng có giao diện restart hẹp; unit allowlist, kiểm approval tại thực thi, audit; đăng ký MCP facade W.
- Deliverables: Helper, tool restart_service, unit và integration tests.
- Acceptance Criteria: Không sudo toàn cục; không shell tự do; không write nếu chưa duyệt; đúng unit đã duyệt, kết quả gắn action ID.
- Phương pháp kiểm thử: VM test valid approval, forged token, replay, unit ngoài allowlist và helper unavailable.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P03-05.1`: Chốt thiết kế/đầu vào cho **Privileged helper và restart_service**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Không sudo toàn cục; không shell tự do; không write nếu chưa duyệt; đúng unit đã duyệt, kết quả gắn action ID.
2. `P03-05.2`: Tạo và bàn giao: Helper, tool restart_service, unit và integration tests. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P03-05.3`: Kiểm chứng bằng: VM test valid approval, forged token, replay, unit ngoài allowlist và helper unavailable. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P03-06 — Hardening systemd và audit

- Epic: EPIC-03; owner duy nhất: Trang; hỗ trợ/review: Thủy.
- Ưu tiên: Critical; SP: 3; ước lượng: 6 giờ; tuần: 3; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P03-05.
- Mô tả: MCP reader dùng NoNewPrivileges=yes, ProtectSystem/ProtectHome phù hợp; helper tách riêng, audit đủ trường.
- Deliverables: Systemd deployment notes, quyền socket và audit schema.
- Acceptance Criteria: Reader vẫn đọc được tool cần thiết; không gọi sudo từ reader; audit có người duyệt, action, params, thời gian, kết quả, verify link.
- Phương pháp kiểm thử: Kiểm tra quyền thực tế dưới service identity và audit cho pass/deny/fail.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P03-06.1`: Chốt thiết kế/đầu vào cho **Hardening systemd và audit**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Reader vẫn đọc được tool cần thiết; không gọi sudo từ reader; audit có người duyệt, action, params, thời gian, kết quả, verify link.
2. `P03-06.2`: Tạo và bàn giao: Systemd deployment notes, quyền socket và audit schema. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P03-06.3`: Kiểm chứng bằng: Kiểm tra quyền thực tế dưới service identity và audit cho pass/deny/fail. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P03-07 — Lab S1–S5 và bộ đáp án chuẩn

- Epic: EPIC-03; owner duy nhất: Trang; hỗ trợ/review: Hiền.
- Ưu tiên: High; SP: 5; ước lượng: 10 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P00-04, P03-01.
- Mô tả: Dựng scripts tái lập crash loop, nginx syntax, port conflict, backend 502, phân vùng test gần đầy; reset/cleanup từng ca.
- Deliverables: 5 runbooks, scripts, expected evidence và snapshot IDs.
- Acceptance Criteria: Mỗi ca có trigger, ground truth, giới hạn tài nguyên và reset; S5 dùng đĩa/mount riêng trong VM.
- Phương pháp kiểm thử: Tái lập từng ca rồi restore, đối chiếu evidence; không tác động host.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P03-07.1`: Chốt thiết kế/đầu vào cho **Lab S1–S5 và bộ đáp án chuẩn**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Mỗi ca có trigger, ground truth, giới hạn tài nguyên và reset; S5 dùng đĩa/mount riêng trong VM.
2. `P03-07.2`: Tạo và bàn giao: 5 runbooks, scripts, expected evidence và snapshot IDs. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P03-07.3`: Kiểm chứng bằng: Tái lập từng ca rồi restore, đối chiếu evidence; không tác động host. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-01 — Gate tuần 1 với sáu read-only tools

- Epic: EPIC-04; owner duy nhất: Thủy; hỗ trợ/review: Hiền, Trang.
- Ưu tiên: Critical; SP: 2; ước lượng: 4 giờ; tuần: 1; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P01-02, P01-03, P01-04, P03-02, P02-01.
- Mô tả: Chốt 6 tools: query_journal, search_log, service_status, list_failed_units, disk_usage, memory_oom_events; thu bằng chứng gate.
- Deliverables: Biên bản gate W1 và test results.
- Acceptance Criteria: Sáu tool có unit tests; MCP client gọi server thật thành công; blocker có owner.
- Phương pháp kiểm thử: Chạy contract suite và smoke trên VM bằng user ít quyền.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-01.1`: Chốt thiết kế/đầu vào cho **Gate tuần 1 với sáu read-only tools**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Sáu tool có unit tests; MCP client gọi server thật thành công; blocker có owner.
2. `P04-01.2`: Tạo và bàn giao: Biên bản gate W1 và test results. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-01.3`: Kiểm chứng bằng: Chạy contract suite và smoke trên VM bằng user ít quyền. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-02 — Bộ test contract và hồi quy MCP

- Epic: EPIC-04; owner duy nhất: Thủy; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 3; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P01-07.
- Mô tả: Mở rộng kiểm thử chung cho output limits, invalid args, permission denied, timeout của tools từ cả ba owner.
- Deliverables: Regression suite và báo cáo coverage theo tool.
- Acceptance Criteria: Không tool nào thiếu tests; lỗi thống nhất; fixture không chứa secrets.
- Phương pháp kiểm thử: Chạy bộ test 11 R và contract W dùng fake helper.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-02.1`: Chốt thiết kế/đầu vào cho **Bộ test contract và hồi quy MCP**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Không tool nào thiếu tests; lỗi thống nhất; fixture không chứa secrets.
2. `P04-02.2`: Tạo và bàn giao: Regression suite và báo cáo coverage theo tool. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-02.3`: Kiểm chứng bằng: Chạy bộ test 11 R và contract W dùng fake helper. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-03 — Gate tuần 2 và chẩn đoán đa bước

- Epic: EPIC-04; owner duy nhất: Hiền; hỗ trợ/review: Thủy, Trang.
- Ưu tiên: Critical; SP: 2; ước lượng: 4 giờ; tuần: 2; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P01-07, P02-06, P03-07.
- Mô tả: Chạy S1/S2/S4 qua CLI và rà evidence; xác nhận 11 R và backlog tuần 3.
- Deliverables: Trace ba tình huống và gate W2.
- Acceptance Criteria: 3 tình huống đều kết thúc có bằng chứng/uncertainty; đủ 11 R; không gọi W thật.
- Phương pháp kiểm thử: VM smoke với một model cố định, review trace thủ công.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-03.1`: Chốt thiết kế/đầu vào cho **Gate tuần 2 và chẩn đoán đa bước**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: 3 tình huống đều kết thúc có bằng chứng/uncertainty; đủ 11 R; không gọi W thật.
2. `P04-03.2`: Tạo và bàn giao: Trace ba tình huống và gate W2. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-03.3`: Kiểm chứng bằng: VM smoke với một model cố định, review trace thủ công. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-04 — Security tests và gate đủ 12 tools

- Epic: EPIC-04; owner duy nhất: Thủy; hỗ trợ/review: Trang, Hiền.
- Ưu tiên: Critical; SP: 5; ước lượng: 10 giờ; tuần: 3; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P04-02, P02-08, P03-06, P03-07.
- Mô tả: Kiểm tra độc lập allowlist, traversal, regex, injection, approval tampering/replay, không approval, đủ 5 tình huống.
- Deliverables: Security matrix, gate W3 và 12-tool manifest.
- Acceptance Criteria: Zero write thành công khi chưa phê duyệt; tests chạy trên deployment thật trong VM; không bypass policy để qua gate.
- Phương pháp kiểm thử: Negative E2E tests và 5 scenario smoke; kiểm audit và process privileges.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-04.1`: Chốt thiết kế/đầu vào cho **Security tests và gate đủ 12 tools**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Zero write thành công khi chưa phê duyệt; tests chạy trên deployment thật trong VM; không bypass policy để qua gate.
2. `P04-04.2`: Tạo và bàn giao: Security matrix, gate W3 và 12-tool manifest. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-04.3`: Kiểm chứng bằng: Negative E2E tests và 5 scenario smoke; kiểm audit và process privileges. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-05 — Khóa giao thức đánh giá và thu 15 lượt

- Epic: EPIC-04; owner duy nhất: Trang; hỗ trợ/review: Hiền.
- Ưu tiên: Critical; SP: 5; ước lượng: 10 giờ; tuần: 4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P04-04.
- Mô tả: Khóa commit/model/prompt/config, chạy S1–S5 mỗi ca 3 lần với reset; thu ground truth, evidence, latency, tool count.
- Deliverables: 15 run records, manifest và raw results đã lọc.
- Acceptance Criteria: 15 lượt đầy đủ, kể cả failed runs; không thay model giữa chừng; retry được ghi rõ.
- Phương pháp kiểm thử: Kiểm đếm 5×3, đối chiếu snapshot IDs và trace từng run.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-05.1`: Chốt thiết kế/đầu vào cho **Khóa giao thức đánh giá và thu 15 lượt**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: 15 lượt đầy đủ, kể cả failed runs; không thay model giữa chừng; retry được ghi rõ.
2. `P04-05.2`: Tạo và bàn giao: 15 run records, manifest và raw results đã lọc. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-05.3`: Kiểm chứng bằng: Kiểm đếm 5×3, đối chiếu snapshot IDs và trace từng run. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-06 — Tính metric và phân tích lỗi

- Epic: EPIC-04; owner duy nhất: Hiền; hỗ trợ/review: Thủy.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P04-05.
- Mô tả: Chấm root cause, evidence, median tool count/latency, safety, approval rejection và verify success; mẫu số riêng rõ.
- Deliverables: Bảng kết quả và phân tích nguyên nhân sai.
- Acceptance Criteria: ≥80% accuracy là mục tiêu; không bịa kết quả; đối chiếu 15 lượt và ghi denominators cho write tests.
- Phương pháp kiểm thử: Tính lại từ raw data; reviewer chấm lại mẫu và mọi trường hợp bất đồng.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-06.1`: Chốt thiết kế/đầu vào cho **Tính metric và phân tích lỗi**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: ≥80% accuracy là mục tiêu; không bịa kết quả; đối chiếu 15 lượt và ghi denominators cho write tests.
2. `P04-06.2`: Tạo và bàn giao: Bảng kết quả và phân tích nguyên nhân sai. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-06.3`: Kiểm chứng bằng: Tính lại từ raw data; reviewer chấm lại mẫu và mọi trường hợp bất đồng. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-07 — Ổn định MCP và hướng dẫn chạy

- Epic: EPIC-04; owner duy nhất: Thủy; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P04-04.
- Mô tả: Ngân sách sửa lỗi MCP, hướng dẫn cài, quyền đọc, schema và troubleshooting; giữ version trace.
- Deliverables: MCP fixes nếu cần, README quickstart và tool guide tiếng Anh.
- Acceptance Criteria: Người khác chạy theo hướng dẫn từ snapshot sạch; thay đổi tác động evaluation phải được rerun.
- Phương pháp kiểm thử: Regression suite MCP và clean setup smoke.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-07.1`: Chốt thiết kế/đầu vào cho **Ổn định MCP và hướng dẫn chạy**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Người khác chạy theo hướng dẫn từ snapshot sạch; thay đổi tác động evaluation phải được rerun.
2. `P04-07.2`: Tạo và bàn giao: MCP fixes nếu cần, README quickstart và tool guide tiếng Anh. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-07.3`: Kiểm chứng bằng: Regression suite MCP và clean setup smoke. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-08 — Ổn định Agent và báo cáo kỹ thuật tiếng Anh

- Epic: EPIC-04; owner duy nhất: Hiền; hỗ trợ/review: Thủy.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P04-06.
- Mô tả: Ngân sách sửa lỗi Agent; viết Introduction, Background, Architecture, Agent, Discussion, Conclusion; ghép phần MCP/Security/Evaluation.
- Deliverables: Báo cáo tiếng Anh hoàn chỉnh và Agent regression results.
- Acceptance Criteria: Đúng 12 tools/5–7 ca/15–21 lượt như thực tế; liên kết evidence, số liệu thống nhất.
- Phương pháp kiểm thử: Review nội dung, kiểm bảng số liệu và regression affected flows.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-08.1`: Chốt thiết kế/đầu vào cho **Ổn định Agent và báo cáo kỹ thuật tiếng Anh**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Đúng 12 tools/5–7 ca/15–21 lượt như thực tế; liên kết evidence, số liệu thống nhất.
2. `P04-08.2`: Tạo và bàn giao: Báo cáo tiếng Anh hoàn chỉnh và Agent regression results. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-08.3`: Kiểm chứng bằng: Review nội dung, kiểm bảng số liệu và regression affected flows. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-09 — Phần MCP trong báo cáo và slide tiếng Anh

- Epic: EPIC-04; owner duy nhất: Thủy; hỗ trợ/review: Hiền.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P04-07.
- Mô tả: Viết MCP implementation, contracts, testing; dựng slide 12–15 trang cùng phần Agent của Hiền và Security của Trang.
- Deliverables: Phần báo cáo MCP và deck tiếng Anh.
- Acceptance Criteria: Tool thật, sơ đồ quyền và kết quả hiện rõ; cả ba kiểm tra số liệu và thuật ngữ.
- Phương pháp kiểm thử: Review slide/báo cáo và diễn tập thời lượng.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-09.1`: Chốt thiết kế/đầu vào cho **Phần MCP trong báo cáo và slide tiếng Anh**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Tool thật, sơ đồ quyền và kết quả hiện rõ; cả ba kiểm tra số liệu và thuật ngữ.
2. `P04-09.2`: Tạo và bàn giao: Phần báo cáo MCP và deck tiếng Anh. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-09.3`: Kiểm chứng bằng: Review slide/báo cáo và diễn tập thời lượng. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-10 — Phần Security Evaluation và runbook demo

- Epic: EPIC-04; owner duy nhất: Trang; hỗ trợ/review: Thủy.
- Ưu tiên: High; SP: 3; ước lượng: 6 giờ; tuần: 4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P04-05, P03-06.
- Mô tả: Viết security, môi trường, kịch bản và phương pháp; thêm kết quả đã được P04-06 chấm; ổn định lab/helper.
- Deliverables: Phần báo cáo Security/Evaluation, demo runbook và safety regression.
- Acceptance Criteria: Nêu giới hạn, fail-safe write disabled nếu không an toàn; demo có restore/cleanup.
- Phương pháp kiểm thử: Peer review và dry-run snapshot sạch, kiểm quyền và approval.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-10.1`: Chốt thiết kế/đầu vào cho **Phần Security Evaluation và runbook demo**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Nêu giới hạn, fail-safe write disabled nếu không an toàn; demo có restore/cleanup.
2. `P04-10.2`: Tạo và bàn giao: Phần báo cáo Security/Evaluation, demo runbook và safety regression. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-10.3`: Kiểm chứng bằng: Peer review và dry-run snapshot sạch, kiểm quyền và approval. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-11 — Diễn tập bảo vệ và nghiệm thu cuối

- Epic: EPIC-04; owner duy nhất: Trang; hỗ trợ/review: Hiền, Thủy.
- Ưu tiên: Critical; SP: 2; ước lượng: 4 giờ; tuần: 4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: P04-08, P04-09, P04-10.
- Mô tả: Điều phối demo; mỗi người trình bày phần chính và trả lời chéo; kiểm tài liệu, test và scope cuối.
- Deliverables: Biên bản rehearsal, checklist bàn giao và hạn chế còn lại.
- Acceptance Criteria: Demo sạch, đủ evidence/test; gate write không đạt thì ghi chưa hoàn thành scope 12 tools và vô hiệu hóa write.
- Phương pháp kiểm thử: Một lượt demo từ snapshot sạch và vấn đáp cả ba; không tính vào 15 lượt baseline.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-11.1`: Chốt thiết kế/đầu vào cho **Diễn tập bảo vệ và nghiệm thu cuối**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: Demo sạch, đủ evidence/test; gate write không đạt thì ghi chưa hoàn thành scope 12 tools và vô hiệu hóa write.
2. `P04-11.2`: Tạo và bàn giao: Biên bản rehearsal, checklist bàn giao và hạn chế còn lại. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-11.3`: Kiểm chứng bằng: Một lượt demo từ snapshot sạch và vấn đáp cả ba; không tính vào 15 lượt baseline. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-12 — Review chéo và điều phối của Thủy

- Epic: EPIC-04; owner duy nhất: Thủy; hỗ trợ/review: Trang.
- Ưu tiên: High; SP: 3; ước lượng: 8 giờ; tuần: 1–4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: Không.
- Mô tả: Thực hiện 2 giờ mỗi tuần. Điều phối contracts, review PR Agent/Security và kiểm tính nhất quán tool schema.
- Deliverables: Review log theo Task ID, integration notes và biên bản họp.
- Acceptance Criteria: 8 giờ được phân bổ 2 giờ/tuần; review của owner trên PR người khác; không tự approve PR.
- Phương pháp kiểm thử: Kiểm review checklist, blockers có owner và quyết định được ghi lại.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-12.1`: Chốt thiết kế/đầu vào cho **Review chéo và điều phối của Thủy**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: 8 giờ được phân bổ 2 giờ/tuần; review của owner trên PR người khác; không tự approve PR.
2. `P04-12.2`: Tạo và bàn giao: Review log theo Task ID, integration notes và biên bản họp. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-12.3`: Kiểm chứng bằng: Kiểm review checklist, blockers có owner và quyết định được ghi lại. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-13 — Review chéo và điều phối của Hiền

- Epic: EPIC-04; owner duy nhất: Hiền; hỗ trợ/review: Thủy.
- Ưu tiên: High; SP: 3; ước lượng: 8 giờ; tuần: 1–4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: Không.
- Mô tả: Thực hiện 2 giờ mỗi tuần. Điều phối tích hợp Agent, review PR MCP và cập nhật integration blockers.
- Deliverables: Review log theo Task ID, integration notes và biên bản họp.
- Acceptance Criteria: 8 giờ được phân bổ 2 giờ/tuần; review của owner trên PR người khác; không tự approve PR.
- Phương pháp kiểm thử: Kiểm review checklist, blockers có owner và quyết định được ghi lại.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-13.1`: Chốt thiết kế/đầu vào cho **Review chéo và điều phối của Hiền**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: 8 giờ được phân bổ 2 giờ/tuần; review của owner trên PR người khác; không tự approve PR.
2. `P04-13.2`: Tạo và bàn giao: Review log theo Task ID, integration notes và biên bản họp. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-13.3`: Kiểm chứng bằng: Kiểm review checklist, blockers có owner và quyết định được ghi lại. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

### P04-14 — Review chéo và điều phối của Trang

- Epic: EPIC-04; owner duy nhất: Trang; hỗ trợ/review: Hiền.
- Ưu tiên: High; SP: 3; ước lượng: 8 giờ; tuần: 1–4; trạng thái: TODO.
- Phụ thuộc hoàn thành trước: Không.
- Mô tả: Thực hiện 2 giờ mỗi tuần. Điều phối lịch VM/lab, luân phiên họp nhóm và review PR permissions/security.
- Deliverables: Review log theo Task ID, integration notes và biên bản họp.
- Acceptance Criteria: 8 giờ được phân bổ 2 giờ/tuần; review của owner trên PR người khác; không tự approve PR.
- Phương pháp kiểm thử: Kiểm review checklist, blockers có owner và quyết định được ghi lại.

Subtasks (cùng owner, TODO; đã nằm trong SP/giờ của task cha, không tính thành task riêng):

1. `P04-14.1`: Chốt thiết kế/đầu vào cho **Review chéo và điều phối của Trang**; đầu ra là contract hoặc checklist review được, bao gồm các trường hợp: 8 giờ được phân bổ 2 giờ/tuần; review của owner trên PR người khác; không tự approve PR.
2. `P04-14.2`: Tạo và bàn giao: Review log theo Task ID, integration notes và biên bản họp. Đầu ra có phiên bản và được reviewer kiểm tra độc lập.
3. `P04-14.3`: Kiểm chứng bằng: Kiểm review checklist, blockers có owner và quyết định được ghi lại. Lưu evidence, đối chiếu Acceptance Criteria và reviewer xác nhận.

## Ngoài baseline

build_timeline, check_path_access, rollback_change; SQLite, timer, dashboard, LLM thứ hai và S6/S7 không được tính vào cam kết/giờ/SP. Chỉ thêm task với một owner và ước lượng riêng sau khi nhóm duyệt thay đổi phạm vi. OOM/SSH tools vẫn bắt buộc và có fixture tests dù S6/S7 không chạy thật.
