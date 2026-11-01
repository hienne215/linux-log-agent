# Security Design — Linux Log AI Agent

Phiên bản 1.0 — 01/11/2026. Phạm vi SCRUM-11 / P00-02. **Thiết kế để review, chưa triển khai hoặc chứng minh hardening bằng runtime.** Đọc cùng [ARCHITECTURE.md](ARCHITECTURE.md); không có cấu hình hoặc hướng dẫn chạy thao tác thay đổi hệ thống trong tài liệu này.

## 1. Tài sản, giả định và threat model

Tài sản cần bảo vệ: trạng thái Linux/units; log và secrets; thông tin xác thực LLM; khóa approval; danh tính người duyệt; evidence/audit/report; tài nguyên VM và laptop host.

Đối thủ trong phạm vi: log có prompt injection hoặc control characters; LLM chọn sai tool, bịa approval hoặc params; input/path/regex độc hại; read-plane process bị chiếm quyền với UID ít quyền; replay/tamper grant; nhầm session/VM/unit; timeout, crash và output quá lớn. Mục tiêu: không biến các đầu vào này thành write chưa được người dùng duyệt, giới hạn dữ liệu đọc và egress, không báo sửa thành công khi chưa kiểm chứng.

Trusted computing base (TCB): OS/kernel/systemd, code và config do administrator triển khai, CLI xác nhận dưới UID người vận hành, coordinator, helper và cơ chế quản lý khóa/policy. Root/admin, UID người vận hành hoặc coordinator/helper bị chiếm quyền nằm ngoài bảo đảm “LLM không tự duyệt”; phải nêu giới hạn này. Một chương trình độc hại chạy cùng UID người vận hành có thể giả lập CLI: `SO_PEERCRED` chứng minh UID, **không chứng minh một con người đang hiện diện**. Vì vậy không chạy Agent dưới UID đó, không cung cấp API approve qua read plane, và bảo vệ đường nhập TTY trong CLI tin cậy. TTY check một mình không phải cơ chế xác thực.

Agent và MCP stdio cùng UID `mcp-log-reader` theo ADR-03. Không tuyên bố cách ly hai tiến trình khỏi nhau; exploit Agent có thể đọc mọi dữ liệu UID này được OS cho phép. Rule tool allowlist bảo vệ đường gọi thông thường từ model; DAC/sandbox và UID tách biệt bảo vệ approval/helper khi read plane bị xâm phạm. Không có bảo đảm chống kernel exploit hoặc root helper bị khai thác.

## 2. Trust boundaries và nguyên tắc bắt buộc

| Boundary | Dữ liệu đi qua | Kiểm soát tại bên nhận |
|---|---|---|
| TB-01: người dùng → CLI/Agent | Câu hỏi, scope | Session, VM cố định, schema; yêu cầu người dùng không sửa policy |
| TB-02: LLM/log → ứng dụng | Tool choice, text, evidence | Untrusted data; structured validation, budget, citation checks; không diễn dịch thành command/approval |
| TB-03: Agent → MCP/tools | Tool name/params | Registry cố định, validate lại, fixed argv, timeout/output caps; boundary logic, cùng UID |
| TB-04: Linux → evidence → LLM/API/report | Log, metadata, IP/secrets | Redact trước context/egress, provenance, strip terminal escapes, output bounds |
| TB-05: read plane → coordinator | Proposal/status | Socket permissions + peer UID; chỉ tạo pending/read own session; không approve |
| TB-06: human CLI → coordinator | Quyết định gắn action | Control socket riêng, peer UID allowlist, session binding, authoritative params, interactive-only CLI |
| TB-07: coordinator → helper | Internal grant | Peer UID + MAC + version/VM/params/policy/TTL/epoch + atomic consume; recheck allowlist |
| TB-08: helper → systemd | Restart một unit | Fixed method/mode, canonical exact unit, completion tracking; không command string |
| TB-09: VM → host/LLM endpoint | Egress và thao tác lab | Không điều khiển host; chỉ endpoint LLM đã cấu hình, không gửi raw secrets |

Các invariants để review/code test:

