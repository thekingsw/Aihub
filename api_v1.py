"""AI Hub API v1 — mỗi AI một token riêng, tin có id tăng dần.

Cắm vào ai_hub.Handler: mọi đường dẫn /api/v1/... được chuyển cho dispatch().
Xác thực: Authorization: Bearer <token agent> hoặc AI_HUB_INTERNAL_TOKEN (quyền admin, gửi với tư cách anh).
"""
import json
import os
import re
import secrets
import threading
from urllib.parse import parse_qs, quote, urlparse

import hub_db
import hub_files
import hub_legacy
import hub_tasks
from hub_db import HubError, Forbidden, NotFound

BASE = os.path.dirname(os.path.abspath(__file__))
MAX_REQUEST = 256 * 1024
MAX_WAIT = 25.0
_hub = None
legacy_rows = None   # ai_hub gắn hàm đọc hộp thư cũ vào đây; None nghĩa là không có
_hub_lock = threading.Lock()


def get_hub():
    global _hub
    with _hub_lock:
        if _hub is None:
            _hub = hub_db.HubDB(os.environ.get("AI_HUB_DB", os.path.join(BASE, "hub.sqlite3")))
        return _hub


class _Principal:
    def __init__(self, admin=False, agent=None):
        self.admin = admin
        self.agent = agent

    @property
    def sender(self):
        return "anh" if self.admin else self.agent["id"]


def _authenticate(handler):
    header = handler.headers.get("Authorization", "")
    if not header:
        # Giao diện web: phiên đăng nhập của anh (cookie). Yêu cầu ghi phải kèm header tự đặt
        # X-Mailbox-Request để chặn giả mạo yêu cầu chéo trang (cùng cơ chế với /api/message).
        session = getattr(handler, "_authenticated", None)
        if session is not None and session():
            if handler.command != "GET" and handler.headers.get("X-Mailbox-Request") != "1":
                raise HubError("Yêu cầu không hợp lệ.", 403, "FORBIDDEN")
            return _Principal(admin=True)
        raise HubError("Thiếu hoặc sai token.", 401, "UNAUTHENTICATED")
    if not header.startswith("Bearer "):
        raise HubError("Thiếu hoặc sai token.", 401, "UNAUTHENTICATED")
    token = header[7:].strip()
    expected = os.environ.get("AI_HUB_INTERNAL_TOKEN", "")
    if expected and secrets.compare_digest(token, expected):
        return _Principal(admin=True)
    agent = get_hub().authenticate(token)
    if agent is None:
        raise HubError("Thiếu hoặc sai token.", 401, "UNAUTHENTICATED")
    return _Principal(agent=agent)


def _require_admin(principal):
    if not principal.admin:
        raise Forbidden("Chỉ admin mới được làm việc này.")


