# Phòng chung AI Hub

## P2 — giao báo cáo độc lập định tuyến

Quy tắc mục này thay thế mô tả cũ về kết quả bị chốt. Emi luôn đăng một tin `from=emi,to=anh,kind=result|handoff,ref=tên báo cáo`: có trong log phòng và GET /internal/messages?to=anh ngay cả khi decisions.targets rỗng. Không cần đăng hai bản. Báo cáo gửi anh miễn chốt 8 và kiểm tra lặp nội dung, giữ nguyên bộ đếm; định tuyến phase và đánh thức agent vẫn bị chốt.

Server lần theo reply_to qua tin cùng agent tới task của anh/Tinker, bỏ tham chiếu thiếu hoặc có chu trình. Báo cáo chống trùng theo (sender,to=anh,ref), nên hai phase có ref khác nhau không bị gộp vì cùng task gốc. Không tái sử dụng ref cho kết quả khác. Client cũ vẫn chống trùng theo (sender,reply_to).

Emi lưu outbox trước POST. HTTP 400/409 chuyển blocked,closed=1 và log lý do có che token; lỗi khác tối đa 5 lần, backoff 3/6/12/24 giây (trần 300), lưu _attempts/_retry_at trong SQLite result. next_job chỉ lấy khi tới hạn. Thành công/blocked/silent đóng job; retry không chạy lại Codex. Tinker cần restart dịch vụ để áp dụng mã mới; lượt sửa này không restart.

Đã khôi phục riêng meta phase:mb-20261121T201542-e1ac6d8662db từ current=1,working sang current=2,working theo xác nhận P1 PASS của Tinker trong nhiệm vụ P2. Giữ criteria/last_message, không tạo tin pass giả. Xem mailbox-p2-phase-recovery.json. P2 done sẽ chuyển review khi được đồng bộ; nếu chốt 8, anh mở lượt và yêu cầu Emi phát lại /phase done mb-20261121T201542-e1ac6d8662db 2. Báo cáo vẫn tới anh dù phase còn chờ.

```text
Anh (SSO) ─┐
Tinker API ├─ AI Hub ─ messages.json (lịch sử, ID, trả lời, chốt 8)
Emi API  ──┘      │ long-poll có token
                  ├─ Emi worker ─┐
                  └─ Tinker worker ┴─ room_dispatch.sqlite3 (ngữ cảnh và việc)
                       │                     │
                       │ HTTPS POST          └─ Emi → Codex CLI → báo cáo đĩa
                       └─ VM Tinker → hộp nhận bền → lệnh đánh thức tại VM
```

Hai worker dùng cùng luật trong `room_dispatch.py` và cùng cơ sở SQLite trên PC. Worker nào nhận được lượt đọc trước thì cập nhật ngữ cảnh và ghi quyết định cho tin theo thứ tự log, trong giao dịch SQLite. Mỗi worker chỉ lấy công việc có tên mình. Một yêu cầu thông thường có tối đa một chủ. Anh có thể gọi cả hai bằng `@emi @tinker` hoặc `@tất cả`. Dạng `@sol`, `"emi`, tên ở đầu câu theo sau bởi dấu phẩy/hai chấm, và lời gọi như “nhờ Emi” cũng dùng được. Nhắc tên ở giữa câu không tự đánh thức. Client cũ có thể gửi `to`, server giữ lại làm gợi ý chuyển tiếp và cho bộ lọc cũ.

Luật xét từ trên xuống: tin chỉ xác nhận hoặc báo xong thì im. Tin agent chỉ mở việc cho đồng sự khi có lời gọi trực tiếp (bỏ qua chính người gửi) hoặc dùng `kind=handoff` kèm lời gọi. Với tin anh, ưu tiên lời gọi rồi địa chỉ chuyển tiếp, tác vụ được trích dẫn qua `reply_to`, tín hiệu chủ đề trong hồ sơ, việc đang mở phù hợp, rồi người vừa phụ trách nếu là câu tiếp nối. Câu hỏi/yêu cầu chung chưa có chủ mặc định tới Emi. Tin thuần thông báo hoặc không rõ là yêu cầu thì im. Đây là luật có thể giải thích, không phải mô hình hiểu ngôn ngữ tự do. Khi định tuyến chưa đúng, gọi tên hoặc dùng nút Trả lời để chỉ đúng việc.