1. Không tool arbitrary shell/exec; không `shell=True`, `eval`, template lệnh hoặc executable do model chọn.
2. Agent, MCP và diagnostics không sudo, setuid helper, file capabilities hoặc policy restart trực tiếp. Không cấp quyền systemd management cho read-plane UID qua group/Polkit khác.
3. Log/tool outputs và metadata không thể thay đổi system policy, registry, allowlist, approver hay trusted UI prompt.
4. `restart_service` trong MCP chỉ là facade; thao tác ghi duy nhất của baseline là helper restart exact unit lab đã duyệt.
5. Agent/MCP không đọc/ghi được control/helper socket, khóa grant, approval state, helper ledger, audit authoritative hoặc policy/config triển khai.
6. Mọi lỗi xác thực, audit bắt buộc, policy, expiry hoặc trạng thái không chắc chắn đều fail closed với write; không fallback sudo hoặc tự duyệt.
7. Một grant cho tối đa một lần thử thực thi; restart không tự retry và không được gọi là rollback.
8. Không gửi dữ liệu chưa lọc ra LLM/API; kết luận và verify cần evidence có nguồn, thời gian và giới hạn.

## 3. Ma trận quyền và IPC

| Identity dự kiến | Được phép | Không được phép |
|---|---|---|
| UID người vận hành | CLI trusted, diagnose socket, coordinator control; ghi report của mình | CLI không có API tự ký grant hoặc gọi helper. Không chạy model/tool dưới UID này. |
| `mcp-log-reader` | Agent + child MCP; đọc journal/log lab theo quyền tối thiểu; proposal/status socket; LLM endpoint nếu được phép | sudo/root, control/helper socket, policy/key/audit write, arbitrary unit management, home của người dùng |
| `lla-approval` | Proposal/control handlers, state/audit riêng, key grant, helper socket | Không trực tiếp gọi systemd restart; không quyền sửa mã helper/allowlist root-owned |
| Helper service root (đề xuất) | Verify grant, ledger/audit, fixed systemd RestartUnit cho allowlist | Không nhận shell, path/executable/method tùy ý; không endpoint cho Agent; không chỉnh config Linux theo LLM |
| Người quản trị triển khai | Cài code/config, khóa, allowlist, quyền socket trong VM ở task sau | Không ngầm cấp các quyền này cho Agent bằng sudoers rộng |

Pathname Unix sockets trong runtime directory được administrator quản lý; không dùng thư mục writable bởi read-plane để đặt control/helper socket. Tách socket/group cấp quyền đọc/ghi theo bảng, không một group chung cho cả ba. Server và client kiểm peer UID đúng cấu hình; PID chỉ hỗ trợ audit, không làm danh tính bền vững. Linux cung cấp peer credentials qua `SO_PEERCRED`; xem [unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html). Socket mode một mình không thay thế validation message/session, và không dùng trường `uid` từ JSON để xác thực.

Mã/config deployment không được đọc từ working directory writable bởi Agent; dùng entrypoint cố định và environment tối thiểu, không plugin/import path do model cung cấp. Tên tài khoản/path cụ thể, ownership/mode và LLM credential injection phải được Trang xác nhận bằng permission matrix VM; bảng này chưa phải tài khoản đã tạo.

## 4. NoNewPrivileges và lý do không gọi sudo trong MCP

`NoNewPrivileges=yes` ngăn tiến trình và các con nhận thêm quyền qua exec, gồm setuid/setgid và file capabilities. Vì vậy thiết kế MCP ít quyền rồi gọi sudo để nâng quyền là xung đột. Không khắc phục bằng tắt hardening trên reader, thêm sudoers hoặc chạy toàn Agent dưới root. Xem [systemd.exec — NoNewPrivileges](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html).

Giải pháp đã chọn: systemd khởi động read-plane service dưới UID ít quyền; Agent sinh MCP con cùng UID và sandbox. Coordinator/helper là service độc lập, **không phải con được Agent nâng quyền**. IPC gửi yêu cầu tới helper đã có quyền phù hợp, không nâng quyền của sender. Helper được systemd cấp identity từ lúc khởi động; NNP không tự thu hồi quyền root đã có. Hardening helper cần profile riêng và kiểm khả năng gọi systemd, không sao chép sandbox reader một cách máy móc.

