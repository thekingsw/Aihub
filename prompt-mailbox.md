# Prompt cho Emi — Đổi AI Hub thành Hộp thư (mailbox) cho 3 người

## Cập nhật 17/11 ~14:58 (Anh đã xác nhận)

- **TinkerApp: Anh đã test bằng trình duyệt, ỔN.** Vòng review/sửa TinkerApp (light theme, mở app full-page, Inbox, thu hồi phiên) đã nghiệm thu xong, không cần sửa thêm.
- **AI Hub hiện chưa lên do mất key API — ĐÂY LÀ BÌNH THƯỜNG, KHÔNG CẦN SỬA.** Anh đã tự xóa key xingchiapi.com nên AI Hub không gọi được AI nữa. Đừng mất công sửa/khôi phục key. Nhiệm vụ hộp thư này sẽ bỏ hẳn API, nên vấn đề key không còn liên quan.

## 1. Bối cảnh

AI Hub hiện tại ở `E:\MyProj\ai-hub` (Python stdlib, cổng 8090, chạy bằng `start.bat`, bind IP Tailscale `100.79.204.50` qua biến `AI_HUB_HOST`).
Trước đây AI Hub là app chat gọi API xingchiapi.com (Sol 6.1 / Astra 6). **Anh đã xóa key API đó rồi, không dùng API nữa.**

Hiện AI Hub đã tích hợp SSO với TinkerApp (đăng nhập 1 lần qua TinkerApp, session + thu hồi phiên, nút "← Tinker", theme sáng). **Giữ nguyên toàn bộ phần SSO, theme sáng và nút quay về.**

## 2. Mục tiêu

Đổi AI Hub thành **hộp thư trao đổi** giữa 3 người, KHÔNG gọi API AI nào nữa:
- **Anh** (người dùng, chủ nhân).
- **Tinker** (trợ lý chính, làm việc qua SSH/VM + máy anh).
- **Emi** (là em — đội Astra/Sol, làm việc qua Codex trên PC anh).

Anh giao việc trên hộp thư, Tinker và Emi phối hợp làm, rồi báo cáo kết quả lên hộp thư. Việc chi tiết kỹ thuật sâu thì hai bên vẫn trao đổi bằng file `.md` (kiểu `For_tinker.md`), hộp thư chỉ hiện thông tin trao đổi/tóm tắt/báo cáo.

## 3. Quy tắc quan trọng nhất: gọi ai thì người đó trả lời

Mỗi tin nhắn có **người nhận rõ ràng**: `tinker`, `emi`, `anh`, hoặc `all` (cả hai / tất cả).
- Tin nhắn gửi cho **Tinker** thì chỉ Tinker trả lời, Emi không trả lời.
- Tin nhắn gửi cho **Emi** thì chỉ Emi trả lời, Tinker không trả lời.
- Tin nhắn gửi cho **cả hai (all)** thì cả hai cùng trả lời.
- Tin nhắn gửi cho **Anh** là báo cáo/kết quả, Anh đọc, không cần trả lời.

Đây là quy ước làm việc, không phải tính năng chặn kỹ thuật: giao diện phải hiển thị **rõ người nhận của từng tin** (ví dụ nhãn "→ Tinker", "→ Emi", "→ Cả hai") để không ai nhầm. Tinker và Emi khi đọc hộp thư chỉ nhận việc được gửi cho mình (hoặc cho cả hai).

## 4. Mô hình dữ liệu

Tin nhắn lưu trong file JSON (ví dụ `messages.json` trong thư mục AI Hub), mỗi tin gồm:
- `id`: chuỗi duy nhất (ví dụ timestamp + random).
- `from`: `anh` | `tinker` | `emi`.
- `to`: `tinker` | `emi` | `anh` | `all`.
- `body`: nội dung tin (text).
- `created_at`: thời gian ISO.
- `ref`: (tùy chọn) đường dẫn file `.md` đính kèm khi tin nhắn chỉ là tóm tắt, chi tiết trong file đó.

Giữ file JSON đơn giản, đọc được bằng mắt thường. Khi ghi thêm tin thì append, không ghi đè toàn bộ lịch sử. Khóa file khi ghi để tránh mất tin khi hai bên gửi cùng lúc.

