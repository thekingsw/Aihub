"""AI Hub v1 store: danh bạ agent (token riêng) và nhật ký tin có id tăng dần.

SQLite, stdlib thuần. Tin có id nguyên tăng dần để agent dùng làm mốc (?after=<id>).
Token chỉ được hiển thị một lần khi tạo/đổi; DB chỉ giữ SHA-256 của token.
"""
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

HUMANS = frozenset({"anh"})
KINDS = frozenset({"task", "message", "result", "review", "ack", "handoff"})
MODES = frozenset({"push", "pull"})
RESERVED_IDS = frozenset({"all", "anh", "system", "admin"})
MAX_BODY = 20000
MAX_AGENT_TURNS = 8
AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
TOKEN_PREFIX = "ah1_"


class HubError(Exception):
    status = 400
    code = "BAD_REQUEST"

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        if status:
            self.status = status
        if code:
            self.code = code


class Conflict(HubError):
    status = 409
    code = "CONFLICT"


class NotFound(HubError):
    status = 404
    code = "NOT_FOUND"


class Forbidden(HubError):
    status = 403
    code = "FORBIDDEN"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token():
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _text(value, name, limit, required=False):
    if value is None:
        if required:
            raise HubError("Thiếu trường %s." % name)
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise HubError("Trường %s phải là chuỗi tối đa %d ký tự." % (name, limit))
    return value.strip() or None


