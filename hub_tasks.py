"""AI Hub P2: bảng việc (task board) — giao việc, làm, nhờ review, nghiệm thu.

Vòng đời:  todo → in_progress → in_review → done
                       ↑______rework______|
Mỗi lần chuyển trạng thái tự đăng tin tương ứng vào phòng (cùng luồng thread_id = task id), và
được ghi vào task_events. Chuyển trạng thái + đăng tin diễn ra trong một savepoint: tin bị chặn
(vd. đủ 8 lượt agent) thì trạng thái không đổi.
"""
import re
import sqlite3
import threading

from hub_db import HUMANS, Conflict, Forbidden, HubError, NotFound, _now, _text

STATUSES = ("todo", "in_progress", "in_review", "done", "blocked", "cancelled")
TERMINAL = {"done", "cancelled"}
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")

# (từ, đến) -> các vai được phép. "admin" = anh. Vai: assignee, reviewer, creator.
TRANSITIONS = {
    ("todo", "in_progress"): {"assignee"},
    ("in_progress", "in_review"): {"assignee"},
    ("in_progress", "done"): {"assignee"},          # chỉ khi việc không có reviewer
    ("in_review", "done"): {"reviewer"},
    ("in_review", "in_progress"): {"reviewer"},     # trả về làm lại (rework)
    ("blocked", "in_progress"): {"assignee"},
    ("done", "in_progress"): set(),                 # mở lại: chỉ admin
    ("cancelled", "in_progress"): set(),
}
for _src in ("todo", "in_progress", "in_review"):
    TRANSITIONS[(_src, "blocked")] = {"assignee"}
for _src in ("todo", "in_progress", "in_review", "blocked"):
    TRANSITIONS[(_src, "cancelled")] = {"creator"}
NOTE_REQUIRED = {"blocked", "cancelled"}


def board_for(hub):
    """Mỗi HubDB có đúng một TaskBoard."""
    board = getattr(hub, "_board", None)
    if board is None:
        with hub._lock:
            board = getattr(hub, "_board", None)
            if board is None:
                board = hub._board = TaskBoard(hub)
    return board