Mục tiêu cho read plane: NNP, `ProtectSystem=strict`, `ProtectHome=yes`, giới hạn filesystem/process/network và vùng state riêng khi cần. Với stdio, profile phải áp ở service cha Agent để child MCP thừa hưởng; không giả vờ có MCP service khác UID. ReadWritePaths nếu có chỉ cho runtime/state tối thiểu, không chứa unit files, executable, log nguồn hoặc policy. Truy cập journal vẫn có thể lộ dữ liệu nhạy cảm dù filesystem read-only; group journal không phải bộ lọc theo unit. Nếu quyền quá rộng, dùng log lab đã tách/ACL phù hợp hoặc trả thiếu quyền, không cấp root để “đọc cho đủ”.

Các directive sandbox là mục tiêu thiết kế; chưa tạo unit, chưa kiểm phiên bản systemd/Ubuntu, chưa chạy security analysis hay probe quyền. Chỉ sau P03-01/P03-06 và security gate mới được tuyên bố hardening hoạt động.

## 5. An toàn công cụ và dữ liệu

### 5.1 Input, process và tài nguyên

- Tool names/arguments tuân schema server-owned. Exact allowlist units, log IDs, config profiles và health profiles; reject null bytes, unit bắt đầu bằng option, alias/template ngoài policy, path traversal và extra fields.
- Collector sử dụng fixed absolute executable + argv list, không shell expansion. Tham số không được trở thành option/executable. Giới hạn thời gian, kích thước pipe và số dòng trong lúc đọc, không đợi hết output rồi cắt; timeout phải dừng cây tiến trình đúng phạm vi.
- Log file được mở dưới allowed root; kiểm symlink và file descriptor để tránh TOCTOU, rotation có nguồn/file identity rõ; reject escapes thay vì chỉ kiểm prefix chuỗi. Không nhận glob/path raw từ LLM.
- Literal search mặc định. Regex nếu hỗ trợ phải có engine/worker có deadline và giới hạn input/pattern; không coi giới hạn độ dài pattern là đủ chống catastrophic backtracking. Chưa có sandbox/timeout regex thì trả unsupported cho regex phức tạp.
- `check_config` chỉ profile lab đã review: không reload/start, không ghi config; config có include/module tùy ý có thể làm validator đọc hoặc thực thi ngoài dự kiến. Không cho LLM chọn config/executable/modules. Nếu không chứng minh validator là read-only dưới quyền thực thì disable profile.
- Health checks chỉ lab loopback/service endpoint theo profile cố định, timeout, không redirect ra ngoài, không arbitrary URL, credential hoặc SSRF qua log. Method không gây mutation; verify không tự sửa.
- Output permission denied/partial/truncated được giữ nguyên; không giả dữ liệu process/nhật ký mà UID không đọc được. Rate-limit proposals, concurrent reads và pending sessions.

### 5.2 Prompt injection, redaction và secrets

Log, journal message, tool output và field reason của LLM chỉ là dữ liệu. Chuỗi “ignore previous instructions”, “approved=true”, câu lệnh hoặc action ID trong log không được đưa vào control channel. Prompt phân tách dữ liệu giúp model nhưng không phải security boundary; enforcement nằm ở schema, UID, backend policy và helper.

Redact tại collector/server trước khi evidence vào Agent context; adapter lọc lần nữa trước provider. Xóa token/password/authorization headers/private keys theo rule và fixtures; không log environment hoặc request đầy đủ chứa credential. IP dùng pseudonym ổn định trong scope đánh giá để tương quan; mapping/key cục bộ không tới provider/report công khai. Redaction không bảo đảm phát hiện mọi secret: API chỉ được dùng với dữ liệu lab đã rà soát và chính sách dữ liệu được nhóm chấp thuận; nếu không thì không gửi, dùng dữ liệu đã xử lý/local model phù hợp.

