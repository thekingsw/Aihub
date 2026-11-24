# Báo cáo triển khai API v1 (P1) — 24/11/2026

## Tóm tắt
- P1 do Claude viết: API v1 để nhiều AI làm việc chung như một phòng ban, mỗi AI một token riêng.
- Đã deploy lên PC (`E:\MyProj\ai-hub`), server restart, kiểm chứng xong. Muse đã kết nối với vai trò test + review.
- Tài liệu API: `AIHUB_V1_API.md`. Client mẫu: `agent_client.py`.

## File thay đổi
- Mới: `hub_db.py`, `api_v1.py`, `agent_client.py`, `test_api_v1.py`, `AIHUB_V1_API.md`.
- Sửa: `ai_hub.py` (thêm `import api_v1`, route mọi `/api/v1/*` sang `api_v1.dispatch`), `.gitignore` (thêm `hub.sqlite3`).

## Kiểm chứng (người triển khai: Muse)
- Test 22/22 PASS (`test_api_v1.py`: store + API qua HTTP thật — vòng task → report → review, long-poll, chống vòng lặp 8 lượt, chống giả danh `from`, token chỉ lưu SHA-256).
- Server restart qua WMI (pid 32040 → 19388), `GET /` trả 401 (server sống), error log trống.
- Admin tạo agent `muse` (HTTP 201, mode pull, poll 300s). `GET /api/v1/agents` thấy `muse` online. Poll `after=0` rỗng. Đã đăng tin chào tới anh (id 1).
- Cron `ai-hub-v1-watch` hỏi v1 mỗi 5 phút, báo tin mới vào chat AI Hub.

## Trạng thái hiện tại (để đọc tiếp)
- Hộp thư cũ (`/internal/messages`) vẫn chạy song song — worker của Emi dùng nó, chưa bỏ.
- Nhà cung cấp xingchiapi: `/v1/responses` trả 403 từ 22/11 (đổi key mới vẫn 403) — Emi/Codex chưa chạy được. Hai việc đang chờ provider lên lại: (1) tính năng chat-session — mỗi phiên chat ↔ 1 Codex session (spec trong tin hộp thư cũ id `20261122T103231-821c4fa10143`); (2) sửa lỗi 409 — task text chưa có trong repo này, hỏi Muse lấy file `aihub-409-fix-task-retry.txt`.
- Agent `codex` chưa tạo — admin tạo qua `POST /api/v1/agents` với `AI_HUB_INTERNAL_TOKEN`.
- Token các agent chỉ lưu SHA-256 trong `hub.sqlite3`, token chỉ hiện một lần lúc tạo/đổi. Token của Muse lưu riêng trên máy Muse, không có trong repo. `start.bat` (chứa internal token), `history.json`, `messages.json`, log và `hub.sqlite3` đều không commit.

## Lưu ý vận hành
- Restart server: qua WMI `Invoke-CimMethod Win32_Process.Create` (xem `AGENTS.md`); không dùng `Start-Process` từ phiên SSH (process con chết khi SSH đóng).
- Worker Emi (`emi_worker.py`) không đổi trong P1, không cần restart.
- `hub.sqlite3` là dữ liệu chạy, không commit (đã có trong `.gitignore`).
