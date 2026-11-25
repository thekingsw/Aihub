#!/usr/bin/env python3
"""Client tối giản cho AI Hub v1 (stdlib). Dùng cho Muse, Codex adapter, Claude Code...

Biến môi trường: AI_HUB_URL (vd http://100.79.204.50:8090), AI_HUB_TOKEN (token riêng của agent).
Token không nhận qua tham số dòng lệnh để khỏi lọt vào lịch sử shell.

  python agent_client.py poll --after 0 [--wait 20]
  python agent_client.py post --to codex --kind task --task-id T1 --title "Làm game" "Nội dung..."
  python agent_client.py agents
  python agent_client.py upload anh-chup.png      # in ra id tệp; gắn vào tin bằng: post --file <id> ...
  python agent_client.py download <id> dich.png
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class HubClient:
    def __init__(self, base_url, token, timeout=60):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method, path, body=None, timeout=None, raw=None):
        if raw is not None:
            data, content_type = raw, "application/octet-stream"
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            content_type = "application/json"
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except ValueError:
                return {"ok": False, "error": "HTTP_%d" % exc.code, "message": exc.reason}

    def poll(self, after=0, limit=50, wait=0):
        path = "/api/v1/messages?after=%d&limit=%d" % (after, limit)
        if wait:
            path += "&wait=%s" % wait
        return self._request("GET", path, timeout=self.timeout + wait)

    def post(self, body, **fields):
        return self._request("POST", "/api/v1/messages", dict(fields, body=body))

    def agents(self):
        return self._request("GET", "/api/v1/agents")

    def upload(self, source, name=None):
        """Tải một tệp (đường dẫn hoặc bytes) lên hub. Trả về {"ok": True, "file": {id, name, size, url, ...}}."""
        if isinstance(source, (bytes, bytearray)):
            data = bytes(source)
        else:
            with open(source, "rb") as handle:
                data = handle.read()
            name = name or os.path.basename(str(source))
        if not name:
            raise ValueError("Cần tên tệp khi upload bytes.")
        return self._request("POST", "/api/v1/files?name=" + urllib.parse.quote(name, safe=""), raw=data,
                             timeout=max(self.timeout, 120))

    def download(self, file_id, dest):
        """Tải tệp về đường dẫn `dest` (ghi tạm rồi đổi tên). Trả về đường dẫn, lỗi thì raise OSError."""
        request = urllib.request.Request(self.base + "/api/v1/files/" + urllib.parse.quote(file_id, safe=""))
        request.add_header("Authorization", "Bearer " + self.token)
        tmp = str(dest) + ".part"
        try:
            with urllib.request.urlopen(request, timeout=max(self.timeout, 120)) as response, open(tmp, "wb") as out:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
            os.replace(tmp, dest)
        except urllib.error.HTTPError as exc:
            raise OSError("Hub trả lỗi %d khi tải tệp %s." % (exc.code, file_id)) from None
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return dest

    def me(self):
        return self._request("GET", "/api/v1/me")

    def task(self, task_id):
        return self._request("GET", "/api/v1/tasks/" + urllib.parse.quote(task_id, safe=""))

    def transition(self, task_id, status, note=None, files=None):
        body = {"status": status}
        if note:
            body["note"] = note
        if files:
            body["files"] = files
        return self._request("POST", "/api/v1/tasks/" + urllib.parse.quote(task_id, safe="") + "/status", body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    poll = sub.add_parser("poll")
    poll.add_argument("--after", type=int, default=0)
    poll.add_argument("--limit", type=int, default=50)
    poll.add_argument("--wait", type=int, default=0)
    post = sub.add_parser("post")
    post.add_argument("body")
    post.add_argument("--to", action="append")
    post.add_argument("--kind", default="message")
    for name in ("task-id", "project", "title", "thread-id", "request-id"):
        post.add_argument("--" + name)
    post.add_argument("--reply-to", type=int)
    post.add_argument("--file", action="append", help="id tệp đã upload (có thể lặp lại)")
    sub.add_parser("agents")
    up = sub.add_parser("upload")
    up.add_argument("path")
    up.add_argument("--name")
    down = sub.add_parser("download")
    down.add_argument("id")
    down.add_argument("dest")
    args = parser.parse_args(argv)
    base, token = os.environ.get("AI_HUB_URL"), os.environ.get("AI_HUB_TOKEN")
    if not base or not token:
        sys.exit("Thiếu AI_HUB_URL hoặc AI_HUB_TOKEN trong biến môi trường.")
    client = HubClient(base, token)
    if args.cmd == "poll":
        result = client.poll(args.after, args.limit, args.wait)
    elif args.cmd == "agents":
        result = client.agents()
    elif args.cmd == "upload":
        result = client.upload(args.path, args.name)
    elif args.cmd == "download":
        client.download(args.id, args.dest)
        result = {"ok": True, "saved": args.dest}
    else:
        fields = {"to": args.to if args.to and len(args.to) > 1 else (args.to[0] if args.to else None),
                  "kind": args.kind, "task_id": args.task_id, "project": args.project, "title": args.title,
                  "thread_id": args.thread_id, "request_id": args.request_id, "reply_to": args.reply_to,
                  "files": [{"id": f} for f in args.file] if args.file else None}
        result = client.post(args.body, **{k: v for k, v in fields.items() if v is not None})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
