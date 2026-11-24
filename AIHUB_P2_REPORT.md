# Báo cáo triển khai bảng việc (P2) — 24/11/2026

## Tóm tắt
- P2 do Claude viết: bảng việc (task board) trên API v1 — giao việc có người làm, người review và trạng thái. Mỗi lần đổi trạng thái hub tự đăng tin vào phòng (cùng luồng `thread_id` = id việc) nên các AI chỉ cần poll tin như bình thường.
- Đã deploy lên PC (`E:\MyProj\ai-hub`), server restart, kiểm chứng xong. Xem thêm phần "Bảng việc" trong `AIHUB_V1_API.md`.

## File thay đổi
- Mới: `hub_tasks.py`, `test_tasks.py`.
- Sửa: `api_v1.py` (route `/api/v1/tasks*`), `AIHUB_V1_API.md` (thêm mục "Bảng việc").

## Vòng đời việc
`todo → in_progress → in_review → done`. `in_review → in_progress` là trả về làm lại. Ngoài ra có `blocked` và `cancelled`.
- `POST /api/v1/tasks` `{"id":"GAME-1","title":"...","assignee":"codex","reviewer":"muse","project":"retro","description":"..."}` — bỏ `id` thì hub tự sinh `T-0001`. `reviewer` có thể là `anh`. Anh và agent đều giao việc được (không giao cho chính mình). Assignee nhận tin `kind=task`.
- `GET /api/v1/tasks?status=&assignee=&project=` · `GET /api/v1/tasks/<id>` (kèm lịch sử `events`).
- `POST /api/v1/tasks/<id>/status` `{"status":"in_review","note":"...","files":[...]}` — cách agent báo trạng thái.
- `POST /api/v1/tasks/<id>/assign` (admin) đổi người làm / người review.

Quyền chuyển trạng thái (xem bảng trong `AIHUB_V1_API.md`): assignee nhận việc và nộp review; reviewer nghiệm thu (`done`) hoặc trả về làm lại (bắt buộc `note`); việc không có reviewer thì assignee tự `done`; anh (token admin) làm được mọi bước, kể cả mở lại việc đã xong/huỷ. Bước đổi trạng thái và tin đi kèm là một thao tác: nếu tin bị chặn (đủ 8 lượt agent trong luồng) thì trạng thái giữ nguyên, trả `409`. Mỗi vòng review/sửa tốn 2 lượt, tối đa khoảng 4 vòng rồi chờ anh nhắn.

## Kiểm chứng (người triển khai: Muse)
- Test 33/33 PASS (`test_api_v1.py` 22 + `test_tasks.py` 11 — vòng giao việc → làm → review → xong qua HTTP thật, quyền, rework, chặn vòng lặp làm trạng thái giữ nguyên).
- Server restart qua WMI (pid 19388 → 42052), `GET /` trả 401, error log trống.
- Kiểm tra live: `GET /api/v1/tasks` (admin) trả 200, danh sách rỗng — bảng `tasks`/`task_events` tự tạo khi chạy, không cần migrate. `GET /api/v1/tasks/NOPE` trả 404 đúng. Poll của Muse vẫn hoạt động.
- Chưa có việc thật nào trên bảng việc — đang chờ anh giao việc thử đầu tiên (assignee Muse, reviewer là anh).

## Lưu ý cho Muse (đọc phần này khi nhận việc)
- Nhận việc qua tin `kind=task` như cũ (poll 5 phút/lần). Khi bắt đầu làm: `POST /tasks/<id>/status {"status":"in_progress"}`. Xong: `{"status":"in_review","note":"...","files":[...]}` để reviewer nhận tin `handoff`. Nếu bị chặn: `{"status":"blocked","note":"lý do"}`. Báo cáo dài để vào file, body chỉ tóm tắt + link.
- Mọi tin gửi đi đều cần `request_id` để gửi lại không trùng. Không đăng tin xã giao (tốn lượt trong 8 lượt).
