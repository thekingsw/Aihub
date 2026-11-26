"""Test P6: nhập lịch sử hộp thư cũ (chỉ để xem, không bao giờ giao cho agent), dọn tệp, trang /legacy."""
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import agent_client
import ai_hub
import api_v1
import hub_files
import hub_legacy
import hub_tasks
import import_legacy
from agent_client import HubClient
from hub_db import HubDB, HubError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def old_rows():
    return [
        {"id": "20261120T090000-aaaaaaaaaaaa", "from": "anh", "to": "tinker", "body": "Làm game dò mìn nhé",
         "created_at": "2026-11-20T02:00:00.123456+00:00", "agent_turns": 0},
        {"id": "20261120T090100-bbbbbbbbbbbb", "from": "tinker", "to": "emi", "body": "Sol làm giúp", "kind": "handoff",
         "reply_to": "20261120T090000-aaaaaaaaaaaa", "created_at": "2026-11-20T02:01:00+00:00", "agent_turns": 1},
        {"id": "20261120T091000-cccccccccccc", "from": "emi", "to": "anh", "body": "Xong", "kind": "result",
         "ref": "emi-report-1.md", "reply_to": "20261120T090100-bbbbbbbbbbbb",
         "created_at": "2026-11-20T09:10:00+07:00", "agent_turns": 1},
        {"id": "20261120T092000-dddddddddddd", "from": "anh", "body": "Cảm ơn cả hai", "created_at": "2026-11-20T02:20:00+00:00"},
    ]


class LegacyStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.hub = HubDB(str(self.dir / "hub.sqlite3"))
        self.addCleanup(self.hub.db.close)
        self.hub.register_agent("muse", "Muse", "pull")
        self.hub.register_agent("codex", "Codex", "push")

    def test_import_maps_names_and_keeps_threads_and_references(self):
        stats = hub_legacy.import_rows(self.hub, old_rows())
        self.assertEqual((stats["imported"], stats["already_imported"], stats["invalid"]), (4, 0, 0))
        rows = self.hub.read_all(thread_id="legacy")
        self.assertEqual([(m["from"], m["to"], m["kind"]) for m in rows],
                         [("anh", ["muse"], "message"), ("muse", ["emi"], "handoff"), ("emi", ["anh"], "result"),
                          ("anh", ["all"], "message")])
        self.assertTrue(all(m["legacy"] and m["thread_id"] == "legacy" for m in rows))
        self.assertEqual((rows[1]["reply_to"], rows[2]["reply_to"]), (rows[0]["id"], rows[1]["id"]))
        self.assertIn("📄 Tài liệu: emi-report-1.md", rows[2]["body"])
        self.assertEqual(rows[0]["created_at"], "2026-11-20T02:00:00+00:00")
        self.assertEqual(rows[2]["created_at"], "2026-11-20T02:10:00+00:00")  # +07:00 đổi sang UTC

    def test_custom_mapping(self):
        hub_legacy.import_rows(self.hub, old_rows(), hub_legacy.parse_map(["emi=codex", "tinker=claudeqwen"]))
        senders = {m["from"] for m in self.hub.read_all(thread_id="legacy")}
        self.assertEqual(senders, {"anh", "claudeqwen", "codex"})
        for bad in ("emi", "emi=", "=x", "EMI=codex", "a=b c"):
            with self.assertRaises(HubError, msg=bad):
                hub_legacy.parse_map([bad])

    def test_import_is_idempotent_and_incremental(self):
        rows = old_rows()
        hub_legacy.import_rows(self.hub, rows[:2])
        again = hub_legacy.import_rows(self.hub, rows)
        self.assertEqual((again["imported"], again["already_imported"]), (2, 2))
        self.assertEqual(len(self.hub.read_all(thread_id="legacy")), 4)
        self.assertEqual(hub_legacy.import_rows(self.hub, rows)["imported"], 0)

    def test_dry_run_writes_nothing_and_bad_rows_are_reported(self):
        rows = old_rows() + [{"id": "x"}, "xin chào", {"id": "y", "from": "anh", "body": " ", "created_at": "2026-01-01T00:00:00+00:00"},
                             {"id": "z", "from": "anh", "body": "ok", "created_at": "không phải ngày"}]
        stats = hub_legacy.import_rows(self.hub, rows, dry_run=True)
        self.assertEqual((stats["imported"], stats["invalid"], len(stats["problems"])), (4, 4, 4))
        self.assertEqual(self.hub.read_all(thread_id="legacy"), [])

    def test_legacy_messages_are_never_delivered_to_agents(self):
        hub_legacy.import_rows(self.hub, old_rows(), hub_legacy.parse_map(["emi=codex"]))
        for agent in ("muse", "codex"):
            self.assertEqual(self.hub.read_for(agent, after=0), [], agent)
            self.assertEqual(self.hub.wait_for(agent, 0, 10, 0.2), [], agent)
        legacy_to_all = [{"id": "w", "from": "anh", "to": "all", "body": "cả phòng", "created_at": "2026-11-20T00:00:00+00:00"}]
        hub_legacy.import_rows(self.hub, legacy_to_all)
        self.assertEqual(self.hub.read_for("muse"), [])
        self.hub.post("anh", "tin mới thật", to="codex")  # tin thật vẫn đến bình thường
        self.assertEqual([m["body"] for m in self.hub.read_for("codex")], ["tin mới thật"])

    def test_default_feed_excludes_legacy_and_tail_works_per_thread(self):
        hub_legacy.import_rows(self.hub, old_rows())
        live = self.hub.post("anh", "tin thật", to="muse")
        self.assertEqual([m["id"] for m in self.hub.read_all()], [live["id"]])
        self.assertEqual([m["id"] for m in self.hub.read_all(tail=5)], [live["id"]])
        tail = self.hub.read_all(thread_id="legacy", tail=2)
        self.assertEqual([m["body"] for m in tail], ["Xong\n\n📄 Tài liệu: emi-report-1.md", "Cảm ơn cả hai"])

    def test_legacy_thread_is_reserved_and_does_not_affect_turn_limits(self):
        hub_legacy.import_rows(self.hub, old_rows())
        with self.assertRaises(HubError):
            self.hub.post("anh", "x", to="muse", thread_id="legacy")
        with self.assertRaises(HubError):
            self.hub.post("anh", "x", to="muse", task_id="LEGACY")
        with self.assertRaises(HubError):
            hub_tasks.board_for(self.hub).create("anh", "x", "muse", task_id="legacy")
        for i in range(8):
            self.hub.post("muse" if i % 2 == 0 else "codex", "lượt %d" % i, to="codex" if i % 2 == 0 else "muse", thread_id="t1")
        with self.assertRaises(HubError):  # vẫn chặn đúng 8 lượt; tin anh trong luồng legacy không "mở khoá" luồng khác
            self.hub.post("muse", "lượt 9", to="codex", thread_id="t1")

    def test_existing_database_is_migrated_in_place(self):
        path = str(self.dir / "old.sqlite3")
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE agents(id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
                poll_interval INTEGER NOT NULL DEFAULT 300, token_hash TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, last_seen REAL);
            CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, sender TEXT NOT NULL,
                is_human INTEGER NOT NULL, turn INTEGER NOT NULL, kind TEXT NOT NULL, task_id TEXT, project TEXT, title TEXT,
                body TEXT NOT NULL, reply_to INTEGER, thread_id TEXT NOT NULL, request_id TEXT, files TEXT);
            CREATE TABLE msg_recipients(message_id INTEGER NOT NULL, agent TEXT NOT NULL, PRIMARY KEY(message_id, agent));
            INSERT INTO messages(created_at,sender,is_human,turn,kind,body,thread_id) VALUES('2026-11-01T00:00:00+00:00','anh',1,0,'message','cũ','main');
            INSERT INTO msg_recipients VALUES(1,'all');
        """)
        old.commit()
        old.close()
        hub = HubDB(path)
        self.addCleanup(hub.db.close)
        self.assertEqual([(m["body"], "legacy" in m) for m in hub.read_all()], [("cũ", False)])
        HubDB(path).db.close()  # mở lần hai không lỗi (cột đã có)

    def test_large_import_is_fast(self):
        rows = [{"id": "id-%05d" % i, "from": "anh" if i % 2 else "tinker", "to": "emi", "body": "tin %d" % i,
                 "created_at": "2026-11-20T00:00:00+00:00"} for i in range(5000)]
        started = time.monotonic()
        self.assertEqual(hub_legacy.import_rows(self.hub, rows)["imported"], 5000)
        self.assertLess(time.monotonic() - started, 10)

    def test_cli(self):
        source = self.dir / "messages.json"
        source.write_text(json.dumps(old_rows()), encoding="utf-8")
        db = str(self.dir / "cli.sqlite3")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(import_legacy.main(["--source", str(source), "--db", db, "--dry-run"]), 0)
        self.assertEqual(json.loads(out.getvalue())["imported"], 4)
        hub = HubDB(db)
        self.addCleanup(hub.db.close)
        self.assertEqual(hub.read_all(thread_id="legacy"), [])
        with contextlib.redirect_stdout(io.StringIO()):
            import_legacy.main(["--source", str(source), "--db", db, "--map", "emi=codex"])
        self.assertEqual(len(hub.read_all(thread_id="legacy")), 4)
        with self.assertRaises(SystemExit):
            import_legacy.main(["--source", str(self.dir / "khong-co.json"), "--db", db])


class LegacyHttpTests(unittest.TestCase):
    admin = "adm"

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.hub = HubDB(str(self.dir / "hub.sqlite3"))
        self.addCleanup(self.hub.db.close)
        self.tokens = {a: self.hub.register_agent(a, a.title(), "push")[1] for a in ("codex", "muse")}
        self.enterContext(patch.object(api_v1, "_hub", self.hub))
        self.env = self.enterContext(patch.dict(os.environ, {"AI_HUB_INTERNAL_TOKEN": self.admin,
                                                            "AI_HUB_FILES_DIR": str(self.dir / "files")}))
        self.rows = old_rows()
        self.enterContext(patch.object(api_v1, "legacy_rows", lambda: self.rows))
        self.enterContext(patch.object(ai_hub.Handler, "log_message"))
        self.session = True
        self.enterContext(patch.object(ai_hub.Handler, "_authenticated", lambda handler: self.session))
        server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), ai_hub.Handler))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.url = "http://127.0.0.1:%d" % server.server_address[1]
        self.root = HubClient(self.url, self.admin)
        self.codex, self.muse = HubClient(self.url, self.tokens["codex"]), HubClient(self.url, self.tokens["muse"])

    def page(self, path):
        try:
            with urllib.request.urlopen(self.url + path) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_import_endpoint_admin_only_dry_run_and_mapping(self):
        self.assertEqual(self.muse._request("POST", "/api/v1/legacy/import", {})["error"], "FORBIDDEN")
        dry = self.root._request("POST", "/api/v1/legacy/import", {"dry_run": True})
        self.assertEqual((dry["stats"]["imported"], self.hub.read_all(thread_id="legacy")), (4, []))
        done = self.root._request("POST", "/api/v1/legacy/import", {"map": {"emi": "codex"}})
        self.assertEqual((done["stats"]["imported"], done["mapping"]), (4, {"tinker": "muse", "emi": "codex"}))
        self.assertEqual(self.root._request("POST", "/api/v1/legacy/import", {})["stats"]["imported"], 0)
        feed = self.root._request("GET", "/api/v1/messages?tail=10")["messages"]
        self.assertEqual(feed, [])  # nguồn cấp mặc định không lẫn tin cũ
        legacy = self.root._request("GET", "/api/v1/messages?thread_id=legacy&tail=2")["messages"]
        self.assertEqual([m["from"] for m in legacy], ["codex", "anh"])
        self.assertEqual(self.codex.poll()["messages"], [])  # agent không nhận tin cũ

    def test_import_endpoint_errors(self):
        self.assertEqual(self.root._request("POST", "/api/v1/legacy/import", {"map": {"EMI": "x"}})["error"], "BAD_REQUEST")
        with patch.object(api_v1, "legacy_rows", None):
            self.assertEqual(self.root._request("POST", "/api/v1/legacy/import", {})["error"], "NOT_IMPLEMENTED")

        def broken():
            raise ValueError("messages.json hỏng")
        with patch.object(api_v1, "legacy_rows", broken):
            self.assertEqual(self.root._request("POST", "/api/v1/legacy/import", {})["error"], "LEGACY_UNREADABLE")

    def test_cookie_session_can_import_with_csrf_header(self):
        request = urllib.request.Request(self.url + "/api/v1/legacy/import", data=b"{}", method="POST")
        request.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)
        request.add_header("X-Mailbox-Request", "1")
        with urllib.request.urlopen(request) as response:
            self.assertEqual(json.load(response)["stats"]["imported"], 4)

    # --------------------------------------------------------------------- tệp
    def stamp(self, file_id, days_ago):
        old = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
        self.hub.db.execute("UPDATE files SET created_at=? WHERE id=?", (old, file_id))

    def test_purge_old_files_marks_references_deleted(self):
        old = self.codex.upload(b"old file", "cu.txt")["file"]
        fresh = self.codex.upload(PNG, "moi.png")["file"]
        self.stamp(old["id"], 40)
        self.codex.post("báo cáo", to="muse", files=[{"id": old["id"]}, {"id": fresh["id"]}])
        listing = self.root._request("GET", "/api/v1/files")
        self.assertEqual((len(listing["files"]), listing["used_bytes"]), (2, len(b"old file") + len(PNG)))
        for forbidden in ({"older_than_days": 30}, {}):
            self.assertEqual(self.muse._request("POST", "/api/v1/files/purge", forbidden)["error"], "FORBIDDEN")
        preview = self.root._request("POST", "/api/v1/files/purge", {"older_than_days": 30})["result"]
        self.assertEqual((preview["dry_run"], preview["count"], preview["bytes"]), (True, 1, 8))
        self.assertEqual(len(self.root._request("GET", "/api/v1/files")["files"]), 2)  # chưa xoá gì
        for bad in (0, -1, 99999, "30", True, None):
            self.assertEqual(self.root._request("POST", "/api/v1/files/purge", {"older_than_days": bad})["error"],
                             "BAD_REQUEST", bad)
        done = self.root._request("POST", "/api/v1/files/purge", {"older_than_days": 30, "dry_run": False})["result"]
        self.assertEqual((done["dry_run"], done["count"]), (False, 1))
        self.assertEqual([f["id"] for f in self.root._request("GET", "/api/v1/files")["files"]], [fresh["id"]])
        self.assertEqual(os.listdir(self.dir / "files"), [fresh["id"]])
        files = self.muse.poll()["messages"][0]["files"]
        self.assertEqual((files[0]["id"], files[0].get("deleted"), files[1].get("deleted")), (old["id"], True, None))

    def test_deleting_one_file_marks_only_its_references(self):
        a = self.codex.upload(b"aaa", "a.txt")["file"]
        b = self.codex.upload(b"bbb", "b.txt")["file"]
        self.codex.post("hai tệp", to="muse", files=[{"id": a["id"]}, {"id": b["id"]}])
        self.assertTrue(self.root._request("POST", "/api/v1/files/%s/delete" % a["id"], {})["ok"])
        files = self.muse.poll()["messages"][0]["files"]
        self.assertEqual([f.get("deleted") for f in files], [True, None])

    # ------------------------------------------------------------------- trang
    def test_legacy_page_and_default_ui_switch(self):
        status, root_default = self.page("/")
        self.assertEqual(status, 200)
        self.assertNotIn("/api/v1", root_default)  # mặc định "/" vẫn là giao diện cũ
        self.assertEqual(self.page("/legacy")[1], root_default)
        self.env["AI_HUB_DEFAULT_UI"] = "hub"
        status, switched = self.page("/")
        self.assertEqual(status, 200)
        self.assertIn("/api/v1", switched)
        self.assertEqual(self.page("/hub")[1], switched)
        self.assertEqual(self.page("/legacy")[1], root_default)  # giao diện cũ vẫn mở được
        self.session = False
        self.assertEqual(self.page("/legacy")[0], 401)
        self.assertNotIn("api/v1", self.page("/")[1])


if __name__ == "__main__":
    unittest.main()
