# Chuyển hẳn từ hộp thư cũ sang AI Hub v1

Hộp thư cũ (Tinker, Emi, phòng chung theo phase) và hệ v1 (agent có token riêng, bảng việc, tệp, runner) chạy song song trên cùng một server. **Không bước nào dưới đây sửa hay xoá hộp thư cũ**, nên có thể dừng hoặc quay lại bất cứ lúc nào.

Đối chiếu tên: **Tinker = Muse** (agent `muse`, kiểu pull). **Emi/Sol (Codex)** sẽ là một agent `push` do `agent_runner.py` chạy.

| Cũ | Mới |
|---|---|
| `GET/POST /internal/messages` (Tinker, Emi) | `GET/POST /api/v1/messages` (token riêng mỗi agent) |
| `emi_worker.py` chạy Codex | `agent_runner.py` + agent `codex` (hoặc agent khác) |
| Phòng chung và phase `/phase plan|done|pass|fail` | Bảng việc: người làm, người review, trạng thái (`/api/v1/tasks`) |
| `ref` (đường dẫn tài liệu trong tin) | Tệp đính kèm có id (`/api/v1/files`) |
| `room.html` ở `/` | `hub.html` ở `/hub`; giao diện cũ còn ở `/legacy` |

## Các bước (làm lần lượt, mỗi bước kiểm tra xong mới sang bước sau)

1. **Áp bản mới và restart** AI Hub (như các lần trước). DB tự nâng cấp tại chỗ (thêm cột `legacy`), không cần làm gì thêm.
2. **Nhập lịch sử**: `/hub` → tab **AI** → *Nhập lịch sử hộp thư cũ* → *Xem trước* → *Nhập*. Rồi mở **Phòng → 📜 Hộp thư cũ** kiểm tra. Lịch sử chỉ để xem, không agent nào nhận được. Có thể chạy lại bất cứ lúc nào để kéo thêm tin cũ mới phát sinh.
3. **Muse dùng hoàn toàn v1**: Muse đã poll `/api/v1`. Kiểm tra cron của Muse không còn gọi `/internal/messages`, và các việc đang dở của Muse nằm trong bảng việc chứ không chỉ trong tin cũ.
4. **Thay Emi bằng runner**: tab **AI** → *+ Thêm AI* (id `codex`, kiểu push) lấy token; `setx AI_HUB_TOKEN_CODEX ...`; cấu hình `runner.json`; chạy `start_runner_desktop.ps1` từ màn hình đăng nhập (Session 1). Giao một việc nhỏ ở tab **Việc** và xem nó đi hết vòng giao → làm → review. Khi ổn, **dừng `emi_worker`** (Scheduled Task cũ) để hai bên không cùng gọi Codex. Cần provider Codex hoạt động trở lại trước bước này.
5. **Đổi trang chủ**: đặt `AI_HUB_DEFAULT_UI=hub` trong môi trường của tiến trình AI Hub rồi restart. `/` mở giao diện mới, `/legacy` vẫn mở giao diện cũ. Quay lại bằng cách bỏ biến này.
6. **Ngừng hộp thư cũ** khi không còn gì dùng nó (nhìn `/legacy` thấy không còn tin mới trong vài ngày): tắt `emi_worker`, `tinker_worker`, `tinker_room_listener`. **Giữ `messages.json`** làm bản lưu (đã nhập vào `/hub`). Mã cũ để nguyên trong repo, chưa xoá gì.

## Lưu ý
- **Phòng chung kiểu phase** (`room_dispatch`) không có bản sao nguyên văn trong v1; vai trò của nó do bảng việc (giao việc, review, trả về sửa, tối đa 8 lượt agent) đảm nhận. Lịch sử phòng cũ nằm trong tin đã nhập.
- Tin nhập từ hộp thư cũ không có `files` (tài liệu cũ chỉ là đường dẫn trong `ref`, được ghi thành chữ).
- Nhập khoảng vài nghìn tin mất dưới vài giây. Nếu một số hàng cũ hỏng (thiếu `id`, người gửi lạ, ngày sai) chúng bị bỏ qua và liệt kê tối đa 5 lý do trong kết quả.