## 5. API nội bộ (để Tinker và Emi gửi/đọc tin bằng máy)

Dùng token nội bộ sẵn có (`AI_HUB_INTERNAL_TOKEN`, đã có trong `start.bat`), KHÔNG đưa token xuống trình duyệt:
- `POST /internal/message` (header `Authorization: Bearer <token>`, body JSON `{from, to, body, ref?}`) → tạo tin nhắn, trả về tin đã tạo. Chỉ nhận `from` là `tinker` hoặc `emi` qua đường này (Anh gửi qua giao diện web).
- `GET /internal/messages?since=<id hoặc thời gian>&to=<tinker|emi|all>` (header Bearer token) → trả danh sách tin, có thể lọc theo người nhận để mỗi bên chỉ lấy việc của mình.
- `GET /internal/messages` không token thì 401.

Không thêm endpoint nào gọi ra ngoài internet. Không cần API key AI nào nữa.

## 6. Giao diện web (cho Anh dùng)

- Danh sách tin nhắn theo thời gian, mới nhất ở dưới. Mỗi tin hiển thị rõ: **người gửi** (màu riêng: Anh xanh dương, Tinker tím, Emi một màu khác dễ phân biệt, ví dụ xanh lá), **người nhận** (nhãn "→ Tinker" / "→ Emi" / "→ Cả hai" / "→ Anh"), thời gian, nội dung.
- Ô soạn tin: có **chọn người nhận** (Tinker / Emi / Cả hai) trước khi gửi. Mặc định người nhận là Tinker hay Emi thì để Anh chọn, không tự đoán.
- Tự cập nhật danh sách mỗi 5 giây (như trước) để Anh thấy tin mới mà không cần refresh.
- Giữ theme sáng hiện tại, giữ nút "← Tinker", giữ bố cục mobile-first, ô soạn tin bám đáy không bị bàn phím che.
- Lịch sử chat AI cũ (nếu còn) thì giữ nguyên file, không xóa; hộp thư mới bắt đầu từ danh sách trống, không trộn với lịch sử cũ.

## 7. Những gì BỎ đi

- Bỏ toàn bộ gọi API xingchiapi.com / OpenAI (hàm `call_openai`, `list_models`, `base_url`, `api_key` trong config, chọn model Sol/Astra). Không cần key nữa.
- Bỏ phần chọn model / hiển thị model trả lời trong giao diện.
- Bỏ các nút/cấu hình liên quan tới API key.

## 8. Những gì GIỮ lại

- Toàn bộ SSO: auth-check qua cookie `tinker_session`, session, thu hồi phiên khi logout/đổi mật khẩu, giới hạn phiên theo hạn phiên trung tâm, kiểm tra module bật.
- Bind `AI_HUB_HOST` (mặc định Tailscale, đang là `100.79.204.50` trong `start.bat`), cổng 8090, Python stdlib, không thêm thư viện ngoài.
- Theme sáng, nút "← Tinker", giao diện tiếng Việt.
- Không động vào thư mục nào ngoài `E:\MyProj\ai-hub`.

## 9. Kiểm thử trước khi báo xong

- Chạy `python -m py_compile ai_hub.py` phải PASS.
- Khởi động AI Hub, mở bằng trình duyệt: đăng nhập qua TinkerApp được, hộp thư hiện danh sách trống, gửi thử 1 tin cho Tinker, 1 tin cho Emi, 1 tin cho Cả hai — cả 3 tin đều hiện đúng nhãn người nhận và màu người gửi.
- Gọi thử `POST /internal/message` bằng token (from=tinker, to=emi) rồi `GET /internal/messages?to=emi` phải thấy tin đó; gọi không token phải 401.
- Kiểm tra ở 360px và 1440px không tràn ngang, ô soạn tin không bị che.
- Ghi kết quả test vào cuối file này hoặc file báo cáo riêng, ghi rõ cái nào đã kiểm và cái nào chưa.

## 10. Cách giao tiếp với Tinker

Tinker là người kiểm tra và deploy. Sau khi sửa xong, ghi tóm tắt vào file này (mục 11 bên dưới): đã đổi những file nào, đã test những gì, còn thiếu gì. Chi tiết kỹ thuật sâu thì viết vào file `.md` riêng và dẫn link/tên file trong tin nhắn hộp thư. Không gửi token/key vào tin nhắn hay file báo cáo.


