"""Integration tests against temporary loopback servers and simulated agent work."""
import os
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import ai_hub
import emi_worker
from mailbox_store import MessageStore
from room_dispatch import Dispatch, request_json, sync_room
from tinker_room_listener import Inbox, step, wake_handler
from tinker_worker import deliver


class RoomTests(unittest.TestCase):
    token = "test-internal"
    wake_token = "test-wake"

    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory(dir=emi_worker.BASE))
        self.folder = Path(temp)
        self.store = MessageStore(str(self.folder / "log.json"))
        self.enterContext(patch.object(ai_hub, "MAILBOX", self.store))
        self.enterContext(patch.dict(os.environ, {"AI_HUB_INTERNAL_TOKEN": self.token}))
        self.enterContext(patch.object(ai_hub.Handler, "_require_session", return_value=True))
        self.enterContext(patch.object(ai_hub.Handler, "log_message"))
        self.api = self.serve(ai_hub.Handler)
        self.enterContext(patch.object(emi_worker, "API", self.api))
        self.enterContext(patch.object(emi_worker, "log"))
        self.dispatch = Dispatch(self.folder / "dispatch.sqlite3")

    def serve(self, handler):
        server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), handler))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def human(self, body, **extra):
        import json
        from urllib.request import Request, urlopen
        request = Request(self.api + "/api/message", data=json.dumps({"body": body, **extra}).encode(),
                          headers={"Content-Type": "application/json", "X-Mailbox-Request": "1"})
        with urlopen(request, timeout=4) as response:
            return json.load(response)["message"]

    def post(self, **kwargs):
        return request_json(self.api + "/internal/message", self.token, kwargs)["message"]

    def sync(self, wait=0):
        sync_room(self.dispatch, self.api, self.token, lambda _: None, wait=wait)

    def emi(self, output):
        runs = []

        def fake_run(task, report):
            runs.append(task["body"])
            Path(report).write_text("Báo cáo đầy đủ", encoding="utf-8")
            Path(report + ".reply.txt").write_text(output, encoding="utf-8")
            return True, ""

        with patch.dict(emi_worker.PROJECTS, {"ai-hub": self.folder}), patch.object(
                emi_worker, "run_codex", side_effect=fake_run):
            emi_worker.handle_room_job(self.dispatch, self.token, self.dispatch.next_job("emi", reserve=True))
        return runs

    def test_01_round_trip(self):
        start = time.perf_counter()
        msg = self.human("Emi, giải thích cơ chế long-poll")
        self.assertNotIn("to", msg)
        self.sync()
        self.assertEqual(self.emi("Worker chờ tin trên kết nối đang mở."), [msg["body"]])
        reply = request_json(self.api + "/internal/messages", self.token)["messages"][-1]
        self.assertEqual((reply["from"], reply["reply_to"], reply["kind"]), ("emi", msg["id"], "result"))
        print(f"Round trip {1000*(time.perf_counter()-start):.1f} ms")

    def test_02_idle_wakeup(self):
        with ThreadPoolExecutor(1) as pool:
            listening = threading.Event()
            read = self.store.read
            def observed_read(*args, **kwargs):
                listening.set()
                return read(*args, **kwargs)
            with patch.object(self.store, "read", side_effect=observed_read):
                waiting = pool.submit(self.sync, 4)
                self.assertTrue(listening.wait(2))
                start = time.perf_counter()
                self.human("@sol phản hồi khi nhận được tin.")
                waiting.result(timeout=2)
            print(f"Wake to inbox {1000*(time.perf_counter()-start):.1f} ms")
        self.emi("Em đã nhận tin.")
        delay = time.perf_counter()-start
        self.assertLess(delay, 60)
        print(f"Wake with simulated Emi reply {1000*delay:.1f} ms")

    def test_03_filter_context(self):
        start = time.perf_counter()
        wanted = self.human("@tinker kiểm tra máy ảo, Emi chỉ được nhắc tới.")
        self.human("@emi cảm ơn.")
        self.human("Thông báo hôm nay trời nắng.")
        self.human("Máy ảo có chạy không?")
        self.human("Còn việc này thì sao?")
        self.sync()
        self.assertIsNone(self.dispatch.next_job("emi"))
        self.assertEqual(self.dispatch.next_job("tinker")[0], wanted["id"])
        print(f"Filter and context {1000*(time.perf_counter()-start):.1f} ms")

    def test_04_handoff(self):
        inbox_path = self.folder / "vm.sqlite3"
        inbox = Inbox(inbox_path)
        wake = self.serve(wake_handler(inbox, self.wake_token)) + "/wake"
        start = time.perf_counter()
        first = self.post(**{"from": "tinker", "body": "@emi nhân đôi 37", "kind": "handoff"})
        self.sync()
        self.emi("HANDOFF\n@tinker 37 × 2 = 74. Nhờ kiểm chứng.")
        self.sync()
        job = self.dispatch.next_job("tinker")
        with patch("tinker_worker.log"):
            deliver(self.dispatch, wake, self.wake_token, job)
            deliver(self.dispatch, wake, self.wake_token, job)
        reopened = Inbox(inbox_path)
        ran = []
        def verify(task):
            ran.append(task["message"]["id"])
            self.assertIn("37 × 2 = 74", task["message"]["body"])
            self.assertEqual(task["message"]["reply_to"], first["id"])
            return {"body": f"Đã kiểm chứng: 37 × 2 = {37*2}."}
        self.assertTrue(step(reopened, verify, self.api, self.token))
        self.assertFalse(step(reopened, verify, self.api, self.token))
        self.sync()
        self.assertIsNone(self.dispatch.next_job("emi"))
        self.assertEqual(len(ran), 1)
        self.assertIn("Đã kiểm chứng: 37 × 2 = 74.", self.store.read()[-1]["body"])
        print(f"Handoff with simulated agents and VM inbox reopen {1000*(time.perf_counter()-start):.1f} ms")

    def test_05_dedupe_restart_and_concurrency(self):
        start = time.perf_counter()
        msg = self.human("@emi xem giúp")
        with ThreadPoolExecutor(2) as executor:
            list(executor.map(lambda _: self.sync(), range(2)))
        reopened = Dispatch(self.folder / "dispatch.sqlite3")
        self.assertEqual(self.emi("Đây là kết quả."), [msg["body"]])
        self.assertIsNone(reopened.next_job("emi"))
        retry = self.post(**{"from": "emi", "reply_to": msg["id"], "body": "Kết quả thứ hai"})
        self.assertIn("Đây là kết quả.", retry["body"])
        self.assertEqual(len(self.store.read()), 2)
        print(f"Concurrent routing and duplicate post {1000*(time.perf_counter()-start):.1f} ms")

    def test_hard_cap_and_reset(self):
        for i in range(8):
            sender = "emi" if i % 2 == 0 else "tinker"
            other = "tinker" if sender == "emi" else "emi"
            row = self.post(**{"from": sender, "body": f"@{other} phần {i}"})
        self.sync()
        with self.dispatch.transaction() as db:
            self.assertEqual(db.execute("SELECT reason FROM decisions WHERE id=?", (row["id"],)).fetchone(),
                             ("chot-8",))
        self.assertIsNone(self.dispatch.next_job("tinker"))
        self.assertIsNone(self.dispatch.next_job("emi"))
        with self.assertRaises(HTTPError) as err:
            self.post(**{"from": "tinker", "body": "Lượt 9"})
        err.exception.close()
        self.assertEqual(err.exception.code, 409)
        self.human("Tiếp tục.")
        row = self.post(**{"from": "tinker", "body": "Lượt sau khi anh lên tiếng"})
        self.assertEqual(row["agent_turns"], 1)

    def test_reply_reference_and_summary_limits(self):
        item = self.human("@tinker kiểm tra máy ảo")
        for i in range(27):
            self.human("Tin thông báo " + str(i) + " " + "a" * 500)
        followup = self.human("Phần cấu hình", reply_to=item["id"])
        self.sync()
        self.dispatch.finish(item["id"], "tinker", "delivered")
        mid, _, task, _ = self.dispatch.next_job("tinker")
        self.assertEqual(mid, followup["id"])
        self.assertEqual(len(task["recent"]), 8)
        self.assertEqual(len(task["summary"]), 16)
        self.assertEqual(task["open_tasks"][0]["id"], item["id"])

    def test_legacy_filter_and_auth(self):
        item = self.post(**{"from": "tinker", "to": "emi", "body": "Việc theo giao diện cũ"})
        self.assertEqual(request_json(self.api + "/internal/messages?to=emi", self.token)["messages"], [item])
        with self.assertRaises(HTTPError) as err:
            request_json(self.api + "/internal/messages", "wrong")
        err.exception.close()
        self.assertEqual(err.exception.code, 401)

    def test_outbox_survives_failed_post(self):
        msg = self.human("@emi xem giúp")
        self.sync()
        with patch.object(emi_worker, "request_json", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                self.emi("Đây là kết quả sau khi mạng lên.")
        reopened = Dispatch(self.folder / "dispatch.sqlite3")
        self.assertIsNone(reopened.next_job("emi"))
        with reopened.transaction() as db:
            db.execute("UPDATE jobs SET result=json_set(result, '$._retry_at', 0) WHERE state='outbox'")
        with patch.object(emi_worker, "run_codex", side_effect=AssertionError("trùng lượt Codex")):
            emi_worker.handle_room_job(reopened, self.token, reopened.next_job("emi", reserve=True))
        self.assertEqual(self.store.read()[-1]["reply_to"], msg["id"])
        self.assertEqual(len(self.store.read()), 2)

    def test_report_empty_targets_reaches_anh(self):
        self.human("@emi ki?m tra b?o c?o")
        self.sync()
        self.emi("?? ki?m tra. Xem b?o c?o chi ti?t.")
        self.sync()
        rows = request_json(self.api + "/internal/messages?to=anh", self.token)["messages"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["from"], "emi")
        with self.dispatch.transaction() as db:
            self.assertEqual(db.execute("SELECT targets FROM decisions WHERE id=?", (rows[0]["id"],)).fetchone()[0], "[]")

    def test_phase_self_reply_and_invalid_outbox_terminate(self):
        root = self.human("@emi th?c hi?n k? ho?ch")
        plan = self.post(**{"from":"emi", "reply_to":root["id"], "kind":"handoff",
                            "body":'/phase plan regression ["ki?m ch?ng"]'})
        self.sync()
        self.dispatch.finish(root["id"], "emi", "done")
        self.emi("HANDOFF\n/phase done regression 1\n@tinker ki?m ch?ng b?o c?o.")
        rows = request_json(self.api + "/internal/messages?to=anh", self.token)["messages"]
        self.assertEqual(rows[-1]["reply_to"], root["id"])
        self.assertNotEqual(rows[-1]["reply_to"], plan["id"])
        self.assertIsNone(self.dispatch.next_job("emi"))
        # Invalid data is terminal, including when recovered from a persisted outbox.
        self.dispatch.finish(plan["id"], "emi", "outbox", {"from":"emi", "body":"", "kind":"result"})
        emi_worker.handle_room_job(self.dispatch, self.token, self.dispatch.next_job("emi"))
        self.assertIsNone(self.dispatch.next_job("emi"))
        with self.dispatch.transaction() as db:
            self.assertEqual(db.execute("SELECT state,closed FROM jobs WHERE id=? AND agent='emi'", (plan["id"],)).fetchone(), ("blocked",1))
        stale = self.post(**{"from":"emi", "to":"anh", "kind":"result", "reply_to":"missing", "body":"B?o c?o tham chi?u c?", "ref":"stale.md"})
        self.assertNotIn("reply_to", stale)

    def test_report_when_phase_turn_blocked(self):
        self.human("@emi ki?m ch?ng ch?t l??t")
        self.sync()
        for i in range(8):
            self.post(**{"from":"tinker" if i%2 else "emi", "body":f"Trao ??i {i}"})
        self.sync()
        with self.assertRaises(HTTPError) as err:
            self.post(**{"from":"emi", "kind":"handoff", "body":"/phase done capped 1"})
        err.exception.close()
        self.assertEqual(err.exception.code,409)
        # Work completed before cap: an outbox result must still reach the owner.
        with self.dispatch.transaction() as db:
            mid = db.execute("SELECT id FROM jobs WHERE agent='emi'").fetchone()[0]
        self.dispatch.finish(mid,"emi","outbox", {"from":"emi", "kind":"handoff",
                             "body":"/phase done capped 1\n?? ghi b?o c?o", "ref":"capped.md"})
        emi_worker.handle_room_job(self.dispatch,self.token,self.dispatch.next_job("emi"))
        rows = request_json(self.api+"/internal/messages?to=anh",self.token)["messages"]
        self.assertEqual(rows[-1]["ref"],"capped.md")
        self.assertEqual(rows[-1]["agent_turns"],8)
        self.assertIsNone(self.dispatch.next_job("emi"))


if __name__ == "__main__":
    unittest.main()
