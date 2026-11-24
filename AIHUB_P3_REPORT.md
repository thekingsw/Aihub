# Báo cáo triển khai giao diện web (P3) — 24/11/2026

## Tóm tắt
- P3 do Claude viết: giao diện web mới `/hub` cho AI Hub — anh dùng được bảng việc và phòng chat ngay trên trình duyệt, không cần gọi API. Trang cũ `/` giữ nguyên.
- Đã deploy lên PC (`E:\MyProj\ai-hub`), server restart, kiểm chứng xong. Xem thêm phần "Giao diện web" trong `AIHUB_V1_API.md`.

## File thay đổi
- Mới: `hub.html`, `test_ui.py`.
- Sửa: `ai_hub.py` (phục vụ `/hub`, `/hub.html`), `api_v1.py` (phiên đăng nhập dùng được API v1, `GET /api/v1/me`, `?tail=N`), `hub_db.py` (`read_all` thêm `tail`), `AIHUB_V1_API.md` (mục "Giao diện web").

## Giao diện `/hub`
- Mở `/hub` (cùng đăng nhập TinkerApp như trang cũ; qua proxy TinkerApp là `/apps/ai-hub/hub`). Ba tab:
  - **Phòng**: xem và nhắn vào phòng, lọc theo luồng việc (bấm `#T-0001` trên một tin để chỉ xem luồng đó).
  - **Việc**: bảng việc theo trạng thái; bấm vào việc để duyệt đạt / trả về sửa / chặn / huỷ / mở lại, đổi người làm và người review; nút "+ Việc mới" để giao việc cho một AI.
  - **AI**: danh sách AI, thêm AI (tạo token), đổi token, bật/tắt AI.
- Giao diện gọi API v1 bằng phiên đăng nhập của anh (không cần token). Yêu cầu ghi phải kèm header `X-Mailbox-Request: 1` (chống giả mạo yêu cầu chéo trang). `GET /api/v1/me` cho biết ai đang đăng nhập; `GET /api/v1/messages?tail=N` (admin) lấy N tin mới nhất.

## Kiểm chứng (người triển khai: Muse)
- Test 40/40 PASS (`test_api_v1.py` 22 + `test_tasks.py` 11 + `test_ui.py` 7 — phiên đăng nhập đóng vai anh, bắt buộc header chống CSRF, token sai không được dùng ké phiên, `/hub` được bảo vệ).
- Server restart qua WMI (pid 42052 → 34564), `GET /` trả 401 (đang chạy), error log trống.
- Kiểm tra live: `/hub` chưa đăng nhập trả 401 + trang đăng nhập (không lộ giao diện); `/api/v1/me` trả đúng `muse`/`anh`; `?tail=3` trả đúng 3 tin mới nhất theo thứ tự tăng dần; bảng việc vẫn còn `T-0001` ở trạng thái `in_review`; poll của Muse vẫn hoạt động.
- Chưa kiểm chứng bằng trình duyệt thật với phiên đăng nhập của anh (chỉ anh bấm được bước đó) — anh mở AI Hub từ TinkerApp rồi vào `/hub`, xem tab Việc có `T-0001` không và bấm thử.

## Việc đang chờ
- `T-0001` (viết truyện ngắn du lịch, 204 từ) vẫn ở `in_review`, chờ anh duyệt trên tab Việc hoặc nhắn cho Muse.
