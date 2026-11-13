# SCRUM-27 CLI tests

Từ repository, dùng Python 3.12.16 với dependencies đã chuẩn bị:

```bash
PYTHONPATH=src python -B -m unittest discover -s tests/cli -v
python -B -I tests/agent_loop/run_tests.py
RUN_MCP_STDIO_SMOKE=1 PYTHONDONTWRITEBYTECODE=1 python tests/integration_boundary/run_composed_tests.py --baseline-ref origin/main
```

CLI suite: **35 tests**, không gọi network, MCP server hoặc Ollama. Lifecycle tests
hủy async task đang chờ và kiểm dispatch đã join; timeout không retry. Filesystem
tests chỉ ghi file tạm, kiểm no-overwrite, symlink, traversal, private mode và lỗi
publish. Terminal tests gọi Python subprocess chạy module CLI, không Linux tools.
Snapshots được review như output synthetic; không tự regenerate trong tests.

SCRUM-24 suite: **64 PASS**. Composed suites: **153 PASS** (57 Adapter, 46 Client,
50 Boundary), gồm real SDK stdio **synthetic** server nếu bật opt-in. Thực hiện
stdio suite trong môi trường cho phép subprocess pipes; đây không phải real Linux
collector/LLM inference. Không xem kết quả synthetic là acceptance production.

Ba snapshot files chứa report đầy đủ mẫu, không credentials hoặc log thật.
