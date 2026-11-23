"""Nhận wake tại VM, giữ inbox/outbox SQLite và chạy trình Tinker đã cấu hình."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlsplit

from room_dispatch import Dispatch, request_json, brief_report


class Inbox(Dispatch):
    def __init__(self, path):
        super().__init__(path)
        self.changed = threading.Event()
        with self.transaction() as db:
            db.execute("UPDATE jobs SET state='uncertain' WHERE state='running'")

    def uncertain(self):
        with self.transaction() as db:
            return [row[0] for row in db.execute("SELECT id FROM jobs WHERE state='uncertain'")]

    def receive(self, task):
        mid = task["message"]["id"]
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{12}", mid):
            raise ValueError("Invalid message ID")
        with self.transaction() as db:
            db.execute("""INSERT OR IGNORE INTO jobs (id,agent,state,task)
                       VALUES (?,'tinker','pending',?)""", (mid, json.dumps(task, ensure_ascii=False)))
        self.changed.set()
        return mid


def wake_handler(inbox, token):
    class WakeHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/wake":
                self.send_error(404)
                return
            if not secrets.compare_digest(
                    self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()):
                self.send_error(401)
                return
            try:
                self.connection.settimeout(5)
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 180000:
                    raise ValueError("Invalid payload size")
                mid = inbox.receive(json.loads(self.rfile.read(size)))
            except (TypeError, ValueError, KeyError, TimeoutError):
                self.send_error(400)
                return
            response = json.dumps({"accepted": mid}).encode()
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
    return WakeHandler


def step(inbox, run, api, api_token):
    job = inbox.next_job("tinker", reserve=True)
    if job is None:
        return False
    mid, state, task, result = job
    if state == "pending":
        try:
            answer = run(task)
            kind = answer.get("kind", "result")
            if kind == "silent":
                inbox.finish(mid, "tinker", "silent")
                return True
            with inbox.transaction() as db:
                inbox.put(db, 'report:' + mid, answer)
            body = answer["body"].strip()
            if kind not in {"result", "handoff"} or not body:
                raise ValueError("Invalid adapter output")
            body = body.replace(api_token, "[ĐÃ ẨN]")
            result = {"from": "tinker", "reply_to": mid, "kind": kind, "body": brief_report(body, "VM TINKER_WAKE_DB / meta report:" + mid)}
        except Exception as exc:
            inbox.finish(mid, "tinker", "uncertain")
            print(f"room id={mid}: adapter bị gián đoạn ({type(exc).__name__})", flush=True)
            return True
        inbox.finish(mid, "tinker", "outbox", result)
    try:
        request_json(api + "/internal/message", api_token, result)
    except HTTPError as exc:
        if exc.code != 409:
            raise
        inbox.finish(mid, "tinker", "blocked", result)
        print(f"room id={mid}: 409, đã giữ bản kết quả trong SQLite", flush=True)
        return True
    inbox.finish(mid, "tinker", "done")
    return True


def main():
    token = os.environ["AI_HUB_TINKER_WAKE_TOKEN"]
    api_token = os.environ["AI_HUB_INTERNAL_TOKEN"]
    api = os.environ["AI_HUB_ROOM_URL"].rstrip("/")
    command = json.loads(os.environ["TINKER_WAKE_COMMAND"])
    if not token or not api_token or api_token == token:
        raise SystemExit("Cần hai token riêng.")
    parsed = urlsplit(api)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise SystemExit("URL AI Hub không hợp lệ.")
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise SystemExit("TINKER_WAKE_COMMAND phải là mảng argv JSON.")
    path = Path(os.environ["TINKER_WAKE_DB"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    with ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("TINKER_WAKE_PORT", "8784"))),
                             BaseHTTPRequestHandler) as server:
        inbox = Inbox(path)
        server.RequestHandlerClass = wake_handler(inbox, token)

        def run(task):
            env = {key: value for key, value in os.environ.items()
                   if key not in {"AI_HUB_TINKER_WAKE_TOKEN", "AI_HUB_INTERNAL_TOKEN"}}
            completed = subprocess.run(command, input=json.dumps(task, ensure_ascii=False),
                                       encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       env=env, timeout=900, check=True)
            return json.loads(completed.stdout)

        def consume():
            for mid in inbox.uncertain():
                print(f"room id={mid}: phiên trước bị gián đoạn", flush=True)
            while True:
                inbox.changed.clear()
                try:
                    if not step(inbox, run, api, api_token):
                        inbox.changed.wait(5)
                except Exception as exc:
                    print(f"Thử lại sau 3 giây ({type(exc).__name__})", flush=True)
                    time.sleep(3)

        threading.Thread(target=consume, daemon=True).start()
        server.serve_forever()


if __name__ == "__main__":
    main()