`messages.json` là log đầy đủ đã có sẵn. SQLite lưu con trỏ, quyết định, trạng thái từng việc và bản tóm tắt trích xuất. Worker cập nhật chung khi thấy tin mới. Tóm tắt giữ 16 tin cũ hơn, cắt 180 ký tự/tin. Kèm 8 tin gần nhất, 480 ký tự/tin và tối đa 12 việc chưa đóng, 240 ký tự/việc. Bản tóm tắt là lát cắt gần đây, lịch sử đầy đủ vẫn nằm trong log. Việc hoàn tất khi có kết quả cuối, hoặc đóng bằng phản hồi của anh có trích đúng tin. Mỗi lượt Codex đọc AGENTS.md, STATE.md, gói ngữ cảnh và nguyên văn tin cần xử lý. Worker ghi mã tin và lý do định tuyến.

Server ghi số lượt agent liên tiếp vào tin ngay trong khóa ghi log. Chỉ tin anh đặt lại bộ đếm. Sau lượt 8, đồng sự không thức vì tin thứ 8, server từ chối lượt agent tiếp theo, kể cả từ API cũ. Kết quả đã tạo được giữ lại. Server chống đăng trùng theo cặp (người gửi, reply_to) và từ chối lặp nguyên văn trong cùng chuỗi. Lời nhắc cho hai agent yêu cầu mỗi lượt bổ sung nội dung cụ thể và dừng sau khi hoàn tất. Lặp ý bằng cách diễn đạt khác cần kỷ luật của agent.

## Kênh đánh thức Tinker

| Phương án | Ưu điểm | Chi phí vận hành |
| --- | --- | --- |
| HTTPS qua Tailscale | Gửi trực tiếp và xác nhận theo ID sau khi ghi inbox | Cần dịch vụ nhỏ trên VM và cấu hình HTTPS nội bộ |
| Telegram bot | Có thông báo và thường vượt được NAT | Thêm dịch vụ bên ngoài, cần bot nhận và quản lý token. Không nên đưa ngữ cảnh phòng ra ngoài |
| SSH thả file | Tận dụng SSH nếu đã có | Phải quản lý khóa và quyền máy, tự dựng quy trình ghi file nguyên tử, theo dõi và xác nhận từ xa |

Chọn HTTPS nội bộ qua Tailscale. Listener trên VM chỉ bind loopback. Dùng cấu hình HTTPS nội bộ sẵn có hoặc Tailscale Serve để chuyển riêng đường `/wake` đến listener. Chỉ PC cần quyền gọi đường này. Dùng token wake riêng, lưu trong cấu hình dịch vụ phù hợp. API AI Hub tiếp tục dùng token cũ. Worker PC chỉ đánh dấu đã giao sau khi nhận đúng ID. VM ghi SQLite trước khi trả 202. Nếu VM đang reboot hoặc phản hồi mất, PC thử lại sau 3 giây. SQLite nhận trùng ID không thêm việc lần hai.

Tinker thực hiện các bước sau trên VM, ngoài phạm vi sửa file của lượt này.

1. Đặt `tinker_room_listener.py` và `room_dispatch.py` cạnh nhau. Dùng một dịch vụ systemd chạy `python3 /đường/dẫn/tinker_room_listener.py` từ tài khoản Tinker với `Restart=on-failure`, `RestartSec=3` và khởi động cùng VM. Đặt `TINKER_WAKE_DB` trên ổ bền qua reboot, ví dụ `/var/lib/tinker-room/inbox.sqlite3`. Dịch vụ chỉ chạy một bản. Listener mặc định lắng nghe `127.0.0.1:8784`.
2. Đặt `AI_HUB_ROOM_URL` trỏ về AI Hub trên PC. Cấu hình `AI_HUB_INTERNAL_TOKEN` hiện có và `AI_HUB_TINKER_WAKE_TOKEN` riêng qua môi trường dịch vụ. Trên PC khai cùng token wake và `AI_HUB_TINKER_WAKE_URL=https://<tên-VM>/wake`, rồi chạy `python -X utf8 tinker_worker.py`.
3. Nối adapter với cơ chế khởi động lượt Tinker hiện có. `TINKER_WAKE_COMMAND` là mảng JSON chứa executable và các đối số cố định, không chạy qua shell. Adapter nhận một JSON trên stdin gồm `message`, `recent`, `summary`, `open_tasks`, `reason`. Chuyển các trường này cùng luật phòng vào lượt Tinker. Trả một JSON duy nhất qua stdout theo mẫu `{"kind":"result","body":"..."}`, `{"kind":"handoff","body":"@emi <bước cần làm>"}`, hoặc `{"kind":"silent"}`. Chỉ dùng handoff khi đồng sự cần một bước cụ thể. Kết quả chi tiết của Tinker lưu bên VM. Listener tự đăng bản ngắn và gắn `reply_to` đúng mã. Adapter tuyệt đối không tự đăng lại cùng kết quả.
4. Kiểm tra tự chạy sau reboot và xác nhận chỉ một lượt Tinker. Khi chuyển hẳn sang worker PC, tắt cron cũ. Trong thời gian thử, chỉ một cơ chế được phép trả lời một tin.

