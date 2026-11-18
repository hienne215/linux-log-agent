# SCRUM-27 — CLI synthetic demo

Trạng thái: MVP **synthetic-only**, chưa nghiệm thu CLI tích hợp thật.
Branch `feat/SCRUM-27-cli-report`, stacked trên PR #8 commit
`61cdff0d2674f14150a1e7df5940a1372b8c1d24`. PR #8 phụ thuộc Draft PR #7
`9c7fbc14e5526305e69ae4ca835153872211de28`. Chưa có contracts SCRUM-12 finalized.
Không coi các dependency này đã merge vào main.

## Chạy từ repository trên Ubuntu

Cần Python 3.12 (đã kiểm chứng 3.12.16). Từ root checkout chứa code SCRUM-27:

```bash
python3.12 -m venv .venv-cli
.venv-cli/bin/python -m pip install .
.venv-cli/bin/python -m agent.cli --help
```

Cài vào venv, không global pip. Dependency CLI: jsonschema 4.26.0 và referencing
0.37.0; setuptools là build dependency. Không cần MCP SDK, Ollama hoặc Linux tools
để chạy CLI. Package có entrypoint `lla-demo` trỏ đến cùng hàm main. Lượt này đã
kiểm chứng module/source execution; chưa thực hiện wheel build/install mới.

Nếu đã có Python environment với dependencies chuẩn bị sẵn, không cần cài project:

```bash
PYTHONPATH=src python -B -m agent.cli --scenario active --question 'Nginx có đang hoạt động không?' --report nginx-active.md
PYTHONPATH=src python -B -m agent.cli --scenario port-conflict --question 'Vì sao Nginx không khởi động?' --report nginx-port.md
PYTHONPATH=src python -B -m agent.cli --scenario permission-denied --question 'Kiểm tra journal của Nginx' --report nginx-permission.md
```

Hoặc sau cài venv ở trên, thay `PYTHONPATH=src python -B` bằng
`.venv-cli/bin/python -B` trong ba lệnh. Không có đường dẫn cá nhân hoặc import từ
worktree khác. Synthetic fixtures được đóng trong package, không đọc tests/ hoặc
thư mục preparation để chạy demo.

Thiếu `--question`: đọc một dòng từ stdin, tối đa 4096 ký tự. Câu hỏi không được
in/lưu lại và không thay đổi scripted decisions; đây chưa là chẩn đoán ngôn ngữ tự
nhiên. Không có chế độ backend thật, server URL, shell, restart, approve hay `--yes`.
Nhận diện từ khóa write chỉ là phản hồi UI; boundary thực tế là allowlist/schema và
executor không có triển khai hệ thống.

## Kết quả đã chạy thực tế

Mọi output bắt đầu bằng **SYNTHETIC DEMO**, ghi rõ không gọi MCP/Ollama/Linux tools.

| Scenario | Exit | Số tool attempts | Evidence | Nhận định |
|---|---:|---:|---|---|
| active | 0 | 1 | active-01 | service active trong fixture, chưa kiểm tra HTTP |
| port-conflict | 0 | 3 | failed-01/02/03 | fixture failed + journal bind error + port 80 đã bị chiếm |
| permission-denied | 1 | 2 | failed-01 | không đủ quyền đọc journal; thiếu dữ liệu để kết luận nguyên nhân |

Ví dụ rút gọn từ terminal, port-conflict:

```text
# SYNTHETIC DEMO
Toàn bộ evidence bên dưới là dữ liệu giả lập. Không gọi MCP, Ollama hoặc Linux tools.
Trạng thái: Hoàn tất kịch bản giả lập; nhận định chưa xác minh thực tế.
- Bước 1: `service_status` — Đã đọc dữ liệu giả lập; refs: failed-01.
- Bước 2: `query_journal` — Đã đọc dữ liệu giả lập; refs: failed-02.
- Bước 3: `listening_ports` — Đã đọc dữ liệu giả lập; refs: failed-03.
- Status: concluded
- Tool attempts: 3
- LLM turns: 4
```

Ca permission-denied có dòng:

```text
- Bước 2: `query_journal` — Không đủ quyền đọc journal (PERMISSION_DENIED, giả lập); refs: không có evidence.
```

Output đầy đủ, xác định và có regression snapshot ở `tests/cli/snapshots/`.
`concluded` chỉ là terminal state của coordinator, không chứng minh Linux khỏe
hoặc nguyên nhân thật. Không action, approval/helper hay auto-remediation.

## Report, lỗi và cancellation

`--report NAME.md` chỉ nhận ASCII basename (chữ/số, `_`, `-`); không nhận absolute
path, slash, dot-dot, thư mục hoặc path từ model. Ghi vào `reports/` trong working
directory của người vận hành; không tự tạo report nếu không có flag này.

Directory phải thuộc UID hiện tại, không group/world writable, không symlink;
file 0600, directory tạo mới 0700. Ghi file riêng, fsync rồi publish bằng hard-link
exclusive: không ghi đè file/symlink đã tồn tại, không xuất file nửa chừng. Dùng
FD-based operations/O_NOFOLLOW, không check-then-open bằng resolve/prefix string.
Nếu filesystem không hỗ trợ hard link hoặc durability call thất bại, báo lỗi và
không tuyên bố đã lưu bền vững. Không có tùy chọn overwrite. Đổi basename cho lần
chạy tiếp; CLI không tự xóa report cũ. Generated reports bị Git ignore.

Exit codes: 0 hoàn tất fixture; 1 thiếu dữ liệu/error/timeout/budget; 2 input/policy
không hợp lệ; 3 lưu report thất bại; 130 hủy. Nếu hủy và lưu thất bại vẫn là 130.

Ctrl+C hủy coordinator, chờ cleanup qua owner hiện có, giữ report an toàn nếu có.
Synthetic executor hợp tác cancellation, không có server/subprocess; second Ctrl+C
có thể cắt việc lưu, CLI không cam kết có artifact bền vững. Không có hard wall
SLA; diagnosis cap 120s và cleanup budget là các giới hạn khác nhau.

Kiểm tra budget exhaustion có thể dùng:

```bash
PYTHONPATH=src python -B -m agent.cli --scenario active --question 'Kiểm tra Nginx' --timeout 0.000000000001
```

Hiển thị timeout/disconnect/không evidence đều dùng thông báo ứng dụng, không raw
exception. Renderer hiện có thực hiện redaction/escaping; trace chỉ nhận state,
tool và ID hữu hạn/allowlisted. Redactor demo che cả field nếu phát hiện dạng
secret đã biết, kể cả JSON/escaped JSON; cố ý bảo thủ và có thể false positive.
Không khẳng định phát hiện mọi secret/log thật. Không gửi raw prompt/exception tới
stdout, report hoặc logger. Nguồn giả lập không trở thành system instruction.

## Ranh giới so với kiến trúc triển khai thật

CLI và synthetic coordinator chạy cùng process chỉ trong demo này; không thực
hiện UID/service/IPC separation của deployment SCRUM-11. Production CLI phải có
binding đã review, giữ read plane/approval/control boundaries. SyntheticExecutor
chỉ tạo Observation nội bộ, không giả CallToolResult hoặc wire metadata SCRUM-12.
Không viết lại reasoning loop hoặc Incident Report renderer.

Sau SCRUM-12 cần Thủy chốt contracts/provenance/cancellation và mapping business
status; Trang review path/permissions/redaction/VM/control-plane boundaries. Hiền
sau đó gắn async Adapter và MCP qua boundary đã nghiệm thu, rồi chạy read-only
end-to-end trong phạm vi được phép. SCRUM-27 chưa Done.