Provider/model/base URL do config tin cậy chỉ định, không từ output model. Token chỉ cấp cho Agent cần gọi API; MCP child có environment allowlist, không kế thừa token vô điều kiện. Tuy nhiên Agent/MCP cùng UID không tạo cách ly OS cho credential LLM: nếu một process bị chiếm quyền, không cam kết giấu token của process kia. Muốn bảo đảm đó phải review lại ADR transport và tách UID. Không có quyền đọc khóa approval trong read plane. Terminal/report escape ANSI/control sequences và nội dung Markdown/HTML nguy hiểm; trusted confirmation label không chứa raw model text. Không lưu reasoning nội bộ của model như audit; lưu quyết định ngắn và evidence kiểm chứng được.

## 6. Approval protocol và restart_service

### 6.1 Đường đề xuất và đường duyệt

1. MCP facade nhận proposal hợp schema, gửi coordinator qua proposal socket. Caller chỉ có quyền đề xuất; coordinator kiểm policy, canonical unit, session/VM và quota, tạo pending action ID ngẫu nhiên.
2. CLI UID người vận hành lấy pending record trực tiếp từ control socket; hiển thị action, exact unit, VM, tác động gián đoạn và expiry do backend xác định. Model chỉ cung cấp reason có nhãn untrusted, không định nghĩa nội dung duyệt.
3. Người dùng xác nhận gắn action ID qua đường nhập TTY riêng của CLI. Không dùng stdin pipe, log, API model, biến môi trường `AUTO_APPROVE` hoặc cờ `--yes` cho write. Phiên non-interactive chỉ read-only. Cancel/no response/expiry là deny.
4. Coordinator kiểm peer UID allowlist và chủ session; chỉ transition `pending → approved` một lần, tái kiểm policy/TTL rồi audit. Không chấp nhận params mới ở approve request; thay đổi unit/params phải tạo proposal mới và duyệt lại.
5. Coordinator tạo grant nội bộ theo ARCHITECTURE §6, gửi helper qua socket riêng; grant không được trả về Agent hoặc CLI. Helper kiểm lại mọi binding cùng exact policy riêng, không coi coordinator nói “đã duyệt” là đủ.

Action state đề xuất: `pending → denied/expired` hoặc `pending → approved → executing → succeeded/failed/execution_unknown`. Verification là trường riêng `not_run/pass/fail/unknown`, không đổi một restart thất bại thành succeeded. Timestamp/state transitions được backend giữ, text LLM không ghi đè được.

### 6.2 Grant, replay và crash

Grant versioned bao gồm action ID, session/trace, target VM/environment, helper epoch, operation, canonical params + digest, policy version, approver UID, issued_at/expires_at. HMAC-SHA-256 bao toàn bộ payload canonical, không chỉ action ID/unit. Reject unknown version/fields, duplicate JSON keys, inconsistent digest, invalid MAC; so sánh MAC constant time. Coordinator và helper dùng cùng quy tắc serialization được khóa ở P03-04. Khóa grant nằm ngoài read plane, root quản lý vòng đời; coordinator là bên được tin cậy giữ khóa này. Nếu coordinator bị chiếm quyền thì có thể mint grant: đó là giới hạn TCB cần review, không được che giấu bằng chữ “signed”.

Helper xác thực coordinator UID trước parse/execute. Với mỗi grant hợp lệ, helper khóa theo action và unit, tái kiểm allowlist/policy/expiry sát lúc thực thi, ghi bền trạng thái consumed/executing **trước** gọi systemd. Ledger do helper quản lý, không writable bởi coordinator/Agent. Đồng thời ghi audit ý định; không ghi được state/audit bắt buộc thì từ chối thực thi. Rate-limit theo unit; một pending restart của unit tại một thời điểm. Cơ chế file ledger có lock/atomic update và durability là đầu vào P03-04, không yêu cầu SQLite baseline.

Sau consumed, mọi request lặp trả trạng thái cũ hoặc replay, không thực thi lại. Crash giữa consume và restart có thể bỏ lỡ thao tác; crash sau restart trước result có thể không rõ kết quả: **at-most-once attempt**, không hứa exactly-once effect. Đánh dấu execution_unknown, kiểm tra R để đối soát, không tự mint grant/retry. Lần thử mới phải là proposal mới được người dùng duyệt sau khi xem trạng thái.