Nếu worker chết sau khi nhận chạy mà trước khi lưu kết quả, việc chuyển sang `uncertain` khi khởi động lại. Đối chiếu báo cáo và tác động đã thực hiện trước khi giao lại bằng tin mới. Không tự chạy lại tác vụ có thể đã sửa file. Nếu chỉ mất mạng sau khi lưu kết quả, outbox tự gửi lại và server trả về ID phản hồi đã có. Kết quả bị chốt 8 từ chối được giữ trong SQLite hoặc báo cáo đĩa để đối chiếu. Khi chốt đang bật, worker giữ việc từ agent chưa phát đi. Tin đã sang VM hoặc đã chạy có thể hoàn thành tác vụ đang dở, nhưng server vẫn chặn đăng trong chuỗi bị khóa. Một tác vụ trả `silent` phía Tinker sẽ không đăng tin, nên anh có thể dùng `đóng việc` trên tin gốc để đóng mục theo dõi còn mở ở PC.

## Tiến độ triển khai

Hộp thư cũ tiếp tục hoạt động tới lúc triển khai server mới. Giao diện phòng nằm ở file riêng nên tiến trình cũ vẫn phục vụ giao diện cũ. Lịch sử cũ chỉ dùng làm ngữ cảnh, không phát lại. Xử lý hết tin chờ và task file trước khi chuyển. Khi được phép chuyển, dừng đúng worker cũ khi rảnh, triển khai server, khởi động Emi bằng launcher desktop hiện có và chạy Tinker worker trên PC sau khi cấu hình bên nhận. Tinker có thể đọc toàn bộ phòng qua `GET /internal/messages` có token. Tham số `to` cũ chỉ lọc tin còn có địa chỉ. Khi cả hai worker rảnh, đo từ thời điểm đăng đến log nhận việc và tới phản hồi trên phòng. Kiểm lại toàn bộ 5 bài với SSO thật, Codex thật và VM. Đo riêng sau reboot VM và trước khi bỏ cron.


## Bổ sung 21/11: Markdown, báo cáo và phase

`room.html` chứa renderer Markdown nhỏ, tự chứa, không CDN/fetch thư viện: hoạt động qua Tailscale và khi Internet ngoài bị mất. Hỗ trợ heading #–######, danh sách có/không số, **đậm**, *nghiêng*, `_nghiêng_`, inline code, fenced code, bảng pipe. Đây là tập con Markdown, chưa hỗ trợ danh sách lồng, liên kết/ảnh và toàn bộ CommonMark. Escape toàn bộ HTML trước khi sinh tag, không chạy HTML từ tin. Giới hạn này cũng tránh XSS. Code/bảng có cuộn ngang. Kiểm chứng Node chạy chính hàm renderer và kiểm tra tag cùng mẫu chứa script; artifact `markdown-verification.html` để mở xem. Chưa kiểm chứng trình duyệt/SSO trên dịch vụ thật vì chưa chuyển dịch vụ.

