"""Test P5: upload/tải tệp đính kèm — giới hạn, kiểu nội dung an toàn, xác thực, đính kèm vào tin và việc."""
import contextlib
import hashlib
import http.client
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import agent_client
import ai_hub
import api_v1
import hub_files
import hub_tasks
from agent_client import HubClient
from hub_db import HubDB

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FileTests(unittest.TestCase):
    admin = "adm"

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.files_dir = self.dir / "files"
        self.hub = HubDB(str(self.dir / "hub.sqlite3"))
        self.addCleanup(self.hub.db.close)
        self.tokens = {a: self.hub.register_agent(a, a.title(), "push")[1] for a in ("codex", "muse")}
        self.board = hub_tasks.board_for(self.hub)
        self.enterContext(patch.object(api_v1, "_hub", self.hub))
        self.env = self.enterContext(patch.dict(os.environ, {
            "AI_HUB_INTERNAL_TOKEN": self.admin, "AI_HUB_FILES_DIR": str(self.files_dir)}))
        self.enterContext(patch.object(ai_hub.Handler, "log_message"))
        server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), ai_hub.Handler))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.root = HubClient(self.url, self.admin)
        self.codex, self.muse = HubClient(self.url, self.tokens["codex"]), HubClient(self.url, self.tokens["muse"])

    def get(self, path, token=None):
        request = urllib.request.Request(self.url + path)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def upload(self, name, data, client=None):
        reply = (client or self.codex).upload(data, name)
        self.assertTrue(reply["ok"], reply)
        return reply["file"]

    def stored(self):
        return [p.name for p in self.files_dir.iterdir()] if self.files_dir.exists() else []

    # ------------------------------------------------------------------ cơ bản
    def test_upload_download_roundtrip_is_byte_exact(self):
        data = os.urandom(300_000)  # nhiều chunk
        meta = self.upload("game.zip", data)
        self.assertEqual((meta["name"], meta["size"], meta["url"]), ("game.zip", len(data), "/api/v1/files/" + meta["id"]))
        status, headers, body = self.get(meta["url"], self.tokens["muse"])
        self.assertEqual((status, body), (200, data))
        self.assertEqual(hashlib.sha256(body).hexdigest(), hashlib.sha256(data).hexdigest())
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertEqual(self.stored(), [meta["id"]])

    def test_requires_auth_everywhere(self):
        meta = self.upload("a.txt", b"hello")
        self.assertEqual(self.get(meta["url"])[0], 401)
        self.assertEqual(self.get(meta["url"], "ah1_sai")[0], 401)
        self.assertEqual(HubClient(self.url, "ah1_sai").upload(b"x", "a.txt")["error"], "UNAUTHENTICATED")
        self.assertEqual(self.stored(), [meta["id"]])

    def test_session_cookie_can_view_and_needs_csrf_header_to_upload(self):
        meta = self.upload("shot.png", PNG)
        with patch.object(ai_hub.Handler, "_authenticated", lambda handler: True):
            self.assertEqual(self.get(meta["url"])[0], 200)  # <img src> của giao diện
            request = urllib.request.Request(self.url + "/api/v1/files?name=x.txt", data=b"abc", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 403)
            request.add_header("X-Mailbox-Request", "1")
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)["file"]["name"], "x.txt")

    def test_only_safe_types_render_inline(self):
        cases = {"a.png": (PNG, "image/png", "inline"), "fake.png": (b"<script>alert(1)</script>", "application/octet-stream", "attachment"),
                 "a.html": (b"<script>alert(1)</script>", "application/octet-stream", "attachment"),
                 "a.svg": (b"<svg onload=alert(1)/>", "application/octet-stream", "attachment"),
                 "a.pdf": (b"%PDF-1.4", "application/octet-stream", "attachment"),
                 "notes.md": ("# Xin chào".encode(), "text/plain; charset=utf-8", "inline")}
        for name, (data, mime, disposition) in cases.items():
            meta = self.upload(name, data)
            status, headers, _ = self.get(meta["url"], self.tokens["codex"])
            self.assertEqual((headers["Content-Type"], headers["Content-Disposition"].split(";")[0]), (mime, disposition), name)

    def test_filename_is_sanitised_and_unicode_survives(self):
        self.assertEqual(hub_files.clean_name("../../etc/passwd"), "passwd")
        self.assertEqual(hub_files.clean_name("C:\\Users\\a\\b.txt"), "b.txt")
        self.assertEqual(hub_files.clean_name(".hidden"), "hidden")
        self.assertEqual(hub_files.clean_name("a\x00b\n.txt"), "ab.txt")
        self.assertEqual(hub_files.clean_name(""), "tep")
        long = hub_files.clean_name("x" * 300 + ".json")
        self.assertTrue(len(long) <= 120 and long.endswith(".json"))
        meta = self.upload("../../evil.sh", b"echo")
        self.assertEqual(meta["name"], "evil.sh")
        self.assertEqual(self.stored(), [meta["id"]])  # không có gì ghi ra ngoài thư mục kho
        unicode_meta = self.upload("báo cáo ảnh.png", PNG)
        _, headers, _ = self.get(unicode_meta["url"], self.tokens["codex"])
        self.assertIn("filename*=UTF-8''b%C3%A1o%20c%C3%A1o%20%E1%BA%A3nh.png", headers["Content-Disposition"])
        self.assertNotIn("á", headers["Content-Disposition"])

    # ---------------------------------------------------------------- giới hạn
    def test_size_limit(self):
        self.env["AI_HUB_MAX_UPLOAD_MB"] = "0.001"
        self.assertEqual(self.codex.upload(b"x" * 5000, "big.bin")["error"], "PAYLOAD_TOO_LARGE")
        self.assertEqual(self.codex.upload(b"", "empty.txt")["error"], "BAD_REQUEST")
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.root.me()["limits"]["max_upload_bytes"], 1048)

    def test_quota(self):
        self.env["AI_HUB_FILES_QUOTA_MB"] = "0.002"  # ~2 KB
        self.upload("a.bin", b"x" * 1500)
        self.assertEqual(self.codex.upload(b"x" * 1500, "b.bin")["error"], "QUOTA_EXCEEDED")
        self.assertEqual(len(self.stored()), 1)

    def test_chunked_upload_is_refused(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.putrequest("POST", "/api/v1/files?name=a.txt")
        connection.putheader("Authorization", "Bearer " + self.tokens["codex"])
        connection.putheader("Transfer-Encoding", "chunked")
        connection.endheaders(b"5\r\nhello\r\n0\r\n\r\n")
        self.assertEqual(connection.getresponse().status, 411)
        self.assertEqual(self.stored(), [])

    def test_interrupted_upload_leaves_nothing_behind(self):
        with socket.create_connection(("127.0.0.1", self.port)) as sock:
            sock.sendall(("POST /api/v1/files?name=a.bin HTTP/1.0\r\nAuthorization: Bearer %s\r\nContent-Length: 1000\r\n\r\n"
                          % self.tokens["codex"]).encode() + b"x" * 10)
            sock.shutdown(socket.SHUT_WR)
            reply = sock.recv(4096).decode("utf-8", "replace")
        self.assertIn(" 408 ", reply.split("\r\n")[0])
        self.assertEqual((self.stored(), self.hub.list_files()), ([], []))

    def test_stale_temp_files_are_cleaned_at_startup(self):
        self.files_dir.mkdir()
        stale, fresh = self.files_dir / ".tmp-old", self.files_dir / ".tmp-new"
        stale.write_bytes(b"x"), fresh.write_bytes(b"x")
        os.utime(stale, (time.time() - 7200,) * 2)
        hub_files.FileStore(self.hub, self.files_dir)
        self.assertEqual(sorted(self.stored()), [".tmp-new"])

    def test_bad_ids_cannot_escape(self):
        for bad in ("..%2f..%2fetc%2fpasswd", "../x", "A" * 32, "0" * 31):
            self.assertEqual(self.get("/api/v1/files/" + bad, self.tokens["codex"])[0], 404, bad)
        self.assertEqual(self.get("/api/v1/files/" + "0" * 32, self.tokens["codex"])[0], 404)

    # ---------------------------------------------------- đính kèm vào tin và việc
    def test_message_attachments_are_resolved_from_the_database(self):
        meta = self.upload("shot.png", PNG)
        spoof = {"id": meta["id"], "name": "evil.exe", "url": "http://evil.test/x", "size": 1}
        sent = self.codex.post("Ảnh chụp", to="muse", kind="result", files=[spoof, {"name": "doc", "url": "https://x.test/d"}])
        self.assertTrue(sent["ok"], sent)
        got = self.muse.poll()["messages"][0]["files"]
        self.assertEqual(got[0], {"id": meta["id"], "name": "shot.png", "size": len(PNG), "mime": "image/png", "url": meta["url"]})
        self.assertEqual(got[1], {"url": "https://x.test/d", "name": "doc"})
        for bad in ([{"id": "0" * 32}], [{"id": 5}], [{"url": "javascript:alert(1)"}], [{"name": "x"}], ["x"]):
            reply = self.codex.post("x", to="muse", files=bad)
            self.assertEqual(reply["error"], "BAD_REQUEST", bad)

    def test_task_handoff_carries_uploaded_files(self):
        meta = self.upload("mines.zip", b"PK\x03\x04" + b"0" * 20)
        self.board.create("anh", "Làm game", "codex", reviewer="muse", task_id="G1", description="Làm game")
        self.board.transition("codex", "G1", "in_progress")
        done = self.codex.transition("G1", "in_review", "Xong", files=[{"id": meta["id"]}])
        self.assertTrue(done["ok"], done)
        handoff = [m for m in self.muse.poll()["messages"] if m["kind"] == "handoff"][0]
        self.assertEqual(handoff["files"][0]["id"], meta["id"])
        out = self.dir / "got.zip"
        self.muse.download(handoff["files"][0]["id"], out)
        self.assertEqual(out.read_bytes(), b"PK\x03\x04" + b"0" * 20)

    # ------------------------------------------------------------------ quản trị
    def test_admin_list_and_delete(self):
        meta = self.upload("a.txt", b"hello")
        self.assertEqual(self.codex._request("GET", "/api/v1/files")["error"], "FORBIDDEN")
        listing = self.root._request("GET", "/api/v1/files")
        self.assertEqual((listing["files"][0]["id"], listing["files"][0]["uploader"], listing["used_bytes"]),
                         (meta["id"], "codex", 5))
        self.assertEqual(self.muse._request("POST", "/api/v1/files/%s/delete" % meta["id"], {})["error"], "FORBIDDEN")
        self.assertTrue(self.root._request("POST", "/api/v1/files/%s/delete" % meta["id"], {})["ok"])
        self.assertEqual((self.stored(), self.get(meta["url"], self.tokens["muse"])[0]), ([], 404))
        self.assertEqual(self.root._request("GET", "/api/v1/files")["used_bytes"], 0)

    # ------------------------------------------------------------------- client
    def test_client_and_cli(self):
        source = self.dir / "ket-qua.txt"
        source.write_text("Kết quả", encoding="utf-8")
        meta = self.codex.upload(str(source))["file"]
        self.assertEqual(meta["name"], "ket-qua.txt")
        with self.assertRaises(OSError):
            self.codex.download("0" * 32, self.dir / "none.bin")
        self.assertFalse((self.dir / "none.bin.part").exists())
        with patch.dict(os.environ, {"AI_HUB_URL": self.url, "AI_HUB_TOKEN": self.tokens["muse"]}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(agent_client.main(["post", "--to", "codex", "--file", meta["id"], "Đây là tệp"]), 0)
                self.assertEqual(agent_client.main(["download", meta["id"], str(self.dir / "copy.txt")]), 0)
        self.assertEqual((self.dir / "copy.txt").read_text(encoding="utf-8"), "Kết quả")
        self.assertEqual(self.codex.poll()["messages"][0]["files"][0]["id"], meta["id"])


if __name__ == "__main__":
    unittest.main()
