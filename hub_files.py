"""AI Hub P5: tệp đính kèm. Nội dung nằm trên đĩa (hub-files/<id>), thông tin nằm trong bảng `files`.

- id là 128 bit ngẫu nhiên (32 ký tự hex): biết id mới tải được, và mọi lần tải đều phải xác thực.
- Giới hạn: AI_HUB_MAX_UPLOAD_MB (mặc định 25) cho mỗi tệp, AI_HUB_FILES_QUOTA_MB (mặc định 2048) cho cả kho.
- Chỉ một số loại tệp (ảnh PNG/JPEG/GIF/WebP đúng định dạng, văn bản thuần) được mở ngay trong trình duyệt;
  mọi thứ khác (HTML, SVG, PDF...) luôn bị ép tải về, để tệp tải lên không chạy được script trên trang hub.
"""
import hashlib
import os
import secrets
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hub_db import HubError, NotFound

BASE = os.path.dirname(os.path.abspath(__file__))
CHUNK = 64 * 1024
INLINE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"})
OCTET = "application/octet-stream"
TEXT_EXT = {".txt", ".md", ".log", ".json", ".csv"}


class LengthRequired(HubError):
    status, code = 411, "LENGTH_REQUIRED"


class TooLarge(HubError):
    status, code = 413, "PAYLOAD_TOO_LARGE"


class QuotaExceeded(HubError):
    status, code = 507, "QUOTA_EXCEEDED"


class UploadInterrupted(HubError):
    status, code = 408, "UPLOAD_INCOMPLETE"


def _mb(name, default):
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


def limits():
    """(tối đa mỗi tệp, tổng dung lượng kho) tính bằng byte."""
    return int(_mb("AI_HUB_MAX_UPLOAD_MB", 25.0) * 1024 * 1024), int(_mb("AI_HUB_FILES_QUOTA_MB", 2048.0) * 1024 * 1024)


def clean_name(raw):
    """Chỉ giữ tên tệp: bỏ đường dẫn, ký tự điều khiển, dấu chấm đầu; tối đa 120 ký tự (giữ phần đuôi)."""
    name = str(raw or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip().lstrip(".").strip()
    if len(name) > 120:
        stem, ext = os.path.splitext(name)
        name = stem[: max(1, 120 - len(ext))] + ext[:20]
    return name or "tep"


def detect_mime(name, head):
    """Loại phát hành khi tải về, quyết định lúc upload từ đuôi tệp và vài byte đầu (không tin client)."""
    ext = os.path.splitext(name)[1].lower()
    if ext == ".png" and head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if ext in (".jpg", ".jpeg") and head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if ext == ".gif" and head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if ext == ".webp" and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if ext in TEXT_EXT:
        return "text/plain"
    return OCTET


class FileStore:
    def __init__(self, hub, root):
        self.hub = hub
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 3600
        for stale in self.root.glob(".tmp-*"):  # tệp dở dang do upload bị ngắt từ lần chạy trước
            try:
                if stale.stat().st_mtime < cutoff:
                    stale.unlink()
            except OSError:
                pass

    def save(self, uploader, name, stream, length):
        max_bytes, quota = limits()
        if length is None:
            raise LengthRequired("Cần header Content-Length (không hỗ trợ chunked).")
        if length <= 0:
            raise HubError("Tệp rỗng.")
        if length > max_bytes:
            raise TooLarge("Tệp quá lớn: tối đa %d MB." % (max_bytes // 1048576 or 1))
        if self.hub.files_total_size() + length > quota:
            raise QuotaExceeded("Kho tệp đã đầy. Hãy nhờ anh xoá bớt tệp cũ.")
        name = clean_name(name)
        file_id = secrets.token_hex(16)
        tmp, final = self.root / (".tmp-" + file_id), self.root / file_id
        digest, remaining, head = hashlib.sha256(), length, b""
        try:
            with open(tmp, "wb") as out:
                while remaining:
                    try:
                        chunk = stream.read(min(CHUNK, remaining))
                    except OSError:
                        chunk = b""
                    if not chunk:
                        raise UploadInterrupted("Tải lên bị ngắt giữa chừng; tệp không được lưu.")
                    if len(head) < 16:
                        head += chunk[: 16 - len(head)]
                    digest.update(chunk)
                    out.write(chunk)
                    remaining -= len(chunk)
            os.replace(tmp, final)
            row = self.hub.add_file(file_id, name, detect_mime(name, head), length, digest.hexdigest(), uploader)
        except BaseException:
            for leftover in (tmp, final):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            raise
        return self.hub.file_public(row)

    def open(self, file_id):
        row = self.hub.file_row(file_id)
        path = self.root / file_id
        if row is None or not path.is_file():
            raise NotFound("Không có tệp này.")
        return row, path

    def delete(self, file_id):
        row, path = self.open(file_id)
        self.hub.delete_file_row(file_id)
        try:
            path.unlink()
        except OSError:
            pass
        return row


def _purge(self, older_than_days, dry_run=True):
    """Xoá tệp đã upload quá N ngày (mặc định chỉ xem trước). Tin đã gắn tệp được đánh dấu "đã xoá"."""
    if not isinstance(older_than_days, int) or isinstance(older_than_days, bool) or not 1 <= older_than_days <= 3650:
        raise HubError("older_than_days phải là số nguyên từ 1 đến 3650.")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(timespec="seconds")
    victims = self.hub.files_older_than(cutoff)
    if not dry_run:
        for victim in victims:
            try:
                self.delete(victim["id"])
            except NotFound:
                pass
    return {"dry_run": bool(dry_run), "count": len(victims), "bytes": sum(v["size"] for v in victims),
            "files": victims[:50]}


FileStore.purge = _purge


def files_dir():
    return os.environ.get("AI_HUB_FILES_DIR") or os.path.join(BASE, "hub-files")


def store_for(hub):
    """Mỗi HubDB có đúng một FileStore (thư mục lấy từ AI_HUB_FILES_DIR lúc tạo)."""
    store = getattr(hub, "_file_store", None)
    if store is None:
        with hub._lock:
            store = getattr(hub, "_file_store", None)
            if store is None:
                store = hub._file_store = FileStore(hub, files_dir())
    return store
