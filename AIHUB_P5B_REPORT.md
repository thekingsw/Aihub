# AI Hub P5B — báo cáo deploy (Markdown trên /hub)

Ngày: 2026-11-25. Người deploy: Tinker. Patch: `aihub-p5b-markdown.patch` (Claude viết).

## Nội dung patch
- `hub.html`: thêm bộ dựng Markdown an toàn (DOM/textContent, không innerHTML) — tiêu đề, đậm/nghiêng/gạch, code + khối code, danh sách lồng nhau + `- [x]`, trích dẫn, bảng, đường kẻ, liên kết http(s) và tệp của hub; HTML thô hiện nguyên chữ, ảnh từ xa thành liên kết (không tự tải), `javascript:` không bấm được. Tin nhắn, mô tả việc, ghi chú lịch sử việc hiện dạng Markdown.
- `hub.html`: tệp `.txt/.md/.log/.json/.csv` có nút **👁 Xem**; tệp `.md` xem được dạng đã định dạng hoặc văn bản gốc.
- `test_hub_md.js` (mới): test bộ dựng Markdown bằng Node + jsdom.
- `AIHUB_V1_API.md`: 2 đoạn tài liệu cho tính năng trên.

## Kiểm chứng
- Patch áp sạch trên base P5 (cả thư mục nguồn lẫn bản trên PC); 3 file khớp SHA-256 sau khi áp.
- Test Python: 89/89 PASS (không đổi so với P5).
- Test JS: `node test_hub_md.js` (jsdom) — 26/26 PASS, gồm các test an toàn (HTML thô giữ nguyên chữ, chỉ liên kết http(s), ảnh từ xa không tải, không chậm bất thường với đầu vào độc).
- Deploy lên PC (E:\MyProj\ai-hub): 3 file, SHA-256 khớp. Khởi động lại server qua WMI: pid 7424 → 30484, root 401, /hub 401 (cần đăng nhập), `/api/v1/me` đúng, T-0002 vẫn `done`. Worker cũ (21332) và runner (36016) không động vào.
- Chưa kiểm chứng: nhìn thật bằng trình duyệt với phiên đăng nhập của anh (anh tự mở /hub xem tin dạng Markdown + nút 👁 Xem).
