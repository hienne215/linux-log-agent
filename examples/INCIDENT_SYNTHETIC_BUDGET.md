Ví dụ synthetic; không phải kết quả đọc Linux thật.

# Incident Report — read-only

Báo cáo nội bộ; không xác nhận đã sửa lỗi hoặc thực hiện hành động ghi.

- Status: budget_exhausted
- Tool attempts: 1
- LLM turns: 1
- Execution outcome: response_received
- Cleanup pending: false

## Evidence / provenance

### synthetic\-01
Source: synthetic fixture\:service\_status

> nginx\.service active; synthetic data only


## Nhận định

Văn bản model chưa được xác minh về ngữ nghĩa.

Không đưa ra kết luận chắc chắn từ trạng thái hiện tại.

## Citations

Không có citation.

## Limitations

- Citation hợp lệ chỉ chứng minh ID tồn tại, không chứng minh kết luận đúng.
- Timeout/disconnect không chứng minh tool chưa thực thi hoặc đã rollback.
- Nguồn evidence và độ bao phủ redaction cần được kiểm chứng riêng.