## 11. Báo cáo triển khai local — Emi, 17/11/2026

- Đã đổi `index.html` thành giao diện hộp thư sáng, mobile-first, có nút “← Tinker”, chọn Tinker/Emi/Cả hai, nhãn người gửi/người nhận và tự cập nhật 5 giây.
- Thêm `mailbox_store.py` và `messages.json` riêng. Tin mới có id duy nhất, `from/to/body/created_at` và `ref` tùy chọn. Ghi dùng lock liên tiến trình và thay file nguyên tử để tránh mất tin; `history.json` và `config.json` được giữ nguyên.
- `ai_hub.py`: API nội bộ `POST /internal/message`, `GET /internal/messages`; token Bearer được so sánh an toàn. API trình duyệt `GET /api/messages`, `POST /api/message` chỉ gửi thay Anh. Route chat/key/clear/models/history trả 410 và không gọi API AI. Giữ nguyên định nghĩa hàm AI cũ, SSO, logout và revocation pull.
- Mặc định host đổi sang `100.79.204.50` nếu chưa đặt `AI_HUB_HOST`; `start.bat` hiện có giá trị này.

### Kiểm thử đã chạy

- `py -m py_compile ai_hub.py mailbox_store.py`: PASS.
- 3 unit checks: append/filter/since/ref; 60 lần ghi đồng thời không mất tin; input không hợp lệ không tạo tin: PASS.
- Local HTTP: nội bộ không token 401; POST hợp lệ 201; GET lọc người nhận: PASS. Tin thử đã được dọn khỏi `messages.json`.
- `history.json`: 642318 byte, SHA-256 `ae8a672254eab02c1fba91dead4d16572e249386302b497122620decc62e7633`, giữ nguyên.
- `config.json`: 255 byte, SHA-256 `76c12a4bfbad3337ae78aab43692aae61a582a33b5fb2acf87ad82eb93c310af`, giữ nguyên.
- JavaScript inline được kiểm tra cú pháp bằng Node: PASS.

### Chưa kiểm thử / bàn giao Tinker

- Chưa mở app bằng trình duyệt đã đăng nhập TinkerApp; chưa chụp kiểm tra 360px/1440px và bàn phím mobile.
- Chưa kiểm thử kết nối live qua nginx, login SSO thật, start.bat/process đang chạy hoặc tích hợp bot Tinker/Emi.
- Tinker cần review 3 file code (`ai_hub.py`, `mailbox_store.py`, `index.html`) và `messages.json` mới, chạy smoke test qua SSO trên máy thật, kiểm tra 360/1440px, sau đó restart AI Hub trên PC. Không cần sửa `history.json`/`config.json`; không cần API key.

## 12. Kiểm chứng + deploy — Tinker, 17/11/2026 ~16:00

- Review 3 file (`ai_hub.py`, `mailbox_store.py`, `index.html`): không có URL lạ, không gọi ra ngoài (chỉ SSO về TinkerApp); route AI cũ trả 410 trước khi tới handler cũ nên `history.json`/`config.json` không còn đường ghi; token nội bộ so bằng `compare_digest` (riêng `/internal/logout` vẫn dùng `!=` như code SSO-10 cũ — ghi nhận, không chặn).
- Hash `history.json`/`config.json` khớp đúng bản Emi ghi (ae8a67… / 76c12a…), kích thước không đổi.
- Smoke test API nội bộ (chạy trên PC, token đọc từ `start.bat`, không in ra): không token 401; POST tinker→emi 201; GET lọc `to=emi` thấy tin, `to=tinker` không thấy; người nhận sai 400; POST `/api/chat` 410; `/api/status` trả `mode: mailbox`. Tin thử đã dọn, `messages.json` về `[]`.
- Restart AI Hub bằng WMI (pid 23984, nghe `100.79.204.50:8090`); qua nginx trả 401 khi chưa đăng nhập (đúng), trực tiếp qua Tailscale `/api/status` trả `mode: mailbox`.
- Chưa kiểm bằng trình duyệt đăng nhập thật (cần anh bấm tay): mở hộp thư, gửi tin cho Tinker/Emi/Cả hai, xem 360/1440px.

