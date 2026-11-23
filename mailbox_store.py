"""Append-only JSON mailbox for AI Hub."""
import json
import os
import tempfile
import threading
import uuid
import time
from contextlib import contextmanager
from datetime import datetime, timezone


class RoomConflict(ValueError):
    pass


class MessageStore:
    SENDERS = {"anh", "tinker", "emi"}
    RECIPIENTS = {"tinker", "emi", "anh", "all"}

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self._ensure()

    @contextmanager
    def _file_lock(self):
        """Serialize processes as well as HTTP threads using a sidecar lock."""
        with self.lock, open(self.path + ".lock", "a+b") as stream:
            if os.name == "nt":
                import msvcrt
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                deadline = time.monotonic() + 15
                while True:
                    stream.seek(0)
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.02)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def _ensure(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump([], stream)

    def read(self, since=None, recipient=None):
        if recipient is not None and recipient not in self.RECIPIENTS:
            raise ValueError("Người nhận không hợp lệ.")
        with self._file_lock(), open(self.path, encoding="utf-8") as stream:
            rows = json.load(stream)
        if not isinstance(rows, list):
            raise ValueError("Dữ liệu hộp thư không hợp lệ.")
        if since:
            index = next((i for i, row in enumerate(rows) if row.get("id") == since), None)
            if index is not None:
                rows = rows[index + 1:]
            else:
                try:
                    stamp = datetime.fromisoformat(since)
                    if stamp.tzinfo is None:
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValueError("Mốc since phải là id có trong hộp thư hoặc thời gian ISO có múi giờ.") from None
                rows = [row for row in rows if datetime.fromisoformat(row["created_at"]) > stamp]
        if recipient:
            rows = [row for row in rows if row.get("to") in {recipient, "all"}]
        return rows

    def wait(self, since, seconds):
        deadline = time.monotonic() + seconds
        with self.changed:
            while True:
                rows = self.read(since)
                remaining = deadline - time.monotonic()
                if rows or remaining <= 0:
                    return rows
                self.changed.wait(min(remaining, 1))

    def append(self, sender, recipient, body, ref=None, reply_to=None, kind=None):
        if (not isinstance(sender, str) or sender not in self.SENDERS or
                (recipient is not None and (not isinstance(recipient, str) or recipient not in self.RECIPIENTS))):
            raise ValueError("Người gửi hoặc người nhận không hợp lệ.")
        if not isinstance(body, str) or not body.strip() or len(body) > 20000:
            raise ValueError("Tin nhắn phải có từ 1 đến 20000 ký tự.")
        if ref is not None and (not isinstance(ref, str) or len(ref) > 2048):
            raise ValueError("Liên kết tài liệu không hợp lệ.")
        if reply_to is not None and (not isinstance(reply_to, str) or not reply_to or len(reply_to) > 64):
            raise ValueError("Mã tin gốc không hợp lệ.")
        if kind not in (None, "result", "handoff", "ack"):
            raise ValueError("Loại tin không hợp lệ.")
        message = {"id": datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12],
                   "from": sender, "body": body.strip()}
        if recipient is not None:
            message["to"] = recipient
        if reply_to is not None:
            message["reply_to"] = reply_to
        if kind is not None:
            message["kind"] = kind
        if ref:
            message["ref"] = ref
        with self._file_lock():
            with open(self.path, encoding="utf-8") as stream:
                rows = json.load(stream)
            if not isinstance(rows, list):
                raise ValueError("Dữ liệu hộp thư không hợp lệ; không ghi đè.")
            report = sender in ("emi", "tinker") and recipient == "anh" and kind in ("result", "handoff")
            if report:
                # Resolve self replies to the original task, or omit a stale reference.
                visited = set()
                while reply_to:
                    original = next((r for r in reversed(rows) if r.get("id") == reply_to), None)
                    if original is None or reply_to in visited:
                        reply_to = None
                        break
                    visited.add(reply_to)
                    if original.get("from") != sender:
                        break
                    reply_to = original.get("reply_to")
                message.pop("reply_to", None)
                if reply_to:
                    message["reply_to"] = reply_to
                # A task/phase has its own durable report reference. Do not conflate
                # different phase results replying to the same original task.
                previous = next((r for r in reversed(rows) if ref and r.get("from") == sender
                                 and r.get("to") == "anh" and r.get("ref") == ref), None)
                if previous:
                    return previous
            if reply_to:
                original = next((row for row in reversed(rows) if row.get("id") == reply_to), None)
                if original is None:
                    raise ValueError("Không tìm thấy tin gốc.")
                if sender != "anh":
                    if original.get("from") == sender:
                        raise ValueError("Agent không tự trả lời tin của mình.")
                    previous = next((row for row in reversed(rows)
                                     if row.get("from") == sender and row.get("reply_to") == reply_to), None)
                    if previous is not None and not report:
                        return previous
            turns = rows[-1].get("agent_turns", 0) if rows else 0
            if sender != "anh" and not report:
                if turns >= 8:
                    raise RoomConflict("Đã đủ 8 lượt agent. Chờ anh nhắn.")
                normalized = " ".join(body.casefold().split())
                for row in reversed(rows[-turns:] if turns else []):
                    if row.get("from") == sender and " ".join(row["body"].casefold().split()) == normalized:
                        raise RoomConflict("Nội dung đã đăng trong chuỗi này.")
            message["agent_turns"] = 0 if sender == "anh" else (turns if report else turns + 1)
            message["created_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            rows.append(message)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix="mailbox-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(rows, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            self.changed.notify_all()
        return message
