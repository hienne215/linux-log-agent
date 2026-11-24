# Phụ thuộc và phát triển song song

Bản đề xuất ngày 01/11/2026, chờ nhóm phê duyệt. Tất cả task khởi tạo TODO; tài liệu này không phải bằng chứng tính năng đã triển khai. Tuần 1–4 là tuần tương đối từ ngày khởi động được nhóm xác nhận.

## Quy tắc

Các ID trong TASKS.md là nguồn phụ thuộc chi tiết. Không bắt đầu phần thực thi thật trước prerequisite; được dựng stub/mock theo interface đã chốt. Checklist cùng tuần có thể dùng handoff giữa tuần, không hàm ý mọi task cùng tuần bắt đầu đồng thời. P02-01 dựng client từ mock sau P00-03, kiểm server thật trong P04-01 sau P01-01.

## Chuỗi bắt buộc

- P00-02 → P00-03 → P01-01; schema phải có sự đồng thuận A/B/C ngay ngày đầu trước tích hợp.
- P00-04 → P03-01 → tools cần quyền/fixtures và mọi kịch bản VM.
- P00-03 → P02-01; P00-04 → P00-05; cả hai mở P02-02 → P02-03 → P02-06.
- P01-07 + CLI/lab → P04-03: 11 R và chẩn đoán đa bước.
- P03-04 → P03-05 → P03-06; P03-04 → P02-07; hai nhánh hội tụ P02-08 → P04-04.
- P04-04 → P04-05 → P04-06 → P04-08 → P04-11; các phần slide, MCP và Security hội tụ trước gate cuối.

Đây là các chuỗi rủi ro tiến độ, chưa phải critical path theo lịch ngày chính xác vì chưa có capacity/ngày bắt đầu được xác nhận.

## Các nhánh có thể chạy song song

| Sau mốc | Thủy | Hiền | Trang | Điều kiện handoff |
|---|---|---|---|---|
| Bắt đầu | P00-01 | P00-02 | P00-04 | Cả ba dự phiên kiến trúc/schema ngày đầu |
| Contract ổn định | P01-01 và tools | P02-01 trên mock, benchmark model | P03-01 và fixtures | Không đợi đủ tools mới dựng client |
| Server nền + quyền | P01-02/03/04 | Client smoke | P03-02 | Thứ tự thực tế theo capacity từng owner |
| Tuần 2 | ports/config/registry | adapter/loop/anomaly/verify/CLI | failed_logins và lab S1–S5 | Hiền dùng fixtures để tránh chờ lab hoàn chỉnh |
| Approval protocol | regression/security harness | CLI approval trên fake coordinator | backend/helper | Chỉ thực thi W sau backend gate |
| Tuần 4 sau W3 | MCP docs/stability, slide draft | Agent regression/report draft | 15 lượt đánh giá, demo runbook | Số liệu cuối chờ P04-06; draft báo cáo không chờ evaluation |

Các task cùng owner không được coi là nhân lực chạy đồng thời. Mock approval/helper phải luôn không có tác dụng ghi. Fixtures là dữ liệu không tin cậy; không thực thi chỉ thị trong log.

## Bàn giao

Contracts và mẫu lỗi → Thủy bàn giao Hiền/Trang; VM snapshot và permission matrix → Trang bàn giao Thủy/Hiền; tool modules + tests → từng owner bàn giao Thủy đăng ký; action protocol → Trang bàn giao Hiền; traces/raw run data → Trang bàn giao Hiền chấm; phần MCP → Thủy, Security → Trang, báo cáo tổng → Hiền. Reviewer phải khác owner.

## Danh sách cạnh đầy đủ

| Task | Phụ thuộc phải hoàn thành |
|---|---|
| P00-01 | Không |
| P00-02 | Không |
| P00-03 | P00-02 |
| P00-04 | Không |
| P00-05 | P00-04 |
| P01-01 | P00-03 |
| P01-02 | P01-01, P03-01 |
| P01-03 | P01-01, P03-01 |
| P01-04 | P01-01 |
| P01-05 | P01-01, P03-01 |
| P01-06 | P01-01, P03-01 |
| P01-07 | P01-02, P01-03, P01-04, P01-05, P01-06, P03-02, P03-03, P02-04, P02-05 |
| P02-01 | P00-03 |
| P02-02 | P00-05, P02-01 |
| P02-03 | P02-02 |
| P02-04 | P00-03, P01-02 |
| P02-05 | P00-03, P01-03 |
| P02-06 | P02-03 |
| P02-07 | P02-06, P03-04 |
| P02-08 | P02-07, P03-05, P02-05 |
| P03-01 | P00-04, P00-02 |
| P03-02 | P03-01, P01-01 |
| P03-03 | P03-01, P01-01 |
| P03-04 | P00-02, P00-03, P03-01 |
| P03-05 | P03-04 |
| P03-06 | P03-05 |
| P03-07 | P00-04, P03-01 |
| P04-01 | P01-02, P01-03, P01-04, P03-02, P02-01 |
| P04-02 | P01-07 |
| P04-03 | P01-07, P02-06, P03-07 |
| P04-04 | P04-02, P02-08, P03-06, P03-07 |
| P04-05 | P04-04 |
| P04-06 | P04-05 |
| P04-07 | P04-04 |
| P04-08 | P04-06 |
| P04-09 | P04-07 |
| P04-10 | P04-05, P03-06 |
| P04-11 | P04-08, P04-09, P04-10 |
| P04-12 | Không |
| P04-13 | Không |
| P04-14 | Không |
