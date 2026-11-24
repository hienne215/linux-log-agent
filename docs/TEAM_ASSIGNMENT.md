# Phân công nhóm

Bản đề xuất ngày 01/11/2026, chờ nhóm phê duyệt. Tất cả task khởi tạo TODO; tài liệu này không phải bằng chứng tính năng đã triển khai. Tuần 1–4 là tuần tương đối từ ngày khởi động được nhóm xác nhận.

Nguồn: đề cương v2 ngày 01/11/2026 do người dùng cung cấp và yêu cầu phân công chính thức. Repository https://github.com/hienne215/linux-log-agent tại commit 1f594f132bb7b2b585766342cb7efc729b0086d6 chỉ có README.md (`# linux-log-agent`); chưa có ARCHITECTURE, ROADMAP, TASKS hay đề cương trong repo. Checkout mới sạch trước khi lập kế hoạch; không thể suy ra trạng thái những checkout khác trên máy. README được giữ nguyên.

## Phạm vi và quyền sở hữu

Thủy giữ trách nhiệm nền tảng MCP, schema, registry và MCP testing. Hiền giữ Agent/Application, detect_anomaly và verify_fix. Trang giữ Linux/Security/DevOps, memory_oom_events, failed_logins và restart_service. Yêu cầu phân công cụ thể của người dùng được ưu tiên hơn bảng tổng quát A làm 11 read tools trong đề cương; Thủy tích hợp chứ không làm thay bốn read tools do Hiền/Trang sở hữu.

| Thành viên | Tổng task | Tổng SP | Tổng giờ | Nhiệm vụ quan trọng | Rủi ro quá tải |
|---|---:|---:|---:|---|---|
| Thủy | 15 | 50 | 88 | MCP contracts/server, 7 read tools, registry, security test độc lập, phần MCP/slide | Đỉnh 36 giờ/tuần; xem capacity bên dưới |
| Hiền | 14 | 43 | 84 | LLM/client/loop/CLI, detect_anomaly, verify_fix, tích hợp, báo cáo tổng | Đỉnh 36 giờ/tuần; xem capacity bên dưới |
| Trang | 12 | 43 | 88 | VM/quyền, OOM/login, approval/helper/restart, lab/evaluation/demo | Đỉnh 28 giờ/tuần; xem capacity bên dưới |

## Giờ theo tuần và capacity

| Thành viên | W1 | W2 | W3 | W4 |
|---|---:|---:|---:|---:|
| Thủy | 36 | 20 | 18 | 14 |
| Hiền | 20 | 36 | 14 | 14 |
| Trang | 20 | 18 | 28 | 22 |

Giả định lập kế hoạch: mỗi thành viên có 40 giờ/tuần, dành tối đa 36 giờ cho baseline và 4 giờ dự phòng. Đây là giả định cần xác nhận, chưa phải cam kết lịch học. Nếu chỉ có 20 giờ/tuần, phải kéo dài lịch hoặc duyệt giảm phạm vi; không yêu cầu một người tự bù phần thiếu. Ước lượng còn bất định khoảng ±25% do repo chưa có triển khai. Không đánh đồng SP với năng suất cá nhân.

Tổng giờ tương đối cân bằng nhưng Thủy đạt trần baseline 36 giờ tuần 1, Hiền đạt 36 giờ tuần 2, Trang nặng nhất tuần 3. Ước lượng có điều kiện: official SDK, contracts gọn dùng một envelope chung, CLI tối thiểu, không xây framework tùy biến. Chỉ thêm 25% bất định đã khiến các tuần đỉnh vượt capacity; không coi buffer 4 giờ là đủ bảo đảm. Đề xuất: Trang chuẩn bị thêm fixture/parsing cases cho Thủy tuần 1; Thủy hỗ trợ fixtures/schema cho Hiền tuần 2; Hiền hỗ trợ harness integration cho Trang tuần 3 trong ngân sách review, giữ nguyên owner chính. Nếu vượt 8 giờ review/người hoặc thực tế quá tải, đưa ra nhóm duyệt điều chỉnh capacity/lịch, không ngầm chuyển trách nhiệm.

## Ma trận 12 tools

| Tool | Loại | Owner triển khai | Reviewer | Tuần | Task |
|---|---|---|---|---|---|
| query_journal | R | Thủy | Trang | 1 | P01-02 |
| search_log | R | Thủy | Trang | 1 | P01-02 |
| service_status | R | Thủy | Trang | 1 | P01-03 |
| list_failed_units | R | Thủy | Trang | 1 | P01-03 |
| disk_usage | R | Thủy | Trang | 1 | P01-04 |
| memory_oom_events | R | Trang | Thủy | 1 | P03-02 |
| failed_logins | R | Trang | Thủy | 2 | P03-03 |
| listening_ports | R | Thủy | Trang | 2 | P01-05 |
| check_config | R | Thủy | Trang | 2 | P01-06 |
| detect_anomaly | R | Hiền | Thủy | 2 | P02-04 |
| verify_fix | R | Hiền | Trang | 2 | P02-05 |
| restart_service | W | Trang | Thủy | 3 | P03-05 |

## Điều phối chia sẻ

Thủy điều phối schema và review gate W1; Hiền điều phối tích hợp Agent và gate W2; Trang điều phối lab, đánh giá và rehearsal. P04-12/13/14 dành 2 giờ/người/tuần cho review và điều phối. Cả ba review kiến trúc, integration, báo cáo/slide tiếng Anh và demo; mỗi đầu ra vẫn có owner duy nhất. Hiền không mặc định là quản lý mọi việc.

## Việc đầu tiên

- Thủy: P00-01 kiểm kê repo/quy tắc PR, sau đó P00-03 ngay khi kiến trúc được chốt.
- Hiền: P00-02 kiến trúc và phiên thống nhất interface ngày đầu với cả nhóm.
- Trang: P00-04 kiểm kê tài nguyên/VM/snapshot, rồi P03-01 quyền và fixtures.
