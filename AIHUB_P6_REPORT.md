# AI Hub P6 — báo cáo deploy (chuyển hẳn sang v1: lịch sử hộp thư cũ + tab Tệp)

Ngày: 2026-11-26. Người deploy: Tinker. Patch: `aihub-p6.patch` (Claude viết).

## Nội dung patch
- `hub_legacy.py` + `import_legacy.py` (mới): nhập lịch sử hộp thư cũ (`messages.json`) vào v1 để xem trong /hub — tin mang cờ `legacy`, nằm ở luồng riêng `legacy`, KHÔNG bao giờ giao cho agent, không tính vào hạn 8 lượt; nhập lặp lại không trùng (dấu `legacy:<id cũ>`); mặc định `tinker` → `muse`.
- `hub_db.py`: DB tự nâng cấp tại chỗ (thêm cột `legacy`); poll và nguồn cấp mặc định bỏ qua tin legacy; luồng `legacy` bị cấm dùng cho việc/luồng thật; xoá tệp thì đánh dấu "đã xoá" ở các tin đã gắn.
- `hub_files.py`: `purge` dọn tệp cũ hơn N ngày (mặc định chỉ xem trước).
- `api_v1.py`: `POST /api/v1/legacy/import`, `POST /api/v1/files/purge` (admin).
- `hub.html`: tab thứ 4 **Tệp** (dung lượng, xoá từng tệp, dọn tệp cũ có xem trước); **📜 Hộp thư cũ** trong tab Phòng (chỉ xem); nút nhập lịch sử ở tab AI; tin có tệp đã xoá hiện "đã xoá".
- `ai_hub.py`: trang `/legacy` mở giao diện cũ; đặt `AI_HUB_DEFAULT_UI=hub` thì `/` mở giao diện mới.
- `AIHUB_MIGRATION.md` (mới): các bước chuyển hẳn — không bước nào sửa/xoá hộp thư cũ.
- `test_legacy.py` (mới): 16 test.

## Kiểm chứng
- Patch áp sạch trên base P5B (cả thư mục nguồn lẫn bản trên PC); 11 file khớp SHA-256 sau khi áp.
- Test Python: 105/105 PASS (89 cũ + 16 mới). Test JS (jsdom): 26/26 PASS.
- Deploy lên PC (E:\MyProj\ai-hub): 11 file, SHA-256 khớp. Sao lưu DB trước khi restart: `hub.sqlite3.bak-p6-20261126` (114.688 byte). Khởi động lại server qua WMI: pid 30484 → 20800, root 401, /hub 401, /legacy 401, `/api/v1/me` đúng, T-0002 vẫn `done`. Worker cũ (21332) và runner (36016) không động vào.
- DB đã nâng cấp: cột `legacy` có mặt, 0 tin legacy (chưa nhập — đúng thiết kế, nhập là bước riêng).
- Chạy thử nhập (dry-run, không ghi gì): hộp thư cũ có 86 tin, cả 86 nhập được, 0 hàng lỗi, ánh xạ `tinker` → `muse`.
- Chưa làm (theo AIHUB_MIGRATION.md, chờ anh quyết từng bước): nhập thật 86 tin; tạo agent `codex` thay Emi (cần provider Codex hoạt động lại); đặt `AI_HUB_DEFAULT_UI=hub`; dừng các worker cũ.
