# Definition of Done

Bản đề xuất ngày 01/11/2026, chờ nhóm phê duyệt. Tất cả task khởi tạo TODO; tài liệu này không phải bằng chứng tính năng đã triển khai. Tuần 1–4 là tuần tương đối từ ngày khởi động được nhóm xác nhận.

## Mỗi task

- Đủ deliverables, đáp ứng từng Acceptance Criterion trong TASKS.md; một owner, reviewer khác tác giả.
- PR/commit hoặc phiên bản tài liệu liên kết Task ID; không ghi đè thay đổi chưa commit; review trước merge.
- Code có unit/contract tests thích hợp, edge cases và negative cases; tests pass và lưu evidence. Integration task có trace VM. Task tài liệu có review nội dung, liên kết, số liệu và walkthrough/checklist được ghi lại.
- Không secret trong repo/report/prompt/fixtures; IP nguồn pseudonym ổn định khi gửi ra LLM; mapping gốc giữ cục bộ.
- Bàn giao interface và hướng dẫn tái lập; không còn blocker chưa ghi nhận. Test fail thì không DONE.

## MCP và Agent

Từng tool có schema, validation, timeout, output limit, error_code, command_source và evidence. Timestamp/unit có giá trị khi áp dụng; tool như disk_usage không có unit systemd thì dùng null kèm semantics rõ, không tạo dữ liệu giả. Linux commands allowlist, subprocess không dùng shell=True, đường dẫn canonical được kiểm tra, regex có giới hạn tài nguyên. Agent có budget bước/thời gian/token; log luôn là dữ liệu không tin cậy. Không tool ngoài manifest được gọi. Báo cáo sự cố có evidence hoặc uncertainty.

## Write và quyền

MCP reader và Agent không sudo toàn cục. Reader có NoNewPrivileges=yes phù hợp; helper tách tiến trình, quyền hẹp, xác thực caller và audit. Approval phải do người tương tác duyệt và được backend đáng tin xác thực, gắn action ID/params/expiry, chống replay, một lần dùng. Kiểm tra lại allowlist tại thực thi. Không token do LLM tự sinh; không đường bỏ qua xác nhận. Có tests unauthenticated, expired, altered params, replay, outside allowlist và unavailable helper. Restart không được mô tả là rollback toàn trạng thái. Verify_fix chỉ đọc và không tự thử ghi lại.

## An toàn lab

Mọi sự cố crash/OOM/full-disk/SSH/firewall chỉ ở VM/môi trường cách ly phù hợp. Snapshot có ID, restore đã thử; phân vùng thử nghiệm và tài nguyên có giới hạn. Ubuntu host chỉ code/đọc/điều khiển VM. Không thực thi các runbook tạo sự cố trong giai đoạn lập kế hoạch này.

## Gate cuối dự án

Đủ 12 tools (11 R + 1 W có approval) và CLI đa bước với một LLM; gate W1/W2/W3 có bằng chứng. S1–S5 mỗi ca ba lượt, 15 lượt bắt buộc, có ground truth và cấu hình khóa. Báo cáo metric ghi mẫu số, raw data, latency và số tool calls; safety ngoài allowlist bằng 0; 100% thử ghi chưa duyệt bị chặn. Accuracy ≥80% là mục tiêu đánh giá, nếu chưa đạt phải nêu kết quả và phân tích, không đổi ground truth để đạt chỉ tiêu. Báo cáo và slide tiếng Anh, README tái lập, demo từ snapshot sạch, cả ba tham gia bảo vệ.

Nếu write chưa an toàn, vô hiệu hóa và báo scope chưa hoàn thành; không coi đây là đủ điều kiện DONE của P03-05/P04-04 hoặc đủ 12 tools hoạt động. Nhóm có thể quyết định nộp read-only với giới hạn được công khai.

## Done cho đợt lập kế hoạch hiện tại

Sáu tài liệu nhất quán về IDs, owner, SP, giờ, dependencies và mốc; có bảng toàn bộ task và tổng công sức; CSV staging đủ nội dung. Không triển khai Phase 1, không đổi code ứng dụng, không tạo Jira/GitHub Issues, không push/commit tự động. Dừng chờ người dùng phê duyệt kế hoạch.
