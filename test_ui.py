"""Test P3: phiên đăng nhập của anh dùng được API v1, /hub phục vụ giao diện, tail, chống CSRF."""
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import ai_hub
import api_v1
from hub_db import HubDB


class UiTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.hub = HubDB(str(self.dir / "hub.sqlite3"))
        self.addCleanup(self.hub.db.close)
        self.hub.register_agent("codex", "Codex", "push")
        self.hub.register_agent("muse", "Muse", "pull")
        self.session = True
        self.enterContext(patch.object(api_v1, "_hub", self.hub))
        self.enterContext(patch.dict(os.environ, {"AI_HUB_INTERNAL_TOKEN": "adm"}))
        self.enterContext(patch.object(ai_hub.Handler, "log_message"))
        self.enterContext(patch.object(ai_hub.Handler, "_authenticated", lambda handler: self.session))
        server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), ai_hub.Handler))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.url = "http://127.0.0.1:%d" % server.server_address[1]

    def call(self, path, method="GET", body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_session_cookie_acts_as_anh(self):
        status, raw = self.call("/api/v1/me")
        data = json.loads(raw)
        self.assertEqual((status, data["ok"], data["me"], data["admin"]), (200, True, "anh", True))
        self.assertGreater(data["limits"]["max_upload_bytes"], 0)
        status, raw = self.call("/api/v1/messages", "POST", {"to": "codex", "body": "xin chào"},
                                {"X-Mailbox-Request": "1"})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(raw)["message"]["from"], "anh")

    def test_session_write_needs_csrf_header(self):
        status, raw = self.call("/api/v1/messages", "POST", {"to": "codex", "body": "x"})
        self.assertEqual(status, 403)
        self.assertEqual(self.hub.read_all(), [])

    def test_no_session_no_token_is_401(self):
        self.session = False
        self.assertEqual(self.call("/api/v1/me")[0], 401)
        self.assertEqual(self.call("/api/v1/agents")[0], 401)

    def test_bad_bearer_does_not_fall_back_to_session(self):
        status, _ = self.call("/api/v1/me", headers={"Authorization": "Bearer ah1_sai"})
        self.assertEqual(status, 401)

    def test_agent_token_me(self):
        _, token = self.hub.register_agent("claude", "Claude", "push")
        self.session = False
        status, raw = self.call("/api/v1/me", headers={"Authorization": "Bearer " + token})
        self.assertEqual(json.loads(raw)["me"], "claude")
        self.assertFalse(json.loads(raw)["admin"])

    def test_tail_returns_latest_ascending(self):
        for i in range(8):
            self.hub.post("anh", "tin %d" % i, to="codex")
        status, raw = self.call("/api/v1/messages?tail=3")
        data = json.loads(raw)
        self.assertEqual([m["body"] for m in data["messages"]], ["tin 5", "tin 6", "tin 7"])
        self.assertEqual(data["last_id"], data["messages"][-1]["id"])
        self.assertEqual(json.loads(self.call("/api/v1/messages?tail=0")[1])["error"], "BAD_REQUEST")
        status, raw = self.call("/api/v1/messages?after=%d&limit=50" % data["last_id"])
        self.assertEqual(json.loads(raw)["messages"], [])

    def test_hub_page_served_and_protected(self):
        status, raw = self.call("/hub")
        self.assertEqual(status, 200)
        text = raw.decode("utf-8")
        self.assertIn("<title>AI Hub</title>", text)
        self.assertIn("/api/v1", text)
        self.session = False
        status, raw = self.call("/hub")
        self.assertEqual(status, 401)
        self.assertNotIn("api/v1", raw.decode("utf-8"))  # chưa đăng nhập thì không lộ giao diện
        self.session = True
        self.assertEqual(self.call("/")[0], 200)  # trang cũ vẫn phục vụ


if __name__ == "__main__":
    unittest.main()
