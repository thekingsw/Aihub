"""Test P1: danh bạ agent, token riêng, API v1, chống vòng lặp, long-poll."""
import os
import sqlite3
import tempfile
import threading
import time
import urllib.error
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import agent_client
import ai_hub
import api_v1
import hub_db
from hub_db import HubDB, HubError, Conflict, Forbidden


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.hub = HubDB(str(self.dir / "hub.sqlite3"))
        self.addCleanup(self.hub.db.close)
        _, self.codex_token = self.hub.register_agent("codex", "Codex", "push", "Làm việc")
        _, self.muse_token = self.hub.register_agent("muse", "Muse", "pull", "Test", poll_interval=300)

    def test_token_stored_only_as_hash(self):
        rows = self.hub.db.execute("SELECT token_hash FROM agents").fetchall()
        for row in rows:
            self.assertNotIn(self.codex_token, row["token_hash"])
            self.assertEqual(len(row["token_hash"]), 64)
        self.assertEqual(self.hub.authenticate(self.codex_token)["id"], "codex")
        self.assertIsNone(self.hub.authenticate("ah1_sai"))
        self.assertIsNone(self.hub.authenticate(None))

    def test_register_validation(self):
        for bad in ("A", "anh", "all", "x", "1abc", "co space"):
            with self.assertRaises(HubError, msg=bad):
                self.hub.register_agent(bad, "X", "push")
        with self.assertRaises(Conflict):
            self.hub.register_agent("codex", "Codex 2", "push")
        with self.assertRaises(HubError):
            self.hub.register_agent("claude", "Claude", "magic")

    def test_rotate_and_disable(self):
        new = self.hub.rotate_token("muse")
        self.assertIsNone(self.hub.authenticate(self.muse_token))
        self.assertEqual(self.hub.authenticate(new)["id"], "muse")
        self.hub.set_enabled("muse", False)
        self.assertIsNone(self.hub.authenticate(new))
        self.hub.set_enabled("muse", True)
        self.assertIsNotNone(self.hub.authenticate(new))

    def test_watermark_and_recipients(self):
        a = self.hub.post("anh", "gửi codex", to="codex", kind="task", task_id="T1")
        b = self.hub.post("anh", "gửi muse", to="muse")
        c = self.hub.post("anh", "gửi tất cả", to="all")
        self.assertTrue(a["id"] < b["id"] < c["id"])
        self.assertEqual([m["id"] for m in self.hub.read_for("codex")], [a["id"], c["id"]])
        self.assertEqual([m["id"] for m in self.hub.read_for("muse", after=a["id"])], [b["id"], c["id"]])
        self.assertEqual(self.hub.read_for("muse", after=c["id"]), [])

    def test_sender_does_not_see_own_broadcast(self):
        self.hub.post("anh", "mở đầu", to="all")
        self.hub.post("codex", "tôi đây", to="all")
        self.assertEqual([m["from"] for m in self.hub.read_for("codex")], ["anh"])
        self.assertEqual([m["from"] for m in self.hub.read_for("muse")], ["anh", "codex"])

    def test_reply_defaults_and_thread(self):
        task = self.hub.post("anh", "làm game", to="codex", kind="task", task_id="GAME-1")
        self.assertEqual(task["thread_id"], "GAME-1")
        reply = self.hub.post("codex", "xong", reply_to=task["id"], kind="result")
        self.assertEqual(reply["to"], ["anh"])
        self.assertEqual(reply["thread_id"], "GAME-1")
        with self.assertRaises(Conflict):
            self.hub.post("codex", "tự trả lời", reply_to=reply["id"])
        with self.assertRaises(HubError):
            self.hub.post("anh", "x", reply_to=999)

    def test_request_id_is_idempotent(self):
        first = self.hub.post("muse", "báo cáo", to="anh", request_id="r1")
        second = self.hub.post("muse", "báo cáo", to="anh", request_id="r1")
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.hub.read_all()), 1)

    def test_validation(self):
        with self.assertRaises(HubError):
            self.hub.post("anh", "   ", to="codex")
        with self.assertRaises(HubError):
            self.hub.post("anh", "x" * 20001, to="codex")
        with self.assertRaises(HubError):
            self.hub.post("anh", "x", to="ghost")
        with self.assertRaises(HubError):
            self.hub.post("anh", "x", to="codex", kind="spam")
        with self.assertRaises(Forbidden):
            self.hub.post("ghost", "x", to="anh")
        with self.assertRaises(HubError):
            self.hub.post("codex", "x", to="codex")
        with self.assertRaises(HubError):
            self.hub.read_for("codex", after="1")
        with self.assertRaises(HubError):
            self.hub.read_for("codex", limit=0)

    def test_agent_turn_limit_resets_per_thread_and_by_human(self):
        for i in range(8):
            sender, to = ("codex", "muse") if i % 2 == 0 else ("muse", "codex")
            self.hub.post(sender, "lượt %d" % i, to=to, thread_id="t1")
        with self.assertRaises(Conflict):
            self.hub.post("codex", "lượt 9", to="muse", thread_id="t1")
        self.hub.post("codex", "luồng khác", to="muse", thread_id="t2")  # luồng khác không bị chặn
        self.hub.post("anh", "tiếp đi", to="all", thread_id="t1")
        self.hub.post("codex", "được rồi", to="muse", thread_id="t1")

    def test_result_to_anh_is_exempt_and_duplicate_body_blocked(self):
        for i in range(10):
            self.hub.post("codex", "báo cáo %d" % i, to="anh", kind="result", thread_id="t1")
        self.hub.post("codex", "Lặp lại", to="muse", thread_id="t3")
        with self.assertRaises(Conflict):
            self.hub.post("codex", "  lặp   LẠI ", to="muse", thread_id="t3")

    def test_online_status(self):
        self.assertFalse({a["id"]: a for a in self.hub.list_agents()}["muse"]["online"])
        self.hub.authenticate(self.muse_token)
        agents = {a["id"]: a for a in self.hub.list_agents()}
        self.assertTrue(agents["muse"]["online"])
        self.assertFalse(agents["codex"]["online"])

    def test_wait_returns_on_new_message(self):
        result = {}

        def waiter():
            result["rows"] = self.hub.wait_for("codex", 0, 10, 5)

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(.2)
        self.hub.post("anh", "dậy đi", to="codex")
        thread.join(3)
        self.assertEqual([m["body"] for m in result["rows"]], ["dậy đi"])
        started = time.monotonic()
        self.assertEqual(self.hub.wait_for("codex", result["rows"][0]["id"], 10, 0.3), [])
        self.assertLess(time.monotonic() - started, 2)