## 13. Sửa lỗi đăng nhập AI Hub — Tinker, 17/11/2026 ~16:50 (bản vá TLS)

- **Triệu chứng:** Anh đăng nhập TinkerApp OK, bấm ô AI Hub vẫn bật ra trang "Đăng nhập AI Hub / Mở TinkerApp để lấy mã". Nginx + cookie đều đúng; AI Hub chạy tốt (`/api/status` → mailbox, `/api/chat` → 410).
- **Nguyên nhân (đã xác minh):** AI Hub gọi ngược TinkerApp qua `https://ubuntudev.tail8afec3.ts.net/internal/auth-check` bằng Python `urllib`. Cert Tailscale (Let's Encrypt, chuỗi ECDSA mới YE2 → Root YE → X2 → X1, còn hạn tới 13/02/2027, fingerprint khớp) bị OpenSSL 3.0 trên PC từ chối với lỗi "certificate has expired" khi dùng bộ CA mặc định (certifi) — do OpenSSL chọn đường cross-sign DST Root CA X3 đã hết hạn. Trình duyệt/.NET vẫn tin cert nên anh mở TinkerApp bình thường; chỉ Python của AI Hub bị. Hệ quả: `auth-check` luôn lỗi → AI Hub tưởng anh chưa đăng nhập → hiện trang đăng nhập. Kéo thu hồi phiên (revocation pull) cũng lỗi thầm lặng tương tự.
- **Cách sửa (KHÔNG tắt xác minh TLS):** Thêm file `isrg_roots.pem` (chỉ 2 gốc ISRG Root X1 + X2, hạn 2035/2040) vào `E:\MyProj\ai-hub`; `ai_hub.py` dùng `ssl.create_default_context(cafile=isrg_roots.pem)` cho cả `_sso_request` (auth-check + đổi mã SSO) và revocation pull. Kiểm chứng: chỉ dùng 2 gốc ISRG thì VERIFY OK; dùng certifi mặc định thì vẫn lỗi. Xác minh TLS vẫn bật, chỉ thu hẹp tập gốc tin cậy về đúng CA cấp cert cho TinkerApp.
- **File đổi:** `ai_hub.py` (thêm `import ssl`, hằng `SSO_CA_BUNDLE`, hàm `_sso_ssl_context()`, 2 chỗ dùng `HTTPSHandler(context=...)`), thêm mới `isrg_roots.pem`. Không động `history.json`/`config.json`/`messages.json`.
- **Kiểm thử:** `py_compile` PASS; gọi `_sso_request` với cookie sai → `ValueError: SSO rejected` (bắt tay TLS thành công, server trả 401) thay vì `SSLError` như trước. Restart AI Hub bằng WMI (python pid 27268, nghe `100.79.204.50:8090`); `/api/status` → `mode: mailbox`; POST `/api/chat` → 410.
- **Còn cần anh test bằng trình duyệt:** đăng nhập TinkerApp rồi bấm ô AI Hub — phải vào thẳng hộp thư (không còn trang đăng nhập). Lưu ý bảo trì: gốc ISRG bền tới 2035/2040 nên không lo hết hạn khi Tailscale đổi cert lá (13/02/2027).

## 14. Canh hộp thư cho Emi — Tinker, 17/11/2026 ~17:11

- **Vấn đề anh chỉ ra:** chỉ Tinker được canh hộp thư, Emi thì không — anh gửi tin cho Emi sẽ không ai trả lời vì Emi không tự chạy nền được.
- **Cách xử lý:** Tinker mở rộng lịch canh hộp thư (cron `ai-hub-mailbox-watch`, mỗi 5 phút) sang cả tin gửi cho Emi. Khi có tin cho Emi, Tinker ghi vào file mới `E:\MyProj\ai-hub\mailbox-emi.md` (mỗi tin gồm giờ + người gửi + toàn văn) và báo anh trong chat chính. Khi anh gọi Emi qua Codex, Emi đọc file này để biết tin mới và trả lời vào hộp thư qua `POST /internal/message` (`from: emi`). Đã báo Emi qua hộp thư và tạo sẵn file `mailbox-emi.md`.
- **Giới hạn cần anh biết:** Emi vẫn cần anh gọi qua Codex mới đọc và trả lời được; Tinker không tự gọi Codex thay anh khi chưa được anh cho phép.
