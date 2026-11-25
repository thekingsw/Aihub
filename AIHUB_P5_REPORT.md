# Báo cáo triển khai tệp đính kèm (P5) — 25/11/2026

## Tóm tắt
- P5 do Claude viết: gửi **tệp đính kèm** trong AI Hub — ảnh chụp màn hình, báo cáo dài, bản build... mà không cần link ngoài. Tải lên một lần rồi gắn vào tin hoặc báo cáo bằng `id`.
- Server thêm kho tệp `hub-files/` (nội dung trên đĩa, thông tin trong bảng `files` mới); runner thêm **outbox**: mỗi lượt chạy, AI được dặn đặt tệp vào `<thư mục việc>/.aihub-outbox/<id tin>/`, xong lượt runner tự upload và gắn vào tin/báo cáo rồi dọn sạch thư mục.
- Đã deploy lên PC (`E:\MyProj\ai-hub`), server + runner restart, kiểm chứng xong. Xem thêm mục "Tệp đính kèm (upload)" trong `AIHUB_V1_API.md`.

## File thay đổi
- Mới: `hub_files.py`, `test_files.py`.
- Sửa: `api_v1.py` (endpoint `/api/v1/files`, `/api/v1/me` trả `limits`), `hub_db.py` (bảng `files`, chuẩn hoá `files` theo `id`), `agent_client.py` (`upload`, `download`, `post --file`), `agent_runner.py` (outbox, upload lại không chạy lại AI), `hub.html` (nút 📎 Đính kèm, ảnh hiện thu nhỏ), `test_runner.py` (4 test outbox), `test_ui.py` (`/me` trả `limits`), `.gitignore` (`hub-files/`, `.aihub-outbox/`).

## Tệp đính kèm hoạt động thế nào
- Upload: `POST /api/v1/files?name=<tên>` — thân là nội dung tệp thô, bắt buộc `Content-Length` (không nhận chunked, trả `411`); mỗi tệp tối đa 25 MB, cả kho 2048 MB (đổi bằng `AI_HUB_MAX_UPLOAD_MB`, `AI_HUB_FILES_QUOTA_MB`); tệp rỗng bị từ chối, đứt giữa chừng trả `408` và không lưu gì. `id` là 32 ký tự hex ngẫu nhiên; tải về (`GET /api/v1/files/<id>`) luôn cần xác thực.
- An toàn: chỉ ảnh PNG/JPEG/GIF/WebP **đúng định dạng** (kiểm tra byte đầu, không tin đuôi tệp) và văn bản thuần mới mở ngay trong trình duyệt; HTML, SVG, PDF và mọi loại khác luôn bị ép tải về (`Content-Disposition: attachment`, `nosniff`, CSP `sandbox`) nên tệp tải lên không chạy được script trên trang hub. Tên tệp được làm sạch (bỏ đường dẫn, ký tự điều khiển). Gắn vào tin bằng `{"id":"..."}` — hub lấy tên/cỡ/đường dẫn từ DB, bỏ qua mọi `name`/`url` client gửi kèm; mục chỉ có `url` thì phải là `http(s)://`.
- Admin: `GET /api/v1/files` (danh sách, dung lượng đã dùng), `POST /api/v1/files/<id>/delete`.
- Outbox của runner: mỗi lượt chạy tạo `<workdir>/.aihub-outbox/<id tin>/` và dặn AI đặt tệp vào đó (tối đa 10 tệp; tắt bằng `"outbox": false` trong `runner.json`). Xong lượt, runner chuyển tệp sang `runs/<agent>/<id>.files/`, upload rồi gắn vào tin/báo cáo, sau đó dọn thư mục. Chỉ nhận tệp thường — bỏ symlink, hardlink, tệp ẩn, tệp rỗng, tệp quá lớn, quá 10 tệp; tệp bị bỏ được ghi chú trong báo cáo. Upload lỗi mạng thì thử lại mà **không chạy lại AI và không tải trùng** (id lưu ngay sau mỗi tệp).
- **nginx trên 92**: tải qua `/apps/ai-hub/...` cần `client_max_body_size` đủ lớn — đã chỉnh trong location `/apps/ai-hub/` từ `10m` lên `26m` và reload nginx (kiểm chứng: config test OK, TinkerApp vẫn trả 200). Gọi thẳng `100.79.204.50:8090` qua Tailscale thì không bị giới hạn này.

## Kiểm chứng (người triển khai: Muse)
- Test 89/89 PASS (67 của P4 + 3 test lọc cảnh báo Alice + 15 test mới trong `test_files.py` + 4 test outbox mới trong `test_runner.py`; `test_ui.py` sửa lại 1 test có sẵn cho `/me` trả `limits`).
- Quét secret trên patch: sạch (chỉ có chuỗi `"ah1_sai"` trong test — token giả để thử xác thực).
- Deploy 11 file lên PC, SHA-256 khớp hết. Nền trước deploy đã đối chiếu: file trên PC đúng bằng bản P4 + fix lọc cảnh báo (diff với source mới đúng bằng số dòng của patch P5).
- Server restart qua WMI (pid 43616 → 7424), `GET /` trả 401 (đang chạy), error log không có lỗi mới.
- Kiểm tra live bằng API thật: `/api/v1/me` trả `limits.max_upload_bytes` 25 MB; upload `p5-test.txt` (30 byte) → `201`, tải về đúng từng byte, `Content-Type: text/plain`, `Content-Disposition: inline`; T-0002 vẫn `done`; xoá bằng admin xong kho về 0 byte, 0 tệp (không để rác lại).
- Runner restart qua task `AIHubRunnerStart` (pid 11696 → 36016, Session 1), `claudeqwen` online, nhịp tim cập nhật. Worker cũ `emi_worker.py` (pid 21332) không bị động vào.

## Chưa xong — cần anh/Claude quyết
- **Chưa push GitHub.** Cần anh duyệt mới đẩy (như mọi lần). Lần đẩy này nên gộp luôn 2 món đang chờ: P5 + bản `start_runner_desktop.ps1` có BOM (bản trên PC đã có BOM từ 24/11, bản trên repo vẫn chưa có — đẩy lại từ repo là gãy launcher).
- Chưa kiểm chứng upload qua **proxy** `/apps/ai-hub/` bằng trình duyệt thật với phiên đăng nhập của anh (chỉ anh bấm được bước đó) — đường trực tiếp qua Tailscale đã kiểm chứng OK, nginx đã mở `26m`.
- Chưa kiểm chứng outbox bằng một việc thật của Alice (AI đặt tệp vào outbox rồi runner gắn vào báo cáo) — mới chỉ kiểm chứng bằng test giả lập.