class ApiTests(unittest.TestCase):
    admin = "test-internal"

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.hub = HubDB(str(self.dir / "hub.sqlite3"))
        self.addCleanup(self.hub.db.close)
        self.enterContext(patch.object(api_v1, "_hub", self.hub))
        self.enterContext(patch.dict(os.environ, {"AI_HUB_INTERNAL_TOKEN": self.admin}))
        self.enterContext(patch.object(ai_hub.Handler, "log_message"))
        server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), ai_hub.Handler))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.url = "http://127.0.0.1:%d" % server.server_address[1]
        self.root = agent_client.HubClient(self.url, self.admin)
        self.codex_token = self.create("codex", "push")
        self.muse_token = self.create("muse", "pull")
        self.codex = agent_client.HubClient(self.url, self.codex_token)
        self.muse = agent_client.HubClient(self.url, self.muse_token)

    def create(self, agent_id, mode):
        result = self.root._request("POST", "/api/v1/agents", {"id": agent_id, "name": agent_id.title(), "mode": mode})
        self.assertTrue(result["ok"], result)
        return result["token"]

    def test_requires_valid_token(self):
        for token in ("", "ah1_sai", "x"):
            client = agent_client.HubClient(self.url, token)
            result = client.poll()
            self.assertEqual(result["error"], "UNAUTHENTICATED")
        self.assertEqual(self.muse._request("GET", "/api/v1/nope")["error"], "NOT_FOUND")

    def test_full_round_trip_task_report_review(self):
        task = self.root._request("POST", "/api/v1/messages", {
            "to": "codex", "kind": "task", "task_id": "GAME-1", "project": "retro",
            "title": "Làm game dò mìn", "body": "Làm game dò mìn bằng HTML."})
        self.assertEqual(task["message"]["from"], "anh")
        got = self.codex.poll(after=0)
        self.assertEqual([m["task_id"] for m in got["messages"]], ["GAME-1"])
        report = self.codex.post("Xong, file ở /games/mines.html", to=["anh", "muse"], kind="result",
                                 reply_to=task["message"]["id"])
        self.assertTrue(report["ok"], report)
        muse_msgs = self.muse.poll(after=0)["messages"]
        self.assertEqual([m["from"] for m in muse_msgs], ["codex"])
        review = self.muse.post("Test xong: nút cờ lỗi.", kind="review", reply_to=muse_msgs[0]["id"])
        self.assertEqual(review["message"]["to"], ["codex"])
        self.assertEqual(review["message"]["thread_id"], "GAME-1")
        self.assertEqual(self.root._request("GET", "/api/v1/messages?thread_id=GAME-1")["last_id"],
                         review["message"]["id"])
        self.assertEqual(len(self.codex.poll(after=got["last_id"])["messages"]), 1)

    def test_sender_cannot_be_spoofed(self):
        spoof = self.muse.post("giả danh", to="anh", **{"from": "codex"})
        self.assertEqual(spoof["error"], "FORBIDDEN")
        self.assertEqual(self.muse.post("ok", to="anh", **{"from": "muse"})["message"]["from"], "muse")
        self.assertEqual(self.hub.read_all(), [self.hub.read_all()[0]])

    def test_agent_cannot_use_admin_routes_or_read_others_mail(self):
        self.assertEqual(self.muse._request("POST", "/api/v1/agents", {"id": "evil", "name": "E", "mode": "push"})["error"],
                         "FORBIDDEN")
        self.assertEqual(self.muse._request("POST", "/api/v1/agents/codex/rotate", {})["error"], "FORBIDDEN")
        self.root._request("POST", "/api/v1/messages", {"to": "codex", "body": "riêng tư"})
        self.assertEqual(self.muse.poll()["messages"], [])

    def test_rotate_and_disable_over_http(self):
        new = self.root._request("POST", "/api/v1/agents/muse/rotate", {})["token"]
        self.assertEqual(self.muse.poll()["error"], "UNAUTHENTICATED")
        self.assertTrue(agent_client.HubClient(self.url, new).poll()["ok"])
        self.root._request("POST", "/api/v1/agents/muse/disable", {})
        self.assertEqual(agent_client.HubClient(self.url, new).poll()["error"], "UNAUTHENTICATED")

    def test_bad_requests(self):
        self.assertEqual(self.codex.post("")["error"], "BAD_REQUEST")
        self.assertEqual(self.codex.post("x", to="ghost")["error"], "BAD_REQUEST")
        self.assertEqual(self.codex._request("GET", "/api/v1/messages?after=abc")["error"], "BAD_REQUEST")
        self.assertEqual(self.codex._request("GET", "/api/v1/messages?wait=abc")["error"], "BAD_REQUEST")
        import urllib.request
        request = urllib.request.Request(self.url + "/api/v1/messages", data=b"body=x", method="POST")
        request.add_header("Authorization", "Bearer " + self.codex_token)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 415)

    def test_idempotent_retry_and_status_codes(self):
        first = self.muse.post("báo cáo", to="anh", request_id="abc")
        again = self.muse.post("báo cáo", to="anh", request_id="abc")
        self.assertEqual(first["message"]["id"], again["message"]["id"])
        self.assertTrue(again["message"]["duplicate"])

    def test_long_poll_over_http(self):
        start = self.codex.poll()["last_id"]
        threading.Timer(.3, lambda: self.root._request(
            "POST", "/api/v1/messages", {"to": "codex", "body": "việc mới"})).start()
        started = time.monotonic()
        result = self.codex.poll(after=start, wait=5)
        self.assertEqual([m["body"] for m in result["messages"]], ["việc mới"])
        self.assertLess(time.monotonic() - started, 3)

    def test_legacy_endpoint_still_routed(self):
        import urllib.request
        with urllib.request.urlopen(self.url + "/api/status") as response:
            self.assertEqual(response.status, 200)

    def test_agents_list_shows_online(self):
        self.muse.poll()
        agents = {a["id"]: a for a in self.codex.agents()["agents"]}
        self.assertTrue(agents["muse"]["online"])
        self.assertNotIn("token_hash", agents["muse"])


if __name__ == "__main__":
    unittest.main()
