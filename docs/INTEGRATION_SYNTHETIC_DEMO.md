# CLI → Async LLM → synthetic MCP demo

**SYNTHETIC DEMO, không Linux diagnostics thật.** Default mock được giữ nguyên;
nhánh trusted backend thêm lựa chọn Ollama giới hạn, xem [TRUSTED_BACKEND.md](TRUSTED_BACKEND.md).
Các lệnh và kết quả bên dưới là mock. Entry point độc lập;
`python -m agent.cli` và các scenario cũ giữ nguyên.

## Dependencies và chạy

Python 3.12; dependency chính xác theo `pyproject.toml`: jsonschema4.26.0,
referencing0.37.0, optional MCP2.3.0. Dùng venv riêng đã có dependencies hoặc,
sau khi clone đúng nhánh integration, chuẩn bị như sau:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[mcp]'
# Chạy từ repository root; không cần editable install nếu dùng PYTHONPATH=src.
PYTHONPATH=src python -B -m agent.integration_demo --scenario active \
  --question 'Kiểm tra Nginx trong fixture' --report integration-active.md
PYTHONPATH=src python -B -m agent.integration_demo --scenario port-conflict \
  --question 'Tại sao Nginx fixture failed?' --report integration-port-conflict.md
PYTHONPATH=src python -B -m agent.integration_demo --scenario permission-denied \
  --question 'Đọc journal trong fixture' --report integration-permission-denied.md