class HubDB:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self.changed = threading.Condition(self._lock)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS agents(
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                mode TEXT NOT NULL CHECK(mode IN ('push','pull')),
                role TEXT NOT NULL DEFAULT '', poll_interval INTEGER NOT NULL DEFAULT 300,
                token_hash TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, last_seen REAL);
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                sender TEXT NOT NULL, is_human INTEGER NOT NULL, turn INTEGER NOT NULL,
                kind TEXT NOT NULL, task_id TEXT, project TEXT, title TEXT,
                body TEXT NOT NULL, reply_to INTEGER, thread_id TEXT NOT NULL,
                request_id TEXT, files TEXT);
            CREATE TABLE IF NOT EXISTS msg_recipients(
                message_id INTEGER NOT NULL, agent TEXT NOT NULL,
                PRIMARY KEY(message_id, agent));
            CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_request
                ON messages(sender, request_id) WHERE request_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS ix_messages_thread ON messages(thread_id, id);
            CREATE INDEX IF NOT EXISTS ix_recipients_agent ON msg_recipients(agent, message_id);
        """)

    # ------------------------------------------------------------------ agents
    def _agent_row(self, row, show_online=True):
        if row is None:
            return None
        agent = {"id": row["id"], "name": row["name"], "mode": row["mode"], "role": row["role"],
                 "poll_interval": row["poll_interval"], "enabled": bool(row["enabled"]),
                 "created_at": row["created_at"]}
        seen = row["last_seen"]
        agent["last_seen"] = (datetime.fromtimestamp(seen, timezone.utc).isoformat(timespec="seconds")
                              if seen else None)
        if show_online:
            window = max(60, row["poll_interval"] * 2.5)
            agent["online"] = bool(seen and time.time() - seen <= window and row["enabled"])
        return agent

    def register_agent(self, agent_id, name, mode, role="", poll_interval=300):
        if not isinstance(agent_id, str) or not AGENT_ID_RE.match(agent_id) or agent_id in RESERVED_IDS:
            raise HubError("id agent phải khớp ^[a-z][a-z0-9_-]{1,31}$ và không trùng tên dành riêng.")
        name = _text(name, "name", 64, required=True)
        role = _text(role, "role", 200) or ""
        if mode not in MODES:
            raise HubError("mode phải là push hoặc pull.")
        if not isinstance(poll_interval, int) or isinstance(poll_interval, bool) or not 5 <= poll_interval <= 86400:
            raise HubError("poll_interval phải là số giây từ 5 đến 86400.")
        token = _new_token()
        with self._lock:
            try:
                self.db.execute(
                    "INSERT INTO agents(id,name,mode,role,poll_interval,token_hash,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (agent_id, name, mode, role, poll_interval, _hash(token), _now()))
            except sqlite3.IntegrityError:
                raise Conflict("Agent %s đã tồn tại." % agent_id) from None
            row = self.db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        return self._agent_row(row), token

    def rotate_token(self, agent_id):
        token = _new_token()
        with self._lock:
            cur = self.db.execute("UPDATE agents SET token_hash=? WHERE id=?", (_hash(token), agent_id))
            if cur.rowcount == 0:
                raise NotFound("Không có agent %s." % agent_id)
        return token

    def set_enabled(self, agent_id, enabled):
        with self._lock:
            cur = self.db.execute("UPDATE agents SET enabled=? WHERE id=?", (1 if enabled else 0, agent_id))
            if cur.rowcount == 0:
                raise NotFound("Không có agent %s." % agent_id)

    def authenticate(self, token):
        """Trả về agent (dict) nếu token hợp lệ và agent đang bật; cập nhật last_seen."""
        if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or len(token) > 200:
            return None
        with self._lock:
            row = self.db.execute("SELECT * FROM agents WHERE token_hash=? AND enabled=1",
                                  (_hash(token),)).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), row["id"]))
            return self._agent_row(row, show_online=False)

    def list_agents(self):
        with self._lock:
            rows = self.db.execute("SELECT * FROM agents ORDER BY id").fetchall()
        return [self._agent_row(r) for r in rows]

    def _known_ids(self):
        return {r["id"] for r in self.db.execute("SELECT id FROM agents")}

    # ---------------------------------------------------------------- messages
    def _message(self, row):
        recipients = [r["agent"] for r in self.db.execute(
            "SELECT agent FROM msg_recipients WHERE message_id=? ORDER BY agent", (row["id"],))]
        message = {"id": row["id"], "created_at": row["created_at"], "from": row["sender"], "to": recipients,
                   "kind": row["kind"], "thread_id": row["thread_id"], "body": row["body"]}
        for key in ("task_id", "project", "title", "reply_to"):
            if row[key] is not None:
                message[key] = row[key]
        if row["files"]:
            message["files"] = json.loads(row["files"])
        return message

    @staticmethod
    def _files(files):
        if files is None:
            return None
        if not isinstance(files, list) or len(files) > 10:
            raise HubError("files phải là danh sách tối đa 10 mục.")
        clean = []
        for item in files:
            if not isinstance(item, dict):
                raise HubError("Mỗi mục files phải là object {id, name, url}.")
            entry = {k: _text(item.get(k), "files." + k, 2048) for k in ("id", "name", "url") if item.get(k) is not None}
            if not entry.get("url") and not entry.get("id"):
                raise HubError("Mỗi mục files cần url hoặc id.")
            clean.append(entry)
        return clean or None

    def post(self, sender, body, to=None, kind="message", task_id=None, project=None, title=None,
             reply_to=None, thread_id=None, request_id=None, files=None):
        body = _text(body, "body", MAX_BODY, required=True)
        task_id = _text(task_id, "task_id", 64)
        project = _text(project, "project", 128)
        title = _text(title, "title", 200)
        thread_id = _text(thread_id, "thread_id", 64)
        request_id = _text(request_id, "request_id", 64)
        if kind not in KINDS:
            raise HubError("kind phải là một trong: %s." % ", ".join(sorted(KINDS)))
        if reply_to is not None and (not isinstance(reply_to, int) or isinstance(reply_to, bool) or reply_to < 1):
            raise HubError("reply_to phải là id tin (số nguyên).")
        files = self._files(files)
        with self._lock:
            known = self._known_ids()
            is_human = sender in HUMANS
            if not is_human and sender not in known:
                raise Forbidden("Người gửi không hợp lệ.")
            if request_id:
                dup = self.db.execute("SELECT * FROM messages WHERE sender=? AND request_id=?",
                                      (sender, request_id)).fetchone()
                if dup:
                    message = self._message(dup)
                    message["duplicate"] = True
                    return message
            original = None
            if reply_to is not None:
                original = self.db.execute("SELECT * FROM messages WHERE id=?", (reply_to,)).fetchone()
                if original is None:
                    raise HubError("Không tìm thấy tin gốc.")
                if original["sender"] == sender and not is_human:
                    raise Conflict("Agent không tự trả lời tin của mình.")
            if to is None:
                recipients = [original["sender"]] if original is not None else ["all"]
            else:
                recipients = [to] if isinstance(to, str) else to
                if not isinstance(recipients, list) or not recipients or len(recipients) > 20:
                    raise HubError("to phải là id hoặc danh sách 1-20 id.")
            valid = known | HUMANS | {"all"}
            for r in recipients:
                if not isinstance(r, str) or r not in valid:
                    raise HubError("Người nhận không hợp lệ: %s." % (r if isinstance(r, str) else "?"))
            recipients = ["all"] if "all" in recipients else sorted(set(recipients))
            if sender in recipients:
                raise HubError("Không gửi tin cho chính mình.")
            if not thread_id:
                thread_id = original["thread_id"] if original is not None else (task_id or "main")
            exempt = kind == "result" and recipients == ["anh"]
            turn = 0 if (is_human or exempt) else 1
            if turn:
                self._guard_loop(sender, thread_id, body)
            cur = self.db.execute(
                "INSERT INTO messages(created_at,sender,is_human,turn,kind,task_id,project,title,body,"
                "reply_to,thread_id,request_id,files) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), sender, int(is_human), turn, kind, task_id, project, title, body, reply_to,
                 thread_id, request_id, json.dumps(files, ensure_ascii=False) if files else None))
            mid = cur.lastrowid
            self.db.executemany("INSERT INTO msg_recipients(message_id,agent) VALUES(?,?)",
                                [(mid, r) for r in recipients])
            message = self._message(self.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone())
            self.changed.notify_all()
        return message

    def _guard_loop(self, sender, thread_id, body):
        """Tối đa 8 lượt agent liên tiếp kể từ tin cuối của người; chặn nội dung lặp."""
        window = ("thread_id=? AND id > COALESCE((SELECT MAX(id) FROM messages "
                  "WHERE thread_id=? AND is_human=1),0)")
        turns = self.db.execute("SELECT COALESCE(SUM(turn),0) FROM messages WHERE " + window,
                                (thread_id, thread_id)).fetchone()[0]
        if turns >= MAX_AGENT_TURNS:
            raise Conflict("Đã đủ %d lượt agent trong luồng này. Chờ anh nhắn." % MAX_AGENT_TURNS)
        normalized = " ".join(body.casefold().split())
        for row in self.db.execute("SELECT body FROM messages WHERE turn=1 AND sender=? AND " + window,
                                   (sender, thread_id, thread_id)):
            if " ".join(row["body"].casefold().split()) == normalized:
                raise Conflict("Nội dung đã đăng trong luồng này.")

    @staticmethod
    def _after(after):
        if after is None:
            return 0
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise HubError("after phải là số nguyên không âm.")
        return after

    @staticmethod
    def _limit(limit):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise HubError("limit phải từ 1 đến 200.")
        return limit

    def read_for(self, agent_id, after=0, limit=100):
        """Tin gửi tới agent (hoặc all), không gồm tin chính agent gửi, tăng dần theo id."""
        after, limit = self._after(after), self._limit(limit)
        with self._lock:
            rows = self.db.execute(
                "SELECT m.* FROM messages m WHERE m.id>? AND m.sender<>? AND EXISTS("
                "SELECT 1 FROM msg_recipients r WHERE r.message_id=m.id AND r.agent IN (?, 'all'))"
                " ORDER BY m.id LIMIT ?", (after, agent_id, agent_id, limit)).fetchall()
            return [self._message(r) for r in rows]

    def read_all(self, after=0, limit=100, thread_id=None, tail=None):
        """Toàn bộ tin (cho anh). tail=N: N tin mới nhất, vẫn xếp tăng dần theo id."""
        after, limit = self._after(after), self._limit(limit)
        clauses, args = ["id>?"], [after]
        if thread_id:
            clauses.append("thread_id=?")
            args.append(thread_id)
        where = " WHERE " + " AND ".join(clauses)
        with self._lock:
            if tail is not None:
                rows = self.db.execute("SELECT * FROM messages" + where + " ORDER BY id DESC LIMIT ?",
                                       args + [self._limit(tail)]).fetchall()[::-1]
            else:
                rows = self.db.execute("SELECT * FROM messages" + where + " ORDER BY id LIMIT ?",
                                       args + [limit]).fetchall()
            return [self._message(r) for r in rows]

    def wait_for(self, agent_id, after, limit, seconds):
        """Long-poll: trả về ngay khi có tin mới hoặc hết thời gian chờ."""
        deadline = time.monotonic() + seconds
        with self.changed:
            while True:
                rows = self.read_for(agent_id, after, limit)
                remaining = deadline - time.monotonic()
                if rows or remaining <= 0:
                    return rows
                self.changed.wait(min(remaining, 1))
