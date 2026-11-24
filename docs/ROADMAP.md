# Roadmap bốn tuần

Bản đề xuất ngày 01/11/2026, chờ nhóm phê duyệt. Tất cả task khởi tạo TODO; tài liệu này không phải bằng chứng tính năng đã triển khai. Tuần 1–4 là tuần tương đối từ ngày khởi động được nhóm xác nhận.

Nguồn: đề cương v2 ngày 01/11/2026 do người dùng cung cấp và yêu cầu phân công chính thức. Repository https://github.com/hienne215/linux-log-agent tại commit 1f594f132bb7b2b585766342cb7efc729b0086d6 chỉ có README.md (`# linux-log-agent`); chưa có ARCHITECTURE, ROADMAP, TASKS hay đề cương trong repo. Checkout mới sạch trước khi lập kế hoạch; không thể suy ra trạng thái những checkout khác trên máy. README được giữ nguyên.

## Năm Epic

- EPIC-00: Project Setup & Architecture.
- EPIC-01: MCP Server & Linux Tools.
- EPIC-02: AI Agent & Application.
- EPIC-03: Linux Security & Troubleshooting.
- EPIC-04: Testing, Evaluation & Documentation.

## Mốc nghiệm thu

| Tuần | Trọng tâm | Gate | Owner điều phối |
|---|---|---|---|
| 1 | Contracts, VM, server, client mock và 6 R | P04-01: 6 tool có tests, client gọi server thật | Thủy |
| 2 | Đủ 11 R, một LLM, loop và CLI | P04-03: S1/S2/S4 đa bước có evidence | Hiền |
| 3 | Approval, helper, 12 tools, security | P04-04: 5 ca smoke và mọi write chưa duyệt bị chặn | Thủy (Trang phụ trách security implementation) |
| 4 | 15 lượt baseline, số liệu, ổn định, báo cáo/slide/demo | P04-11: demo từ snapshot, dữ liệu và tài liệu thống nhất | Trang |

## Quyết định phạm vi

Bản kế hoạch cam kết 12 tools, 11 R và 1 W; sáu tool là gate tuần 1. Đề cương vừa gọi xử lý chủ động là mở rộng vừa yêu cầu 12 tools trong điều kiện kết thúc: kế hoạch theo yêu cầu chính thức của người dùng, đưa write có approval vào baseline tuần 3. Nếu bảo mật chưa đạt, vô hiệu hóa write và ghi rõ scope chưa hoàn thành, không công bố đạt đủ 12 tools hoạt động.

Chọn một LLM sau kiểm kê tài nguyên và smoke benchmark, khóa model/prompt/config khi đánh giá. Không fine-tune, dashboard, multi-host, giám sát dài hạn hoặc tự sửa mọi sự cố.

## Đánh giá

S1 crash loop; S2 nginx syntax; S3 port conflict; S4 backend down gây 502; S5 filesystem test gần đầy. Mỗi ca chạy 3 lần: 15 lượt bắt buộc. S6 OOM/S7 SSH failed logins chỉ thêm tối đa hai ca khi lab an toàn, có thời gian và nhóm duyệt; chạy đủ 7 ca là 21 lượt. Với 6 ca là 18 lượt. Fixture tests của OOM/login vẫn bắt buộc. LLM thứ hai nằm ngoài baseline.

Đo root-cause accuracy và evidence correctness theo tổng số lượt; median tool-call count và latency, ghi đơn vị và thời điểm bắt đầu/kết thúc. Safety là số hành động ngoài allowlist thực thi thành công, mục tiêu 0; unauthorized-write rejection có mẫu số riêng; verify success tính theo các lần sửa đã được duyệt có kết quả cần kiểm chứng, unknown tính không thành công và được ghi riêng. Accuracy ≥80% là mục tiêu nội bộ, chưa là kết quả. Mỗi run lưu model/prompt/commit/config/snapshot/ground truth/trace để tái lập.

## Quản lý thay đổi

Nếu gate chưa đạt, ghi blocker và owner, chỉnh lịch task phụ thuộc; chỉ chạy song song qua contract/mock đã ổn định. Không cắt security hoặc giảm số lượt đánh giá để làm đẹp tiến độ. Ngân sách sửa lỗi nằm trong P04-07/08/10; phát hiện lỗi lớn phải re-estimate. Nếu sửa sau baseline làm đổi hành vi, khóa phiên bản mới và chạy lại các lượt bị ảnh hưởng, công khai phần dữ liệu nào cùng version.

## Nhóm cần xác nhận

1. Ngày bắt đầu và số giờ khả dụng/người/tuần; capacity giả định 40 giờ có phù hợp lịch học không.
2. Phê duyệt 12 tools trong baseline và phân công cụ thể ưu tiên hơn bảng A/B/C tổng quát trong đề cương.
3. Model/API, chi phí, điều kiện dữ liệu, máy chủ VM và transport sau benchmark/ADR; chưa chọn mặc định model lớn.
4. Chọn unit restart/health endpoints/paths/executables allowlist và người có quyền phê duyệt tương tác; không cho Agent tự duyệt.
5. Mốc nộp báo cáo, yêu cầu giảng viên và có thêm S6/S7 hay không; mặc định chỉ S1–S5.
6. Hạn mức review 2 giờ/người/tuần và chấp nhận điều chỉnh lịch nếu review thực tế vượt ngân sách.
