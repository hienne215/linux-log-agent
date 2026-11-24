# Sprint plan

Bản đề xuất ngày 01/11/2026, chờ nhóm phê duyệt. Tất cả task khởi tạo TODO; tài liệu này không phải bằng chứng tính năng đã triển khai. Tuần 1–4 là tuần tương đối từ ngày khởi động được nhóm xác nhận.

Bốn sprint một tuần; ngày lịch sẽ gắn sau khi nhóm xác nhận ngày khởi động. Chi tiết đầu ra và tests trong TASKS.md; mỗi sprint kết thúc bằng review gate.

## Tuần 1

| Task | Owner | Giờ | Ưu tiên |
|---|---|---:|---|
| P00-01 — Chuẩn hóa repo và quy trình PR | Thủy | 2 | High |
| P00-02 — Chốt kiến trúc và ranh giới tin cậy | Hiền | 6 | Critical |
| P00-03 — Contracts cho 12 tool và mock MCP | Thủy | 6 | Critical |
| P00-04 — VM snapshot và kiểm kê tài nguyên | Trang | 6 | Critical |
| P00-05 — Chọn một LLM bằng smoke benchmark | Hiền | 6 | High |
| P01-01 — MCP server và xử lý lỗi dùng chung | Thủy | 6 | Critical |
| P01-02 — query_journal và search_log | Thủy | 8 | Critical |
| P01-03 — service_status và list_failed_units | Thủy | 4 | High |
| P01-04 — disk_usage | Thủy | 4 | High |
| P02-01 — MCP client và mock smoke test | Hiền | 6 | Critical |
| P03-01 — Quyền đọc và fixture Linux nền tảng | Trang | 6 | Critical |
| P03-02 — memory_oom_events là tool thứ sáu tuần 1 | Trang | 6 | High |
| P04-01 — Gate tuần 1 với sáu read-only tools | Thủy | 4 | Critical |
| P04-12 / P04-13 / P04-14 — review và điều phối | Thủy / Hiền / Trang | 2 mỗi người | High |

Tải tuần: Thủy 36 giờ, Hiền 20 giờ, Trang 20 giờ.

## Tuần 2

| Task | Owner | Giờ | Ưu tiên |
|---|---|---:|---|
| P01-05 — listening_ports | Thủy | 6 | High |
| P01-06 — check_config | Thủy | 6 | High |
| P01-07 — Tích hợp registry đủ 11 read-only tools | Thủy | 6 | Critical |
| P02-02 — LLM adapter và tool selection | Hiền | 4 | High |
| P02-03 — Reasoning loop nhiều bước có giới hạn | Hiền | 10 | Critical |
| P02-04 — detect_anomaly | Hiền | 6 | High |
| P02-05 — verify_fix read-only | Hiền | 6 | High |
| P02-06 — CLI và báo cáo sự cố Markdown | Hiền | 4 | High |
| P03-03 — failed_logins | Trang | 6 | High |
| P03-07 — Lab S1–S5 và bộ đáp án chuẩn | Trang | 10 | High |
| P04-03 — Gate tuần 2 và chẩn đoán đa bước | Hiền | 4 | Critical |
| P04-12 / P04-13 / P04-14 — review và điều phối | Thủy / Hiền / Trang | 2 mỗi người | High |

Tải tuần: Thủy 20 giờ, Hiền 36 giờ, Trang 18 giờ.

## Tuần 3

| Task | Owner | Giờ | Ưu tiên |
|---|---|---:|---|
| P02-07 — UI đề xuất và xác nhận hành động | Hiền | 6 | Critical |
| P02-08 — Luồng restart rồi verify và incident report | Hiền | 6 | Critical |
| P03-04 — Chính sách approval và giao thức helper | Trang | 10 | Critical |
| P03-05 — Privileged helper và restart_service | Trang | 10 | Critical |
| P03-06 — Hardening systemd và audit | Trang | 6 | Critical |
| P04-02 — Bộ test contract và hồi quy MCP | Thủy | 6 | High |
| P04-04 — Security tests và gate đủ 12 tools | Thủy | 10 | Critical |
| P04-12 / P04-13 / P04-14 — review và điều phối | Thủy / Hiền / Trang | 2 mỗi người | High |

