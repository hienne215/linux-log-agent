# SCRUM-23 — Live smoke cho Ollama Adapter

Chỉ dùng mock tool definitions, không thực thi tool hay gọi MCP. Script gọi Adapter thật, tối đa4 requests, mỗi case đúng1 lần, không retry. Chỉ chạy khi người vận hành cho phép live inference; không tự tải model hoặc chỉnh service.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 tests/smoke/ollama_adapter_smoke.py
```

- S1: plain text, yêu cầu SMOKE_OK.
- S2: native mock_service_status(unit=lab.service), require_tool=True.
- S3: native mock_listening_ports(port=80, protocol=tcp), strict JSON Schema, require_tool=True.
- S4: yêu cầu model trả plain text với mock tools vẫn quảng bá; Adapter require_tool=True phải trả OUTPUT_POLICY. Chỉ PASS khi quan sát xác nhận native_calls_count=0, content nonempty, thinking rỗng và response kết thúc bình thường. Nếu model vẫn gọi tool, case không được coi đã kiểm chứng text-only rejection.

Model/digest cố định theo benchmark Instruct; num_ctx4096, num_predict256, temperature0, num_thread4, stream=false, không think. Preflight chỉ đọc /api/version,/api/tags,/api/ps. Không warmup inference. Cấu hình trong request không làm thay đổi cấu hình Ollama service.

Parent process kiểm MemAvailable mỗi0.2s, dừng client và cả run nếu dưới1024MiB hoặc không đo được RAM. Có wall guard45s cho mỗi worker ngoài I/O timeout45s của adapter. Khi timeout/hủy chỉ đóng client, không kill Ollama; server có thể còn xử lý request cũ nên không retry. Adapter production vẫn chưa có hard wall deadline. Khi lỗi không mong đợi, không gửi case tiếp theo.

Kết quả mỗi run nằm ở thư mục timestamp UTC mới dưới results/. JSON ghi cases, schemas, model/version/digest, latency, usage, RAM minimum và pass/fail. Chỉ lưu exact expected mock arguments nếu chúng khớp; không ghi raw text, reasoning, provider body hoặc arguments bất kỳ. Với phản hồi bị adapter từ chối, usage được quan sát từ HTTP response bởi harness, không phải Completion trả về.

Đây là4 integration smoke cases, không phải benchmark accuracy18 lượt hoặc bằng chứng MCP/Agent đã tích hợp. Không tự chạy lại hoặc tăng budget khi case fail.
