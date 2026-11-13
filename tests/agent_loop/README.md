# SCRUM-24 — synthetic coordinator và Incident Report

Chạy từ repository bằng Python **3.12.16**, với dependencies trong `pyproject.toml`:

```bash
python -B -I tests/agent_loop/run_tests.py
```

Runner tự tìm `src/` và fixtures bên trong repository, không dùng PYTHONPATH,
đường dẫn ứng viên, worktree khác hoặc thư mục hiện hành. Có thể chạy runner bằng
absolute path từ thư mục khác. Python executable có thể đến từ venv đã chuẩn bị;
đó là runtime dependency, không phải dữ liệu/source ngoài repository.

Để tạo môi trường riêng khi cần (không dùng global pip):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[mcp]'
.venv/bin/python -B -I tests/agent_loop/run_tests.py
```

Offline tests chỉ cần jsonschema 4.26.0/referencing 0.37.0. Extra `mcp` pin 2.3.0 dùng
khi chạy stdio tests của SCRUM-22. Không có model download hoặc inference.

Hiện có **64 tests**: 4 fixture validation, 39 coordinator/Bridge/Client in-memory,
7 isolated coordinator unit/mock, 13 renderer unit và 1 synthetic report-flow test
(bao phủ 3 kịch bản). Fixture validation không tương đương Agent acceptance.

`test_candidate.py` chứa scripted decisions và fixture-only interpreter; các schemas
ba tools chỉ thuộc synthetic tests, không phải replacement của SCRUM-12 contracts.
Tests kiểm no-dispatch, exact tool/model counts, policy, schema, scope, state trace,
timeout, cancellation, pending cleanup và evidence retention. Renderer tests kiểm
redaction, citations, Markdown/HTML/terminal escaping và report sau budget/error.

Không live LLM hoặc Linux collector. Các subprocess stdio tests nằm ở suites SCRUM-22,
không nằm trong64 tests này. Xem `docs/SCRUM-24_AGENT_LOOP.md` và review report.