def _read_json(handler):
    if handler.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
        raise HubError("Content-Type phải là application/json.", 415, "UNSUPPORTED_MEDIA_TYPE")
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        raise HubError("Content-Length không hợp lệ.") from None
    if not 0 < length <= MAX_REQUEST:
        raise HubError("Nội dung yêu cầu rỗng hoặc quá lớn.", 413, "PAYLOAD_TOO_LARGE")
    try:
        data = json.loads(handler.rfile.read(length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HubError("JSON không hợp lệ.") from None
    if not isinstance(data, dict):
        raise HubError("Nội dung phải là một object JSON.")
    return data


def _int(query, name, default):
    values = query.get(name)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError:
        raise HubError("%s phải là số nguyên." % name) from None


def _messages_payload(messages, after):
    return {"ok": True, "messages": messages, "last_id": messages[-1]["id"] if messages else after}


class _Raw:
    """Phản hồi tự ghi (tải tệp), không phải JSON."""

    def __init__(self, send):
        self.send = send


def _send_file(handler, row, path):
    inline = row["mime"] in hub_files.INLINE_MIMES
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in row["name"]) or "tep"
    handler.send_response(200)
    handler.send_header("Content-Type", row["mime"] + ("; charset=utf-8" if row["mime"] == "text/plain" else "")
                        if inline else hub_files.OCTET)
    handler.send_header("Content-Length", str(row["size"]))
    handler.send_header("Content-Disposition", "%s; filename=\"%s\"; filename*=UTF-8''%s" % (
        "inline" if inline else "attachment", ascii_name, quote(row["name"])))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
    handler.send_header("Cache-Control", "private, max-age=86400")
    handler.end_headers()
    try:
        with open(path, "rb") as source:
            while True:
                chunk = source.read(hub_files.CHUNK)
                if not chunk:
                    break
                handler.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _get(handler, path, query):
    hub = get_hub()
    principal = _authenticate(handler)
    if path == "/api/v1/me":
        return {"ok": True, "me": principal.sender, "admin": principal.admin,
                "limits": {"max_upload_bytes": hub_files.limits()[0]}}
    if path == "/api/v1/messages":
        after, limit = _int(query, "after", 0), _int(query, "limit", 100)
        if principal.admin:
            rows = hub.read_all(after, limit, (query.get("thread_id") or [None])[0], _int(query, "tail", None))
            return _messages_payload(rows, after)
        try:
            wait = float((query.get("wait") or ["0"])[0])
        except ValueError:
            raise HubError("wait phải là số giây.") from None
        wait = min(max(wait, 0.0), MAX_WAIT)
        agent_id = principal.agent["id"]
        rows = hub.wait_for(agent_id, after, limit, wait) if wait else hub.read_for(agent_id, after, limit)
        return _messages_payload(rows, after)
    if path == "/api/v1/agents":
        return {"ok": True, "agents": hub.list_agents()}
    match = re.fullmatch(r"/api/v1/files/([0-9a-f]{32})", path)
    if match:
        row, file_path = hub_files.store_for(hub).open(match.group(1))
        return _Raw(lambda h: _send_file(h, row, file_path))
    if path == "/api/v1/files":
        _require_admin(principal)
        return {"ok": True, "files": hub.list_files(_int(query, "limit", 100)),
                "used_bytes": hub.files_total_size(), "quota_bytes": hub_files.limits()[1]}
    board = hub_tasks.board_for(hub)
    if path == "/api/v1/tasks":
        first = lambda name: (query.get(name) or [None])[0]  # noqa: E731
        return {"ok": True, "tasks": board.list(first("status"), first("assignee"), first("project"),
                                                _int(query, "limit", 100))}
    match = re.fullmatch(r"/api/v1/tasks/([A-Za-z0-9][A-Za-z0-9_-]{0,39})", path)
    if match:
        return {"ok": True, "task": board.get(match.group(1))}
    raise NotFound("Không có đường dẫn này.")


def _post(handler, path, query):
    hub = get_hub()
    principal = _authenticate(handler)
    if path == "/api/v1/files":
        name = (query.get("name") or [""])[0]
        if not name.strip():
            raise HubError("Thiếu tham số name (tên tệp).")
        try:
            length = int(handler.headers.get("Content-Length", ""))
        except ValueError:
            length = None
        handler.connection.settimeout(60)  # chặn máy khách chậm giữ luồng mãi
        return {"ok": True, "file": hub_files.store_for(hub).save(principal.sender, name, handler.rfile, length)}, 201
    if path == "/api/v1/messages":
        data = _read_json(handler)
        claimed = data.pop("from", None)
        if claimed is not None and claimed != principal.sender:
            raise Forbidden("Trường from không khớp với token.")
        fields = {k: data.get(k) for k in ("to", "kind", "task_id", "project", "title", "reply_to",
                                           "thread_id", "request_id", "files")}
        fields = {k: v for k, v in fields.items() if v is not None}
        message = hub.post(principal.sender, data.get("body"), **fields)
        return {"ok": True, "message": message}, (200 if message.get("duplicate") else 201)
    board = hub_tasks.board_for(hub)
    if path == "/api/v1/tasks":
        data = _read_json(handler)
        task = board.create(principal.sender, data.get("title"), data.get("assignee"), data.get("description", ""),
                            data.get("project"), data.get("reviewer"), data.get("id"))
        return {"ok": True, "task": task}, 201
    match = re.fullmatch(r"/api/v1/tasks/([A-Za-z0-9][A-Za-z0-9_-]{0,39})/(status|assign)", path)
    if match:
        task_id, action = match.groups()
        data = _read_json(handler)
        if action == "status":
            return {"ok": True, "task": board.transition(principal.sender, task_id, data.get("status"),
                                                          data.get("note"), data.get("files"))}
        _require_admin(principal)
        return {"ok": True, "task": board.reassign(task_id, data.get("assignee"), data.get("reviewer"),
                                                   bool(data.get("clear_reviewer")))}
    _require_admin(principal)
    if path == "/api/v1/agents":
        data = _read_json(handler)
        agent, token = hub.register_agent(data.get("id"), data.get("name"), data.get("mode"),
                                          data.get("role", ""), data.get("poll_interval", 300))
        return {"ok": True, "agent": agent, "token": token,
                "note": "Token chỉ hiện một lần; hãy lưu ngay."}, 201
    if path == "/api/v1/files/purge":
        data = _read_json(handler)
        return {"ok": True, "result": hub_files.store_for(hub).purge(data.get("older_than_days"),
                                                                      data.get("dry_run", True) is not False)}
    if path == "/api/v1/legacy/import":
        data = _read_json(handler)
        if legacy_rows is None:
            raise HubError("Máy chủ này không có hộp thư cũ để nhập.", 501, "NOT_IMPLEMENTED")
        mapping = hub_legacy.parse_map(["%s=%s" % (k, v) for k, v in (data.get("map") or {}).items()])
        try:
            rows = legacy_rows()
        except (OSError, ValueError) as exc:
            raise HubError("Không đọc được hộp thư cũ: %s" % exc, 500, "LEGACY_UNREADABLE") from None
        return {"ok": True, "stats": hub_legacy.import_rows(hub, rows, mapping, bool(data.get("dry_run"))),
                "mapping": mapping}
    match = re.fullmatch(r"/api/v1/files/([0-9a-f]{32})/delete", path)
    if match:
        row = hub_files.store_for(hub).delete(match.group(1))
        return {"ok": True, "deleted": hub.file_public(row)}
    match = re.fullmatch(r"/api/v1/agents/([a-z][a-z0-9_-]{1,31})/(rotate|enable|disable)", path)
    if match:
        agent_id, action = match.groups()
        if action == "rotate":
            return {"ok": True, "token": hub.rotate_token(agent_id),
                    "note": "Token cũ đã vô hiệu; token mới chỉ hiện một lần."}
        hub.set_enabled(agent_id, action == "enable")
        return {"ok": True}
    raise NotFound("Không có đường dẫn này.")


def dispatch(handler, method):
    path = urlparse(handler.path)
    query = parse_qs(path.query, keep_blank_values=True)
    try:
        if method == "GET":
            result = _get(handler, path.path.rstrip("/"), query)
        elif method == "POST":
            result = _post(handler, path.path.rstrip("/"), query)
        else:
            raise HubError("Phương thức không được hỗ trợ.", 405, "METHOD_NOT_ALLOWED")
        if isinstance(result, _Raw):
            result.send(handler)
            return
        status = 200
        if isinstance(result, tuple):
            result, status = result
        handler._json(result, status)
    except HubError as exc:
        handler._json({"ok": False, "error": exc.code, "message": str(exc)}, exc.status)
    except Exception as exc:  # noqa: BLE001 - không để lỗi lạ làm đứt kết nối
        print("api_v1 loi: %r" % (exc,), flush=True)
        handler._json({"ok": False, "error": "INTERNAL", "message": "Lỗi máy chủ."}, 500)