Tải tuần: Thủy 18 giờ, Hiền 14 giờ, Trang 28 giờ.

## Tuần 4

| Task | Owner | Giờ | Ưu tiên |
|---|---|---:|---|
| P04-05 — Khóa giao thức đánh giá và thu 15 lượt | Trang | 10 | Critical |
| P04-06 — Tính metric và phân tích lỗi | Hiền | 6 | High |
| P04-07 — Ổn định MCP và hướng dẫn chạy | Thủy | 6 | High |
| P04-08 — Ổn định Agent và báo cáo kỹ thuật tiếng Anh | Hiền | 6 | High |
| P04-09 — Phần MCP trong báo cáo và slide tiếng Anh | Thủy | 6 | High |
| P04-10 — Phần Security Evaluation và runbook demo | Trang | 6 | High |
| P04-11 — Diễn tập bảo vệ và nghiệm thu cuối | Trang | 4 | Critical |
| P04-12 / P04-13 / P04-14 — review và điều phối | Thủy / Hiền / Trang | 2 mỗi người | High |

Tải tuần: Thủy 14 giờ, Hiền 14 giờ, Trang 22 giờ.

## Trình tự trong tuần 1

Ngày đầu: Thủy chuẩn hóa repo; Hiền chủ trì kiến trúc với cả ba; Trang kiểm kê máy/VM. Chốt P00-02 và hợp đồng P00-03 trước khi mở nhánh triển khai. Nửa đầu tuần: server, client mock, quyền/fixtures. Nửa sau: sáu tools và smoke client thật. Mọi mốc ngày là đề xuất, chưa phải lịch hẹn.

## Workflow GitHub và Jira

TODO → IN PROGRESS → IN REVIEW → TESTING → DONE. BLOCKED dùng khi đang bị chặn, phải có lý do, task phụ thuộc, owner xử lý và điều kiện mở chặn; khi mở chặn quay về trạng thái phù hợp. Một task có đúng một owner. PR liên kết Task ID, reviewer khác tác giả, kiểm thử trước DONE. Với code, dùng branch riêng như `feat/P01-02-journal-search` khi phù hợp. PR tài liệu cần review và validation tài liệu thay cho test ứng dụng.

Đầu sprint xác nhận capacity và blockers, giữa tuần handoff/review, cuối tuần xem gate và cập nhật giờ còn lại. Không tự merge PR hoặc chuyển DONE nếu thiếu evidence. Không cập nhật Jira thực tế, tạo project hoặc GitHub Issues trong đợt lập kế hoạch này.

## Chuẩn bị nhập Jira

File JIRA_IMPORT.csv chứa 5 Epic và toàn bộ Task, giữ ID nội bộ, owner đề xuất, priority, SP, giờ, tuần, dependencies, deliverables, acceptance và test method trong description. Đây là dữ liệu staging. Trước import phải map tên Thủy/Hiền/Trang sang account ID thực tế, map Critical/High/Medium/Low vào priority của project, xác nhận field Story Points, và ánh xạ Epic theo phiên bản Jira (Epic Link/Parent). Không tự gán Assignee hoặc Status bằng chuỗi tên không xác thực.

Import Epic trước, lấy issue keys thực tế; sau đó map Proposed Epic ID của Task sang keys. Dependencies là ID nội bộ trong description/Depends On, chưa là Jira issue links: tạo liên kết blocks/is blocked by sau khi có mapping keys, bằng thao tác được người dùng cho phép. Subtasks hiện là checklist trong description; không tạo duplicate issues hoặc cộng lại SP. Tất cả trạng thái dự kiến TODO; workflow Jira phải có trạng thái tương ứng. CSV được kiểm tra cú pháp và số dòng cục bộ, chưa được kiểm chứng bằng import trên Jira thật.