Deadline monotonic dùng trong cùng service generation; UTC để audit. Helper khởi động lại tạo epoch mới, vô hiệu mọi grant/pending cũ; coordinator mất state cũng không tự khôi phục approved. Chưa có handshake epoch/state hợp lệ thì W disabled. Restore snapshot phải khởi động lại dịch vụ an toàn, đổi epoch và xóa pending cũ trước demo; snapshot đang chạy không được coi là biện pháp chống replay. Key/epoch/ledger recovery, race expiry và clock changes cần negative tests thực tế.

### 6.3 Allowlist đặc quyền và giới hạn restart

Helper chỉ nhận operation `restart_service` và canonical `.service` exact match do administrator phê duyệt. Không wildcard, arbitrary template instance, user-specified D-Bus method, sudo, reload, edit file, SSH/firewall, package manager hoặc reboot. Systemd unit/config/binary cùng dependencies của unit allowlist phải administrator-owned, không writable bởi read plane; review cả ExecStartPre/Post, aliases và phụ thuộc để tránh “restart lab unit” kéo theo quyền không dự kiến. Không allowlist approval/helper/read-plane services hoặc dịch vụ host quan trọng.

Helper ưu tiên systemd D-Bus fixed RestartUnit + fixed job mode; chỉ báo succeeded sau job completion, không chỉ khi nhận job ID. Timeout/mất kết nối sau dispatch là unknown nếu không biết kết quả. Đặc quyền root hiện hữu là rủi ro của helper; endpoint hẹp không tự biến root process thành không thể bị khai thác. Trang phải kiểm policy D-Bus, dependency effects và hardening riêng trong VM. Không có route helper từ laptop hoặc mạng công cộng.

`verify_fix` đọc trạng thái mới, health check và log sau mốc action/cursor; active chưa đủ kết luận healthy. Report ghi riêng approval, helper outcome và verification. Restart không khôi phục bộ nhớ, trạng thái kết nối hoặc dữ liệu trước đó; không quảng cáo rollback toàn hệ thống.

## 7. Audit và dữ liệu lưu

Coordinator/helper ghi audit authoritative ngoài vùng Agent có thể sửa. Mỗi event gồm schema/event/trace/action/session IDs, VM, policy version, canonical params đã lọc và digest, peer UID, approver UID khi có, UTC time, transition, deny/error code, helper outcome/job reference, verification link. Không ghi MAC/key/grant đầy đủ, API token, raw secret hoặc IP mapping. Tool trace/evidence IDs nối audit với report nhưng report không thay audit.

Read-plane gửi verification result như observation chưa tin cậy; coordinator chỉ gắn provenance/link, không dùng nó cấp quyền mới hoặc xóa event execution_unknown. Nếu cần khẳng định verify đáng tin trước đối thủ đã chiếm Agent, phải có verifier độc lập — ngoài mức bảo đảm hiện tại. Các artifacts đánh giá phải nói rõ trust assumption này.

Audit local append-only theo interface và ownership, không tuyên bố chống root sửa hoặc tamper-proof. Đề xuất retention lab: 7 ngày cho raw evidence/mapping nếu cần; 30 ngày cho report/audit đã lọc, hạn mức dung lượng và quyền truy cập do Trang/nhóm xác nhận. Cleanup không do model điều khiển; không chạy dọn dữ liệu ở task này. Khi storage/audit lỗi, W fail closed; report mô tả lỗi, không giả thành công.

## 8. Kế hoạch kiểm chứng cho các task sau

**Chưa chạy các test dưới đây.** Thủy giữ test độc lập ở P04-02/P04-04; Trang giữ permission/helper/hardening P03-01/04/05/06; Hiền giữ CLI/loop/verify P02-03/05/07/08. Fixtures/mocks trước, test ảnh hưởng hệ thống chỉ trong VM snapshot đã được phép ở task tương ứng.