class TaskBoard:
    def __init__(self, hub):
        self.hub = hub
        self.db = hub.db
        self._lock = hub._lock
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                project TEXT, status TEXT NOT NULL, assignee TEXT NOT NULL, reviewer TEXT,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, created_at TEXT NOT NULL,
                actor TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, note TEXT, message_id INTEGER);
            CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status, assignee);
            CREATE INDEX IF NOT EXISTS ix_task_events_task ON task_events(task_id, id);
        """)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _task(row):
        return {k: row[k] for k in ("id", "title", "description", "project", "status", "assignee",
                                    "reviewer", "created_by", "created_at", "updated_at")}

    def _begin(self):
        self.db.execute("SAVEPOINT task_op")

    def _commit(self):
        self.db.execute("RELEASE task_op")

    def _rollback(self):
        self.db.execute("ROLLBACK TO task_op")
        self.db.execute("RELEASE task_op")

    def _check_actor(self, actor):
        if actor not in HUMANS and actor not in self.hub._known_ids():
            raise Forbidden("Người thực hiện không hợp lệ.")

    def _agent(self, agent_id, name, allow_human=False):
        if allow_human and agent_id in HUMANS:
            return agent_id
        row = (self.db.execute("SELECT enabled FROM agents WHERE id=?", (agent_id,)).fetchone()
               if isinstance(agent_id, str) else None)
        if row is None or not row["enabled"]:
            raise HubError("%s không hợp lệ hoặc đang tắt: %s." % (name, agent_id))
        return agent_id

    # -------------------------------------------------------------------- tạo việc
    def create(self, actor, title, assignee, description="", project=None, reviewer=None, task_id=None):
        title = _text(title, "title", 200, required=True)
        description = _text(description, "description", 20000) or title
        project = _text(project, "project", 128)
        with self._lock:
            self._check_actor(actor)
            self._agent(assignee, "assignee")
            if reviewer is not None:
                self._agent(reviewer, "reviewer", allow_human=True)
                if reviewer == assignee:
                    raise HubError("reviewer phải khác assignee.")
            if assignee == actor:
                raise HubError("Không giao việc cho chính mình.")
            if task_id is None:
                last = self.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                task_id = "T-%04d" % (last + 1)
                while self.db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                    last += 1
                    task_id = "T-%04d" % (last + 1)
            elif not isinstance(task_id, str) or not TASK_ID_RE.match(task_id) or task_id.lower() == "legacy":
                raise HubError("id việc phải khớp ^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$ (và không phải 'legacy').")
            now = _now()
            self._begin()
            try:
                try:
                    self.db.execute(
                        "INSERT INTO tasks(id,title,description,project,status,assignee,reviewer,created_by,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (task_id, title, description, project, "todo", assignee, reviewer, actor, now, now))
                except sqlite3.IntegrityError:
                    raise Conflict("Việc %s đã tồn tại." % task_id) from None
                message = self.hub.post(actor, description, to=assignee, kind="task", task_id=task_id,
                                        project=project, title=title, thread_id=task_id)
                self._event(task_id, actor, None, "todo", None, message["id"])
                self._commit()
            except Exception:
                self._rollback()
                raise
            return self.get(task_id)

    def _event(self, task_id, actor, source, target, note, message_id):
        self.db.execute("INSERT INTO task_events(task_id,created_at,actor,from_status,to_status,note,message_id)"
                        " VALUES(?,?,?,?,?,?,?)", (task_id, _now(), actor, source, target, note, message_id))

    # --------------------------------------------------------------------- đọc
    def get(self, task_id, with_events=True):
        with self._lock:
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise NotFound("Không có việc %s." % task_id)
            task = self._task(row)
            if with_events:
                task["events"] = [
                    {k: e[k] for k in ("created_at", "actor", "from_status", "to_status", "note", "message_id")}
                    for e in self.db.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (task_id,))]
            return task

    def list(self, status=None, assignee=None, project=None, limit=100, involves=None, open_only=False):
        """involves=<agent>: việc mà agent là người làm hoặc người review. open_only: bỏ việc đã xong/huỷ."""
        if status is not None and status not in STATUSES:
            raise HubError("status phải là một trong: %s." % ", ".join(STATUSES))
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise HubError("limit phải từ 1 đến 200.")
        clauses, args = [], []
        for column, value in (("status", status), ("assignee", assignee), ("project", project)):
            if value is not None:
                clauses.append(column + "=?")
                args.append(value)
        if involves is not None:
            clauses.append("(assignee=? OR reviewer=?)")
            args += [involves, involves]
        if open_only:
            clauses.append("status NOT IN ('done','cancelled')")
        sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        with self._lock:
            rows = self.db.execute(sql + " ORDER BY updated_at DESC, id LIMIT ?", args + [limit]).fetchall()
        return [self._task(r) for r in rows]

    # ------------------------------------------------------------- chuyển trạng thái
    def _roles(self, actor, task):
        roles = set()
        if actor in HUMANS:
            roles.add("admin")
        if actor == task["assignee"]:
            roles.add("assignee")
        if actor == task["reviewer"]:
            roles.add("reviewer")
        if actor == task["created_by"]:
            roles.add("creator")
        return roles

    def transition(self, actor, task_id, to_status, note=None, files=None):
        if to_status not in STATUSES:
            raise HubError("status phải là một trong: %s." % ", ".join(STATUSES))
        note = _text(note, "note", 20000)
        with self._lock:
            self._check_actor(actor)
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise NotFound("Không có việc %s." % task_id)
            task = self._task(row)
            source = task["status"]
            allowed = TRANSITIONS.get((source, to_status))
            if allowed is None:
                raise Conflict("Không thể chuyển %s → %s." % (source, to_status))
            if source == "in_progress" and to_status == "done" and task["reviewer"]:
                raise Conflict("Việc có reviewer: phải chuyển in_review trước.")
            if source == "in_review" and to_status == "in_progress" and not note:
                raise HubError("Trả về làm lại cần note nêu lý do.")
            if to_status in NOTE_REQUIRED and not note:
                raise HubError("Chuyển sang %s cần note." % to_status)
            roles = self._roles(actor, task)
            if "admin" not in roles and not (roles & allowed):
                raise Forbidden("Bạn không có quyền chuyển %s → %s." % (source, to_status))
            self._begin()
            try:
                message = self._notify(actor, task, source, to_status, note, files)
                self.db.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                                (to_status, _now(), task_id))
                self._event(task_id, actor, source, to_status, note, message["id"] if message else None)
                self._commit()
            except Exception:
                self._rollback()
                raise
            return self.get(task_id)

    def _notify(self, actor, task, source, target, note, files):
        """Đăng tin tương ứng với bước chuyển; trả về tin hoặc None nếu không cần báo."""
        def to(*ids):
            return [i for i in dict.fromkeys(ids) if i and i != actor] or None

        kind, recipients, body = None, None, note
        if target == "in_review":
            kind, recipients = "handoff", to(task["reviewer"], "anh")
            body = note or "Đã xong phần việc, mời review."
        elif target == "done" and source == "in_review":
            kind, recipients = "review", to(task["assignee"], "anh")
            body = "ĐẠT. " + note if note else "ĐẠT."
        elif target == "done":
            kind, recipients = ("result", to("anh")) if actor != "anh" else ("message", to(task["assignee"]))
            body = note or "Đã hoàn thành."
        elif source == "in_review" and target == "in_progress":
            kind, recipients = "review", to(task["assignee"])
            body = "CẦN SỬA. " + note
        elif target == "blocked":
            kind, recipients = "result", to("anh")
            body = "BỊ CHẶN. " + note
        elif target == "cancelled":
            kind, recipients = "notice", to(task["assignee"])
            body = "ĐÃ HUỶ. " + note
        elif source == "blocked" and target == "in_progress":
            kind, recipients = "task", to(task["assignee"])
            body = "MỞ CHẶN. " + (note or task["title"])
        elif source in TERMINAL and target == "in_progress":
            kind, recipients = "task", to(task["assignee"])
            body = "MỞ LẠI. " + (note or task["title"])
        if not kind or not recipients:
            return None
        return self.hub.post(actor, body, to=recipients, kind=kind, task_id=task["id"],
                             project=task["project"], title=task["title"], thread_id=task["id"], files=files)

    def reassign(self, task_id, assignee=None, reviewer=None, clear_reviewer=False):
        """Admin: đổi người làm / người review. Người làm mới nhận lại tin giao việc."""
        with self._lock:
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise NotFound("Không có việc %s." % task_id)
            task = self._task(row)
            if task["status"] in TERMINAL:
                raise Conflict("Việc đã %s; hãy mở lại trước." % task["status"])
            new_assignee = assignee or task["assignee"]
            new_reviewer = None if clear_reviewer else (reviewer or task["reviewer"])
            self._agent(new_assignee, "assignee")
            if new_reviewer is not None:
                self._agent(new_reviewer, "reviewer", allow_human=True)
                if new_reviewer == new_assignee:
                    raise HubError("reviewer phải khác assignee.")
            self._begin()
            try:
                message = None
                if new_assignee != task["assignee"]:
                    message = self.hub.post("anh", task["description"], to=new_assignee, kind="task",
                                            task_id=task_id, project=task["project"], title=task["title"],
                                            thread_id=task_id)
                self.db.execute("UPDATE tasks SET assignee=?, reviewer=?, updated_at=? WHERE id=?",
                                (new_assignee, new_reviewer, _now(), task_id))
                self._event(task_id, "anh", task["status"], task["status"],
                            "assignee=%s reviewer=%s" % (new_assignee, new_reviewer),
                            message["id"] if message else None)
                self._commit()
            except Exception:
                self._rollback()
                raise
            return self.get(task_id)
