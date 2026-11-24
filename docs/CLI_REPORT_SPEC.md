# SCRUM-27 — CLI & Incident Report Specification

**Bản nháp 0.1 · 02/11/2026 · Hiền (Member B).** Phạm vi: thiết kế giao diện, chưa code/test runtime. Cơ sở: SCRUM-11, commit `d42cc9a`, [ARCHITECTURE §3,5–7](https://github.com/hienne215/linux-log-agent/blob/d42cc9a/docs/ARCHITECTURE.md) và [SECURITY §5–7](https://github.com/hienne215/linux-log-agent/blob/d42cc9a/docs/SECURITY.md). Đây là mô hình hiển thị nội bộ đề xuất, **không phải MCP contract**; SCRUM-12 chưa chốt. Không gọi MCP/Ollama trong tài liệu này.

## 1. Commands và input/output đề xuất

Tên executable tạm: `lla`. Câu hỏi là dữ liệu, không được thực thi như shell. CLI tin cậy tách tiến trình/UID với Agent theo SCRUM-11.

| Command | Input | Output và giới hạn |
|---|---|---|
| `lla diagnose "<câu hỏi>" --target <profile> [--unit <unit>] [--since <UTC> --until <UTC>]` | Câu hỏi bắt buộc; profile môi trường đã cấu hình; unit canonical nếu cần; thời gian RFC3339 UTC, since < until | Tạo session/trace; tiến độ, evidence, kết luận và report Markdown. Mặc định chỉ đọc. Thiếu window thì hiện window hiệu lực từ policy trước thu thập; không tự đoán alias unit. |
| `lla report <incident-id>` | ID báo cáo thuộc người dùng hiện tại | Hiển thị bản đã lưu; không gọi model/tool, không tái chẩn đoán. |
| `lla --help` / `lla --version` | Không cần backend | Hướng dẫn / phiên bản CLI và report schema. |

Không nhận URL MCP, executable, shell command, secret hoặc đường dẫn report từ LLM/log. Target là ID tin cậy, không phải host/URL tùy ý. stdout: nội dung kết quả đã lọc; stderr: tiến độ/cảnh báo/lỗi đã lọc. Không cần JSON output trong MVP. Non-interactive vẫn chỉ đọc, không chờ approval.

CLI sinh basename `<incident-id>.md` trong thư mục report cấu hình của người vận hành; Agent không chọn đường dẫn. Không ghi đè file có sẵn, không theo symlink thoát thư mục. Lưu nguyên tử; chỉ báo “đã lưu” sau thành công. Ctrl+C dừng yêu cầu đọc mới, yêu cầu hủy luồng hiện tại và lưu phần đã có với nhãn chưa hoàn tất; không hứa backend đã hủy thao tác đang chạy.

Exit code đề xuất: `0` chẩn đoán hoàn tất và lưu được report (không có nghĩa hệ thống khỏe); `1` phiên lỗi/thiếu dữ liệu/hết budget; `2` input sai; `3` lưu report lỗi; `130` người dùng hủy. Nếu hủy và lưu lỗi, giữ `130`, ghi rõ không có artifact bền vững.

## 2. Trạng thái và lỗi hiển thị

| Trạng thái UI | Hiển thị |
|---|---|
| RECEIVED → COLLECTING ↔ ANALYZING | “Đã nhận yêu cầu” → “Đang thu thập bằng chứng” / “Đang phân tích”; có trace và số bước trong budget. |
| CONCLUDED → REPORTED | Kết luận kèm citations → vị trí report đã lưu. Kết luận về nguyên nhân có thể vẫn chưa xác minh. |
| PARTIAL / BUDGET_EXHAUSTED | Dữ liệu thiếu/bị cắt hoặc hết ngân sách; không diễn giải thành “không có lỗi”. |
| CANCELLED / ERROR | Nêu bước dừng, phần đã thu thập, tình trạng lưu report; không tự tiếp tục hoặc nâng quyền. |

Trạng thái do ứng dụng quản lý; không lấy lời model làm tín hiệu hoàn tất. Nhãn `PARTIAL` tổng hợp chất lượng dữ liệu, không áp đặt enum server. Adapter ánh xạ theo contracts sau SCRUM-12.

| Tình huống | Thông báo mẫu và hành vi |
|---|---|
| Input sai | “Khoảng thời gian không hợp lệ; cần since < until.” Chưa tạo lời gọi backend. |
| Backend/contract chưa sẵn sàng | “Không thể bắt đầu: backend hoặc phiên bản contract chưa tương thích.” Dừng, không fallback sang lệnh Linux. |
| Thiếu quyền | “Không đọc được nguồn đã yêu cầu; kết luận có thể thiếu bằng chứng.” Giữ lỗi trong trace; không sudo. |
| Tool timeout / dữ liệu bị cắt | “Bước đọc chưa hoàn tất / kết quả đã bị cắt.” Ghi nguồn bị ảnh hưởng; retry đọc chỉ theo policy Agent đã duyệt. |
| LLM lỗi | “Không thể tạo kết luận; đã giữ bằng chứng thu thập được.” Không chuyển sang thao tác ghi. |
| Lưu lỗi | “Không lưu được báo cáo; dữ liệu chưa được lưu bền vững.” Không báo thành công; có thể xem phần đã lọc trên terminal. |

## 3. Incident Report — cấu trúc hiển thị

Report schema đề xuất `1.0`; tên trường dưới đây thuộc **report model nội bộ**, không yêu cầu server trả đúng các khóa này. Thiếu dữ liệu ghi “chưa có/không biết”, không tạo ID, timestamp hay số đo giả.

- **Metadata:** incident/session/trace ID, target/environment, window UTC, thời điểm bắt đầu/kết thúc, phiên bản app/report/model/prompt/policy.
- **Yêu cầu và facts:** scope, triệu chứng, evidence ID + nguồn + observed_at + excerpt đã lọc; cờ redacted/truncated. Mỗi fact/kết luận phải trỏ evidence có thật trong session hoặc ghi “chưa xác minh”.
- **Nhận định:** nguyên nhân đề xuất, giả thuyết khác, mức chắc chắn kèm lý do ngắn; thiếu quyền/dữ liệu. Không lưu chain-of-thought hoặc phần trăm tin cậy tự bịa.
- **Trace và action:** tên bước/tool thực nhận, args chuẩn hóa đã lọc, duration, status/error, evidence IDs; tổng số calls và elapsed. Tách đề xuất, approval, execution outcome và verification.
- **Giới hạn và bước tiếp:** việc còn thiếu, phép đọc đề nghị, tình trạng lưu; không biến khuyến nghị thành lệnh tự chạy.

Redact secrets trước hiển thị/lưu; loại ANSI/control sequences, escape HTML/Markdown từ dữ liệu không tin cậy. Không nhúng ảnh/link ngoài từ log, raw log chưa lọc, grant/token/key hoặc IP mapping. Report không thay thế audit authoritative của coordinator/helper.

### Mẫu Markdown — toàn bộ là mock, chưa gọi tool

```markdown
# Incident MOCK-001 — dữ liệu minh họa
Session/trace: mock-session-01 / mock-trace-01; target: lab-vm-demo
Window UTC: [2026-11-02T00:00:00Z, 2026-11-02T00:10:00Z)
Run UTC: 00:10:01Z–00:10:02Z ngày 2026-11-02 (giả lập)
Versions: app/prompt/policy=chưa chốt; report=1.0; model=mock, không gọi LLM
Yêu cầu: Tìm lý do dịch vụ lab không hoạt động. Trạng thái: PARTIAL.

## Facts và evidence
- [E1] Nguồn mock/status, observed_at=2026-11-02T00:10:01Z:
  “Dịch vụ không hoạt động.” redacted=false; truncated=false.
- Nguồn nhật ký chưa đọc được: lỗi quyền giả lập; không có excerpt.

## Nhận định
Chưa xác định nguyên nhân. E1 chỉ chứng minh trạng thái tại thời điểm quan sát.
Giả thuyết lỗi cấu hình chưa được kiểm chứng; cần bằng chứng bổ sung.

## Trace
| Bước minh họa (không phải tên MCP) | Input đã lọc | Kết quả | Duration | Evidence |
|---|---|---|---|---|
| đọc trạng thái | target=lab-vm-demo | ok | 10 ms, mock | E1 |
| đọc nhật ký | window như trên | permission denied, mock | 5 ms, mock | không có |
Tổng: 2 bước giả lập, 0 lời gọi MCP thật; elapsed=1 s giả lập.

## Hành động và verification
Đề xuất: bổ sung bằng chứng đọc; chưa có proposal restart.
Action ID/approver/timestamps: không có; approval: không yêu cầu.
Execution: không thực hiện; verification: not_run.
Giới hạn: thiếu nhật ký; không kết luận “đã sửa”.
```

## 4. Ranh giới approval và các điểm chờ thống nhất

SCRUM-27 chưa cung cấp lệnh restart/approve, `--yes` hay auto-approve. Khi triển khai luồng ghi ở task sau, CLI phải lấy bản ghi trực tiếp từ coordinator: action ID, unit chính xác, VM, ảnh hưởng, expiry. TTY riêng nhận xác nhận gắn action ID; input từ pipe/model/log không có quyền duyệt. Mặc định từ chối; hết hạn/hủy không dispatch. Unknown sau dispatch không tự retry.

Report giữ độc lập approval `pending/denied/expired/approved`, execution `not_run/executing/succeeded/failed/execution_unknown`, verification `not_run/pass/fail/unknown`; đây là mapping UI dự kiến theo SCRUM-11. Restart succeeded không đồng nghĩa verification pass.

| Cần chốt | Người phối hợp |
|---|---|
| Version/schema, tên tool và args thật; lỗi protocol vs tool, empty/partial/truncated, cancellation | Thủy + Hiền sau SCRUM-12 |
| Evidence IDs/provenance, thời gian/cursor, redaction flags và mapping sang report | Thủy + Hiền |
| Target/session/UID binding, control socket, TTL và action states authoritative | Trang + Hiền; thuộc luồng approval sau |
| Thư mục/quyền file, retention, pseudonym/redaction và tiêu chí verification | Trang + Hiền; Thủy phối hợp dữ liệu |
| Tên `lla`, window mặc định, exit codes và snapshot UI với mock chuẩn | Cả nhóm trước viết code |

Đầu ra lần này chỉ là đặc tả. CLI tests, snapshot/redaction tests và tích hợp thuộc bước triển khai sau; không tuyên bố SCRUM-27 hoàn tất.