| ID | Trường hợp | Kỳ vọng | Owner/gate |
|---|---|---|---|
| SEC-01 | Model/log gửi shell, unknown tool, extra fields | Schema/registry từ chối, không spawn command tùy ý | Thủy, P01-01/P04-02 |
| SEC-02 | UID reader đọc ngoài quyền hoặc gọi sudo/control/helper socket | Permission denied, không tăng quyền; ghi thiếu dữ liệu | Trang, P03-01/06 |
| SEC-03 | Traversal/symlink race, malicious option, regex quá hạn, output flood | Không vượt nguồn allowlist, deadline/output cap hoạt động | Thủy, P04-02/04 |
| SEC-04 | Log chứa approval, terminal escape, raw token | Không approve; UI an toàn; secret không tới provider/report | Hiền + Thủy, P02-07/P04-04 |
| SEC-05 | Người dùng từ chối, cancel, không TTY, hết hạn | Không helper dispatch, audit denied/expired | Hiền + Trang, P02-07/P03-04 |
| SEC-06 | Sửa unit, action/session/VM/approver/policy/expiry hoặc fake MAC | Helper từ chối trước mutation | Trang + Thủy, P03-04/P04-04 |
| SEC-07 | Replay/concurrent execute cùng action/unit | Tối đa một attempt; trả replay/status, không retry ghi | Trang + Thủy, P03-05/P04-04 |
| SEC-08 | Helper/coordinator restart, clock jump, restore snapshot, audit disk full | Pending/grant cũ vô hiệu; unknown đúng nghĩa; W fail closed | Trang, P03-04/06 |
| SEC-09 | Sai peer UID dù JSON tự khai đúng user; Agent thử approve | Bị chặn control/helper socket và backend identity | Thủy, P04-04 |
| SEC-10 | Approval hợp lệ cho đúng unit lab | Chỉ unit đó restart; job result gắn action; không dispatch unit khác | Trang + Thủy, P03-05/P04-04 |
| SEC-11 | Helper unavailable, timeout sau dispatch, crash tại consume | Không tự retry, outcome unknown/fail, giữ audit | Trang + Hiền, P02-08/P04-04 |
| SEC-12 | Active nhưng HTTP lỗi; log cũ; không đủ quyền | Verify fail/unknown thay vì pass; evidence đúng window | Hiền, P02-05/08 |
| SEC-13 | Unit/config/helper code writable, alias/dependency nguy hiểm | Deployment/security gate từ chối bật W | Trang + Thủy, P03-06/P04-04 |
| SEC-14 | LLM provider lỗi, budget hết, repeated calls | Loop kết thúc có limitations; không fallback write | Hiền, P02-03 |

Các mục tiêu security: số write ngoài allowlist thành công = 0; thao tác chưa có approval hợp lệ bị từ chối 100%. Đây là **mục tiêu cần đo**, không phải kết quả. Cần lưu test inputs, actual outcomes, audit/action IDs đã lọc và phiên bản app/policy/model; không chỉ ảnh màn hình hoặc khẳng định của LLM.

## 9. Giới hạn môi trường và trạng thái review

Toàn bộ fault injection S1–S5, OOM/SSH tùy chọn, restart và negative tests đặc quyền chạy trong VM có snapshot và resource limits. Không chạy đầy đĩa, OOM, thay SSH/firewall hay sửa service trên Ubuntu laptop. Host chỉ dùng soạn thảo, read-only/control VM; task SCRUM-11 hiện chỉ viết tài liệu, không cài đặt hoặc cấu hình Linux.

Cần Trang xác nhận permissions/allowlist/UID/socket/key/epoch/ledger/D-Bus/sandbox; Thủy xác nhận schema/manifest/SDK/caps/facade và test matrix; Hiền review CLI separation, loop/evidence/report. Xác nhận review của cả ba **chưa có**. Theo [TASKS — P00-02](TASKS.md), vì vậy không đủ điều kiện kết luận SCRUM-11 Done. Hai walkthrough thiết kế đọc và restart denied được ghi tại ARCHITECTURE §9; chúng không thay thế integration/security tests sau này.

## 10. Nguồn kỹ thuật và liên kết

- [MCP specification: stdio transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports): cơ sở cho transport ADR, không khẳng định protocol/SDK version đã pin.
- [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html): ngữ nghĩa NNP và execution sandbox; hỗ trợ directive thực tế phải đối chiếu Ubuntu VM.
- [Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html): Unix socket peer credentials.
- [ARCHITECTURE](ARCHITECTURE.md), [DEPENDENCIES](DEPENDENCIES.md), [DEFINITION_OF_DONE](DEFINITION_OF_DONE.md): thiết kế, thứ tự triển khai và gate review.