```

Ở backend mock mặc định: không chạy inference/Ollama, không cần Ollama service/model. Có thể đọc câu hỏi từ
một dòng stdin nếu bỏ `--question`. `--timeout` là tổng ngân sách tới120s, gồm
initialize/discovery; cleanup có budget riêng, **không phải hard wall deadline**.

| Scenario | Expected status / exit | Tools / LLM turns | Evidence |
|---|---|---|---|
| active | concluded /0 |1/2 | synthetic service_status; HTTP health chưa kiểm tra |
| port-conflict | concluded /0 |2/3 | synthetic service_status + listening_ports, cổng80 |
| permission-denied | limited /1 |2/3 | synthetic service_status + query_journal bị từ chối |

Exit1 cho permission-denied là kết quả giới hạn có chủ đích. `concluded` chỉ kết thúc
flow fixture; `conclusion_verified` vẫn false. Citation chỉ xác nhận ID tồn tại.

Stdout là cùng chuỗi Markdown được lưu vào `reports/<basename>.md`, luôn có nhãn
SYNTHETIC DEMO. Markdown escape provenance/text; ký tự như `_`, `-`, `/` có backslash
trong source Markdown là đúng. Report không chứa câu hỏi hay raw reasoning.
Tên file chỉ basename hợp lệ, không path traversal/symlink/overwrite; exit3 nếu lưu
thất bại. Chạy lại cần chọn tên report mới; không xóa/ghi đè report cũ tự động.

## Đường đi thực sự

`integration_demo.__main__` → `DemoSession` → `AsyncDecisionSource` → Coordinator
→ ToolBridge → StdioMCPClient → official SDK2.3.0 → `integration_demo/server.py`.
Server trả fixture evidence; schema/provenance kiểm tra chặt trước Observation.
Coordinator yêu cầu lượt model tiếp theo; renderer cũ tạo Incident Report.

Mỗi lượt model là subprocess owner thật, chạy sync OllamaAdapter với **MockHTTP**,
không socket/HTTP server. Mock responses có kịch bản trong `fixtures.py`, không
phải khả năng suy luận tự do của model. Model không tạo evidence mới: citations chỉ
lấy từ observations thực nhận. Tool registry/schema là fixtures ứng dụng tin cậy,
không phải SCRUM-12 contracts. Server có annotation `dict[str, Any]` để SDK cung cấp
structuredContent; thiếu structuredContent bị fail closed, không parse prose để cứu.

Chỉ cho service_status/query_journal/listening_ports với unit nginx.service theo
scenario. Không Linux commands, shell, privileged helper, restart hoặc approval.
Tên/arguments invalid bị Adapter và Bridge từ chối trước MCP dispatch. Server launch
cố định từ code tin cậy, không cho CLI/model chọn executable. Underscore fault seams
chỉ dùng tests, không có CLI flags. Dữ liệu tự nhận synthetic không làm nó tin cậy:
binder kiểm cả fixture version/scenario/tool/id/source/text, không lấp metadata thiếu.

## Lifecycle và giới hạn

Dùng `run_coordinator` để rejoin model và executor; DemoSession còn disconnect
session MCP khi request thành công (Bridge idle không tự đóng session). Cleanup owner
riêng luôn thử cả ba joins; request/cleanup budgets tách nhau. Pending owner được giữ
trong session, report gắn cleanup_pending, không reconnect/retry. CLI giữ event loop,
báo pending trên stderr và chỉ lặp **cleanup joins**, không lặp model/tool calls.
Caller thư viện phải giữ session và gọi finish_cleanup đến khi xác nhận hoàn tất.
Không đóng event loop/force-exit khi owner pending. OS teardown có thể kéo dài quá
request budget. Kill worker **không chứng minh backend Ollama đã dừng inference**;
trong demo này hoàn toàn không có inference backend được gọi.

Timeout/disconnect sau dispatch: execution_state unknown; không khẳng định tool
chưa chạy/rollback. Cancellation không tiếp tục reasoning. Budget exhaustion giữ
safe evidence đã nhận. Error/status không log exception/body/credentials; redaction
trước IPC và trong Boundary/Coordinator/renderer. Các giới hạn redaction dữ liệu thật
của Adapter vẫn tồn tại; mock data không chứng minh an toàn cho log thật.

## Tests

```bash
# Chạy trên tree integration hiện tại, không overlay từ worktree khác.
PYTHONDONTWRITEBYTECODE=1 python -B tests/integration_demo/run_tests.py
# Chỉ integration mới; cần MCP2.3.0, không optional skip ngầm.
PYTHONPATH=src python -B -m unittest discover -s tests/integration_demo -v
```

Lượt kiểm thử13/11/2026: **285 regression PASS +29 integration PASS =314 PASS,
0 FAIL,0 SKIP**. Trong285 regression có8 real SDK synthetic stdio tests.
Ba demo terminal đều xuất report trùng stdout; cleanup pending=false.
Runner gồm regression PR#7/#8/#9/#10 và tests integration mới. Phân biệt mock HTTP,
SDK stdio synthetic và live model: **Live Ollama NOT RUN; real Linux NOT RUN**.
Tests quan sát subprocess SDK returncode, model owner, Client owner, Bridge/Coordinator
pending operation; kiểm cả cancellation/timeout/late result/cleanup pending/rejoin.

## Git và bước kế tiếp

Nhánh riêng integration/cli-async-mcp-demo từ PR#9 ab05df01; normal merge PR#10
4cb5ab66 (mang main990f41f). Merge commit108b6e0; PR#7/#8 là ancestors của PR#9.
Không thay branch/base/HEAD PR hiện có. Không đưa PR#6 chưa finalized vào đây.

Bước ngắn nhất sang live LLM: ở một thay đổi được review riêng, thay factory fixed
MockHTTP worker bằng AsyncOllamaAdapter mặc định, giữ AsyncDecisionSource và
run_coordinator, SDK synthetic server và interpreter fixture. Tách nhãn rõ **live LLM
+ synthetic MCP/data**, xác minh prompts/native selection/conclusion bằng tests
mock trước, rồi xin phê duyệt riêng giới hạn inference/RAM. Đây là kế hoạch lúc PR#11 được tạo; lựa chọn backend trên nhánh mới được mô tả
trong TRUSTED_BACKEND.md. Không đổi nhãn evidence thành Linux thật.

Live MCP còn chờ Thủy finalized registry/schema/correlation/envelope/evidence và
Trang xác nhận VM/UID/permissions/topology. Không SCRUM Done hoặc production-ready.
