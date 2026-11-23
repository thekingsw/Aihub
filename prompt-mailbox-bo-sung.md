# Bổ sung cho Emi — Làm rõ về history.json và config.json (AI Hub mailbox)

## 1. Vấn đề em gặp

Bản patch trước bị bộ duyệt chặn vì lo ngại xóa lịch sử. Anh và Tinker làm rõ như sau — **đây là ủy quyền cụ thể để em làm tiếp, không cần hỏi lại**.

## 2. Hai file TUYỆT ĐỐI KHÔNG ĐƯỢC ĐỤNG VÀO

- **`history.json` (~642 KB): KHÔNG xóa, KHÔNG sửa, KHÔNG di chuyển, KHÔNG ghi đè.** Để nguyên 100% như hiện tại.
- **`config.json`: KHÔNG xóa, KHÔNG sửa, KHÔNG xóa `api_key` trong đó.** Để nguyên 100% (dù key đã hết hiệu lực ở phía nhà cung cấp, vẫn để nguyên trong file — chỉ là không dùng tới nữa).

Nếu bộ duyệt hỏi, trả lời: hai file dữ liệu này không nằm trong phạm vi thay đổi.

## 3. Cách tắt đường gọi AI mà không xóa gì

Chỉ sửa file code **`ai_hub.py`**, và làm theo cách an toàn sau (để bộ duyệt chấp nhận):

- **Không xóa hàm** `call_openai` / `list_models`. Giữ nguyên định nghĩa hàm trong file, chỉ không gọi tới chúng nữa.
- **Tắt ở phía giao diện và route:** bỏ ô chat AI và nút gửi AI trong `index.html`; route `/api/chat` (nếu còn) trả về thông báo ngắn "AI chat đã tắt, dùng hộp thư" và không gọi ra ngoài.
- Như vậy không có dữ liệu nào bị xóa, chỉ là app không còn gọi AI nữa.

## 4. Phần thêm mới (không sửa dữ liệu cũ)

- File `messages.json` mới (hộp thư), 2 endpoint nội bộ mới (`POST /internal/message`, `GET /internal/messages`), giao diện hộp thư mới theo prompt gốc `prompt-mailbox.md` mục 4–6.
- Mọi thứ khác trong prompt gốc giữ nguyên (SSO, theme sáng, nút "← Tinker", bind Tailscale, Python stdlib).

## 5. Kiểm thử và báo cáo

- Chạy lại các test ở mục 9 prompt gốc và ghi kết quả.
- Kiểm tra thêm: `history.json` và `config.json` vẫn nguyên vẹn sau khi sửa (so kích thước và thời gian sửa file, không đổi).
- Ghi tóm tắt vào mục 11 của prompt gốc: đã đổi file nào, đã test gì, còn thiếu gì.