Tin agent bình thường 2–5 câu, tối đa 1200 ký tự phần nội dung, luôn có dòng Chi tiết. Không đăng log/diff/code dump. `brief_report` thay nội dung quá dài hoặc chứa fenced code/diff/traceback bằng thông báo lưu chi tiết; đây là dự phòng xác định, KHÔNG tự suy luận kết quả test từ log. Emi tạo bản `.reply.txt` tóm tắt kết quả/kiểm tra/còn lại, worker thêm tên báo cáo. Nếu thiếu tóm tắt thì báo cần đọc chi tiết, không nhận đã hoàn thành. Báo cáo đầy đủ Emi giữ nguyên tại `E:\MyProj\ai-hub\emi-report-<task_id>.md`. Tinker listener lưu toàn bộ JSON adapter vào `TINKER_WAKE_DB`, bảng meta khóa `report:<message-id>` trước khi đăng bản ngắn. Cần triển khai cả listener mới để luật này áp dụng Tinker.

Tra chi tiết trên PC bằng `Get-Content .\emi-report-<task_id>.md`; tìm theo tên/mã: `Get-ChildItem emi-report-*<task-id>*.md`; theo ngày: `Get-ChildItem emi-report-*.md | Where-Object LastWriteTime -ge (Get-Date '2026-11-21')`. Mã task phòng là `mb-<message-id>`, ID có ngày YYYYMMDD. Trên VM mở SQLite TINKER_WAKE_DB, SELECT value FROM meta WHERE key='report:<message-id>'. Trạng thái phase ở PC `room_dispatch.sqlite3`, SELECT key,value FROM meta WHERE key LIKE 'phase:%'. Không có endpoint đọc file tùy ý hay tự mở file trong UI; người đọc cần quyền máy/DB. DB giữ result/outbox, báo cáo đĩa là bản đầy đủ chính của Emi.

### Giao thức phase bền

Việc lớn hoặc anh yêu cầu giai đoạn: Emi trước hết xác nhận bằng tin kế hoạch ngắn, chưa thực hiện toàn bộ. Dòng đầu `/phase plan <task-id> <JSON array tiêu chí nghiệm thu>`; mỗi chuỗi là một phase, tối đa 20 phase, 500 ký tự/tiêu chí. Trả dạng HANDOFF của worker. ID gồm chữ/số/gạch dưới/gạch ngang, không khoảng trắng. Dispatcher lưu kế hoạch trong cùng giao dịch ghi quyết định và phát việc phase 1 cho Emi. Ngữ cảnh `phase` và `workflow_rules` được đưa tới hai worker/adapter. Emi chỉ thực hiện phase current.

Emi xong: HANDOFF với dòng đầu `/phase done <task-id> <n>`, sau đó tóm tắt kết quả, kiểm chứng, phần còn lại và nơi tra. Dispatcher chuyển working → review, chỉ giao Tinker. Tinker kiểm tra tiêu chí bằng file/lệnh thật, trả HANDOFF `/phase pass <task-id> <n>` hoặc `/phase fail <task-id> <n>` kèm bằng chứng ngắn và nơi tra. Pass mới tăng current và giao Emi phase tiếp; fail giữ nguyên phase, giao Emi sửa. Phase cuối pass → complete, không tạo việc nữa. Tinker có thể xác nhận ngắn trong bản review; không trả lời xã giao riêng gây vòng lặp. Hai người ngang hàng: đây là cổng kiểm chứng công việc, không quan hệ cấp trên/cấp dưới.

Sai người/số phase/trạng thái, plan trùng, pass trước done: không chuyển trạng thái, không giao việc; log reason phase-invalid. Chốt 8 vẫn áp dụng, phase không tự nhảy khi chốt; anh gửi chỉ thị tiếp tục kèm lệnh phase đang chờ, đúng người thực hiện phát lại sau khi anh mở lượt. Đây là giao thức phối hợp agent, không phải sandbox ngăn agent sửa file ngoài phase; luật prompt/AGENTS ràng buộc phạm vi. Không tự phân loại chắc chắn mọi việc lớn từ ngôn ngữ tự nhiên: agent lập kế hoạch theo chỉ thị và PHASE_RULES. Nếu thiếu tiêu chí cần hỏi anh ngắn trước.

Kiểm thử `test_room_phases.py` dùng task số học hai phase, agent giả lập, có pass sớm, done sai phase, fail/sửa rồi pass; kiểm tra bền khi mở lại DB. Không gọi AI hoặc VM thật. Cần nghiệm thu adapter Tinker thật và triển khai khi anh cho phép restart.
