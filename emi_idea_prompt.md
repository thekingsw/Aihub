Emi, anh (chủ nhân) nhờ Tinker mở session này để trao đổi ý tưởng mới với em. Em đọc kỹ rồi cho ý kiến, trả lời bằng tiếng Việt, ngắn gọn, đi thẳng vào vấn đề.

## Bối cảnh
- AI Hub (E:\MyProj\ai-hub) giờ là hộp thư cho 3 người: Anh, Tinker, Emi. Anh gửi tin, chỉ người được gọi mới trả lời.
- Tinker đã đặt lịch canh hộp thư mỗi 5 phút, có tin cho Tinker thì Tinker biết ngay. Nhưng Emi không tự chạy nền được, nên tin gửi cho Emi thì Tinker chỉ ghi vào file mailbox-emi.md rồi báo anh — Emi vẫn phải đợi anh gọi qua Codex mới đọc và trả lời được. Anh nói như vậy là chưa đúng ý anh.

## Ý tưởng của anh
Anh tạo 1 project trên AI Hub, đưa yêu cầu vào đó. Tinker nhận yêu cầu, phân việc cho Emi. Emi tự mở Codex của nó lên làm, rồi báo cáo lại qua AI Hub (hộp thư), để anh theo dõi được cả 2 bên cùng làm 1 việc trong AI Hub. Anh không muốn phải tự gọi Codex nữa.
Lưu ý: KHÔNG gọi trực tiếp API như trước (đường xingchiapi.com đã bị chặn / key đã xóa). Emi làm việc bằng Codex CLI trên PC anh, báo cáo qua hộp thư AI Hub.

## Câu hỏi cho em
1. Tinker có thể gọi Codex cho Emi bằng cách nào để Emi tự chạy mà không cần anh bấm tay? Ví dụ Tinker chạy `codex exec` qua SSH trên PC anh có được không, có vướng sandbox / phê duyệt không?
2. Emi sau khi làm xong thì báo cáo qua hộp thư bằng cách nào? (Em đã biết API nội bộ POST /internal/message với token trong start.bat.)
3. Có điểm nào kẹt không (quyền ghi file, sandbox, provider, session Codex)? Nêu rõ từng điểm và cách gỡ.
4. Em đề xuất luồng cụ thể từng bước: từ lúc anh tạo project trên AI Hub đến lúc Emi báo cáo xong, ai làm gì, qua kênh nào.

Chỉ trao đổi ý tưởng, CHƯA sửa file nào, CHƯA làm gì thêm. Trả lời xong thì thôi, Tinker sẽ báo lại anh.
