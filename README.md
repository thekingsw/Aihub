# AI Hub — hộp thư cho Anh / Tinker / Emi

AI Hub là hộp thư nội bộ chạy trên PC (Windows, Python stdlib, không thư viện ngoài).
Emi là tên gọi chung cho đội làm việc qua Codex (Sol). Mỗi task chạy một lượt
`codex exec` mới, báo cáo về hộp thư, Tinker nghiệm thu rồi mới giao tiếp.

## Chạy

- Server: `start.bat` (đặt `AI_HUB_HOST`, `AI_HUB_INTERNAL_TOKEN`, chạy
  `python ai_hub.py`, cổng 8090, bind IP Tailscale). Khởi động lại server phải
  qua WMI `Invoke-CimMethod Win32_Process.Create`, không dùng `Start-Process`
  từ phiên SSH (process con chết khi SSH đóng).
- Worker: `start_worker_desktop.ps1 -RestartIdleWorker` qua Scheduled Task
  (worker bắt buộc chạy Session 1).
- Token nội bộ nằm trong `start.bat`, KHÔNG commit file đó.

## Thành phần chính

- `ai_hub.py` — server hộp thư (`/internal/message`, `/internal/messages`),
  endpoint đổi key xingchiapi (`POST /internal/rotate-codex-key`).
- `emi_worker.py` — worker: nhặt task cho Emi, chạy `codex exec` với
  `--skip-git-repo-check --sandbox workspace-write`, gửi báo cáo vào hộp thư.
- `mailbox_store.py` — lưu trữ hộp thư, giới hạn 8 lượt agent liên tiếp
  (báo cáo `kind=result` gửi cho anh được miễn).
- `room_dispatch.py` — phòng chung (Tinker + Sol, không cần `to=`), chia việc
  theo phase, Tinker nghiệm thu từng phase.
- `index.html`, `room.html` — giao diện hộp thư và phòng chung.
- `chat_emi.py`, `tinker_worker.py`, `tinker_room_listener.py` — công cụ phụ.

## Quy tắc làm việc

- Báo cáo hộp thư ngắn (tối đa ~5.000 ký tự); chi tiết chỉ trong
  `emi-report-<task_id>.md`.
- Một task = một thư mục trong 5 thư mục cho phép: `ai-hub`, `tinkerapp`,
  `sach-noi`, `aphoto`, `talkbook`. Không động vào `history.json`,
  `config.json`.
- Thiết kế xem `AIHUB_ROOM_DESIGN.md` (phòng chung),
  `AIHUB_KEY_ROTATE_DESIGN.md` (đổi key), `AGENTS.md` + `STATE.md` (trạng thái).

## Kiểm thử

`test_key_rotation.py`, `test_room.py`, `test_room_phases.py`,
`test_worker_reports.py` — chạy bằng `python -m unittest` (stdlib).
