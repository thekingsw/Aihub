# Báo cáo triển khai adapter push (P4) — 24/11/2026

## Tóm tắt
- P4 do Claude viết: adapter push `agent_runner.py` — AI chạy bằng dòng lệnh (Codex, Claude Code...) tham gia phòng làm việc. Runner canh hộp thư của agent bằng long-poll (nhận việc gần như tức thì), chạy CLI của AI đó trong thư mục làm việc, rồi tự báo kết quả về hub. AI chỉ cần làm việc và in báo cáo; nó không cần biết API và không bao giờ thấy token.
- Server thêm loại tin `notice` (thông báo, chỉ để biết, không đánh thức AI); tin huỷ việc giờ là `notice` thay vì `message`.
- Đã deploy lên PC (`E:\MyProj\ai-hub`), server restart, kiểm chứng xong. Xem thêm phần "Adapter push: `agent_runner.py`" trong `AIHUB_V1_API.md`.

## File thay đổi
- Mới: `agent_runner.py`, `runner.example.json`, `start_runner_desktop.ps1`, `test_runner.py`.
- Sửa: `agent_client.py` (thêm `me()`, `task()`, `transition()`), `hub_db.py` (loại tin `notice`), `hub_tasks.py` (huỷ việc → `notice`), `hub.html` (hiện `notice`), `AIHUB_V1_API.md` (mục adapter push), `.gitignore` (`runs/`, `runner-state/`, `runner.json`, `agent-runner.*.log`).

## Runner hoạt động thế nào
- Runner tự quyết theo **trạng thái việc**, không theo loại tin: việc giao cho agent (`todo`, trả về sửa, mở lại) → `in_progress`, chạy AI, rồi `in_review` kèm báo cáo; AI in dòng cuối `STATUS: BLOCKED: <lý do>` thì `→ blocked`; lệnh lỗi, quá giờ, không chạy được cũng `→ blocked`.
- Agent là reviewer và nhận `handoff` → chạy AI kiểm thử; dòng cuối `VERDICT: PASS` → `done`, `VERDICT: FAIL` → trả về sửa kèm lý do, không có VERDICT → báo anh (`kind=result`), việc giữ `in_review`.
- Tin riêng (`message`) → chạy AI và trả lời cùng luồng; AI in đúng `SILENT` thì không đăng gì. Tin gửi cả phòng, `result`, `notice`, việc đã xong/huỷ → bỏ qua, không tốn lượt chạy.
- An toàn: tiến trình con chạy với môi trường tối thiểu, mọi biến `AI_HUB*` bị loại, token lọt vào output cũng bị che; `max_runs_per_hour` (mặc định 12) chặn chạy tràn; `timeout_seconds` (mặc định 900) quá giờ thì giết cả cây tiến trình, việc chuyển `blocked`; mốc `after` lưu ở `runner-state/<agent>.json`, chạy xong mà chưa báo được thì lần sau chỉ báo lại, không chạy AI lần nữa; hub chặn 8 lượt thì kết quả vẫn báo cho anh bằng `kind=result`; báo cáo dài lưu ở `runs/<agent>/<id>.md`; `projects` ánh xạ tên dự án sang thư mục, dự án không có trong danh sách thì dùng `workdir` mặc định; `fail_patterns` bắt lỗi môi trường dù lệnh thoát mã 0 (mặc định có lỗi "runner pipe-in" của Codex khi chạy ngoài Session 1); runner gửi nhịp tim mỗi 30 giây nên agent hiện **online** trên `/hub` kể cả khi đang chạy lâu.
- Long-poll đã có sẵn từ P1 (server hỗ trợ `?wait=`, `hub.wait_for`); runner chỉ dùng nó, không cần sửa server thêm.

## Kiểm chứng (người triển khai: Muse)
- Test 67/67 PASS (`test_api_v1.py` 22 + `test_tasks.py` 11 + `test_ui.py` 7 + `test_runner.py` 27 — AI giả chạy thật qua CLI, làm việc/review/chat, chịu lỗi, hết hạn mức, mất kết nối khi báo, token không lọt sang tiến trình con).
- Quét secret trên file đổi: sạch (không có token thật, không có bearer token).
- Server restart qua WMI (pid 34564 → 43616), `GET /` trả 401 (đang chạy), error log trống.
- Kiểm tra live: `/hub` chưa đăng nhập trả 401 (không lộ giao diện); `/api/v1/me` trả đúng `muse`/`anh`; bảng việc vẫn còn `T-0001` ở trạng thái `done`; poll của Muse vẫn hoạt động (`last_id` 6, không có tin mới); worker cũ `emi_worker.py` (pid 21332) không bị động vào.
- Loại tin `notice` được kiểm chứng bằng test (huỷ việc → `notice`), chưa đăng tin `notice` thật lên hub để tránh làm ồn phòng.

## Chưa xong — cần anh/Claude quyết
- **Runner chưa chạy.** Muốn Codex nhận việc qua runner thì cần: tạo agent `codex` (mode `push`) ở tab **AI** trên `/hub` và lấy token một lần; copy `runner.example.json` thành `runner.json` và sửa đường dẫn; `setx AI_HUB_TOKEN_CODEX "ah1_..."` (mở lại PowerShell sau khi setx); chạy `start_runner_desktop.ps1` **từ màn hình đăng nhập Windows (Session 1), không qua SSH/WMI** (Codex cần phiên desktop mới tạo được tiến trình con). Thử nhanh: `python agent_runner.py --config runner.json --once`.
- Em chưa làm bước này vì runner bắt buộc chạy Session 1 (không chạy qua SSH được), và cần anh quyết có dùng runner thay worker cũ (`emi_worker.py`) cho việc của Codex không. Worker cũ vẫn chạy và vẫn xử lý việc qua hộp thư cũ; hai cái không giẫm nhau (runner chỉ lo việc v1 cho agent push, worker chỉ lo hộp thư cũ), nhưng giao việc cho Codex thì phải chọn một đường.
- Chưa kiểm chứng `/hub` bằng trình duyệt thật với phiên đăng nhập của anh (chỉ anh bấm được bước đó).
