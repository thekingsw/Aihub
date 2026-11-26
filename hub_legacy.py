"""AI Hub P6: nhập lịch sử từ hộp thư cũ (messages.json của Tinker/Emi) vào hệ v1 để xem trong /hub.

Tin nhập vào mang cờ `legacy`: chỉ để xem lịch sử. Chúng KHÔNG BAO GIỜ được giao cho agent nào (đọc tin
của agent luôn bỏ qua tin legacy), không tính vào hạn 8 lượt, và nằm trong luồng riêng `legacy`. Nhờ vậy
một runner mới không thể vô tình chạy lại các việc cũ.

Nhập lặp lại an toàn: mỗi tin cũ ghi dấu `legacy:<id cũ>`; chạy lại chỉ thêm tin mới.
"""
import json
import re
from datetime import datetime, timezone

from hub_db import LEGACY_THREAD, MAX_BODY, HubError

DEFAULT_MAP = {"tinker": "muse"}   # Tinker và Muse là một; Emi (Sol) giữ nguyên tên cũ trừ khi anh đổi
LEGACY_KINDS = {"result", "handoff", "ack"}
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def parse_map(pairs):
    """['tinker=muse', 'emi=codex'] -> {'tinker': 'muse', 'emi': 'codex'}"""
    mapping = dict(DEFAULT_MAP)
    for pair in pairs or []:
        old, sep, new = pair.partition("=")
        if not sep or not ID_RE.match(old) or not ID_RE.match(new):
            raise HubError("Ánh xạ phải có dạng tên_cũ=tên_mới (chữ thường, số, _ -): %r." % pair)
        mapping[old] = new
    return mapping


def _iso(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")


def _row(raw, mapping):
    """Trả về (tin chuẩn hoá) hoặc raise ValueError nếu hàng cũ không dùng được."""
    if not isinstance(raw, dict):
        raise ValueError("không phải object")
    legacy_id, sender, body = raw.get("id"), raw.get("from"), raw.get("body")
    if not isinstance(legacy_id, str) or not legacy_id or len(legacy_id) > 64:
        raise ValueError("thiếu id")
    if not isinstance(sender, str) or not ID_RE.match(sender):
        raise ValueError("người gửi không hợp lệ")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("tin rỗng")
    try:
        created = _iso(raw.get("created_at"))
    except (TypeError, ValueError):
        raise ValueError("created_at không hợp lệ") from None
    to = raw.get("to") or "all"
    if not isinstance(to, str) or not ID_RE.match(to):
        raise ValueError("người nhận không hợp lệ")
    ref = raw.get("ref")
    text = body.strip()
    if isinstance(ref, str) and ref.strip():
        text += "\n\n📄 Tài liệu: " + ref.strip()
    kind = raw.get("kind")
    return {"legacy_id": legacy_id, "sender": mapping.get(sender, sender), "to": mapping.get(to, to),
            "kind": kind if kind in LEGACY_KINDS else "message", "body": text[:MAX_BODY], "created_at": created,
            "reply_to": raw.get("reply_to") if isinstance(raw.get("reply_to"), str) else None}


def import_rows(hub, rows, mapping=None, dry_run=False):
    """Nhập các hàng của messages.json theo thứ tự trong tệp. Trả về thống kê."""
    mapping = DEFAULT_MAP if mapping is None else mapping
    if not isinstance(rows, list):
        raise HubError("Dữ liệu hộp thư cũ phải là danh sách tin.")
    stats = {"total": len(rows), "imported": 0, "already_imported": 0, "invalid": 0, "dry_run": bool(dry_run),
             "problems": []}
    with hub._lock:
        hub.db.execute("SAVEPOINT legacy_import")
        try:
            for index, raw in enumerate(rows):
                try:
                    msg = _row(raw, mapping)
                except ValueError as exc:
                    stats["invalid"] += 1
                    if len(stats["problems"]) < 5:
                        stats["problems"].append("hàng %d: %s" % (index + 1, exc))
                    continue
                key = "legacy:" + msg["legacy_id"]
                if hub.db.execute("SELECT 1 FROM messages WHERE request_id=? AND legacy=1", (key,)).fetchone():
                    stats["already_imported"] += 1
                    continue
                stats["imported"] += 1
                if dry_run:
                    continue
                reply_to = None
                if msg["reply_to"]:
                    parent = hub.db.execute("SELECT id FROM messages WHERE request_id=? AND legacy=1",
                                            ("legacy:" + msg["reply_to"],)).fetchone()
                    reply_to = parent["id"] if parent else None
                cur = hub.db.execute(
                    "INSERT INTO messages(created_at,sender,is_human,turn,kind,task_id,project,title,body,reply_to,"
                    "thread_id,request_id,files,legacy) VALUES(?,?,?,?,?,NULL,NULL,NULL,?,?,?,?,NULL,1)",
                    (msg["created_at"], msg["sender"], int(msg["sender"] in {"anh"}), 0, msg["kind"], msg["body"],
                     reply_to, LEGACY_THREAD, key))
                hub.db.execute("INSERT INTO msg_recipients(message_id,agent) VALUES(?,?)", (cur.lastrowid, msg["to"]))
            hub.db.execute("RELEASE legacy_import")
        except Exception:
            hub.db.execute("ROLLBACK TO legacy_import")
            hub.db.execute("RELEASE legacy_import")
            raise
    return stats


def load_file(path):
    try:
        with open(path, encoding="utf-8-sig") as stream:
            return json.load(stream)
    except (OSError, ValueError) as exc:
        raise HubError("Không đọc được hộp thư cũ %s: %s" % (path, exc)) from None
