"""Test P2: bảng việc — vòng giao việc → làm → review → xong, quyền, rework, chặn vòng lặp."""
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import agent_client
import ai_hub
import api_v1
import hub_tasks
from hub_db import HubDB, Conflict, Forbidden, HubError, NotFound


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.hub = HubDB(str(self.dir / "hub.sqlite3"))
        self.addCleanup(self.hub.db.close)
        for agent_id, mode in (("codex", "push"), ("muse", "pull"), ("claude", "push")):
            self.hub.register_agent(agent_id, agent_id.title(), mode)
        self.board = hub_tasks.board_for(self.hub)

    def new(self, **kw):
        args = dict(title="Làm game dò mìn", assignee="codex", reviewer="muse", project="retro",
                    description="Làm game dò mìn bằng HTML.", task_id="GAME-1")
        args.update(kw)
        return self.board.create("anh", args.pop("title"), args.pop("assignee"), **args)

    def test_board_is_singleton_per_hub(self):
        self.assertIs(hub_tasks.board_for(self.hub), self.board)

    def test_create_posts_task_message_to_assignee(self):
        task = self.new()
        self.assertEqual((task["status"], task["assignee"], task["reviewer"]), ("todo", "codex", "muse"))
        inbox = self.hub.read_for("codex")
        self.assertEqual([(m["kind"], m["task_id"], m["thread_id"]) for m in inbox], [("task", "GAME-1", "GAME-1")])
        self.assertEqual(self.hub.read_for("muse"), [])

    def test_auto_id_and_validation(self):
        first = self.board.create("anh", "A", "codex")
        second = self.board.create("anh", "B", "codex")
        self.assertEqual((first["id"], second["id"]), ("T-0001", "T-0002"))
        for kwargs in ({"assignee": "ghost"}, {"assignee": "anh"}, {"reviewer": "codex"}, {"task_id": "bad id"},
                       {"title": " "}, {"assignee": ["codex"]}):
            with self.assertRaises(HubError, msg=str(kwargs)):
                self.new(**kwargs)
        with self.assertRaises(Conflict):
            self.new(task_id="T-0001")
        with self.assertRaises(HubError):  # chính mình giao cho mình
            self.board.create("codex", "x", "codex")

    def test_happy_path_with_review(self):
        self.new()
        self.board.transition("codex", "GAME-1", "in_progress")
        done = self.board.transition("codex", "GAME-1", "in_review", note="Xong, file mines.html",
                                     files=[{"name": "mines.html", "url": "http://x/mines.html"}])
        self.assertEqual(done["status"], "in_review")
        handoff = [m for m in self.hub.read_for("muse") if m["kind"] == "handoff"]
        self.assertEqual(len(handoff), 1)
        self.assertEqual(handoff[0]["files"][0]["name"], "mines.html")
        self.assertIn("anh", handoff[0]["to"])
        final = self.board.transition("muse", "GAME-1", "done", note="Test đủ 3 cấp độ")
        self.assertEqual(final["status"], "done")
        review = [m for m in self.hub.read_for("codex") if m["kind"] == "review"]
        self.assertTrue(review[0]["body"].startswith("ĐẠT"))
        self.assertEqual([e["to_status"] for e in final["events"]], ["todo", "in_progress", "in_review", "done"])

    def test_rework_loop_and_turn_limit_rolls_back_status(self):
        self.new()
        self.board.transition("codex", "GAME-1", "in_progress")
        for _ in range(3):  # mỗi vòng: handoff (1 lượt) + trả về (1 lượt) = 2 lượt agent
            self.board.transition("codex", "GAME-1", "in_review", note="Xong lần %d" % _)
            self.board.transition("muse", "GAME-1", "in_progress", note="Lỗi nút cờ %d" % _)
        self.board.transition("codex", "GAME-1", "in_review", note="Xong lần 4")
        self.board.transition("muse", "GAME-1", "in_progress", note="Vẫn lỗi")
        with self.assertRaises(Conflict):  # lượt agent thứ 9 trong luồng
            self.board.transition("codex", "GAME-1", "in_review", note="Xong lần 5")
        self.assertEqual(self.board.get("GAME-1")["status"], "in_progress")  # savepoint hoàn tác
        self.board.transition("anh", "GAME-1", "in_review", note="Anh ép sang review")  # anh nhắn là hết khoá
        self.assertEqual(self.board.get("GAME-1")["status"], "in_review")

    def test_permissions(self):
        self.new()
        with self.assertRaises(Forbidden):
            self.board.transition("muse", "GAME-1", "in_progress")  # reviewer không được nhận việc
        with self.assertRaises(Forbidden):
            self.board.transition("claude", "GAME-1", "in_progress")  # người ngoài
        self.board.transition("codex", "GAME-1", "in_progress")
        with self.assertRaises(Conflict):
            self.board.transition("codex", "GAME-1", "done")  # có reviewer thì phải qua in_review
        self.board.transition("codex", "GAME-1", "in_review")
        with self.assertRaises(Forbidden):
            self.board.transition("codex", "GAME-1", "done")  # không tự nghiệm thu
        with self.assertRaises(Forbidden):
            self.board.transition("claude", "GAME-1", "done")
        with self.assertRaises(HubError):
            self.board.transition("muse", "GAME-1", "in_progress")  # trả về mà không nêu lý do
        with self.assertRaises(Conflict):
            self.board.transition("muse", "GAME-1", "todo")
        with self.assertRaises(HubError):
            self.board.transition("muse", "GAME-1", "nonsense")
        with self.assertRaises(NotFound):
            self.board.transition("muse", "NOPE", "done")
        with self.assertRaises(Forbidden):
            self.board.transition("ghost", "GAME-1", "done")

    def test_no_reviewer_flow_and_reopen(self):
        self.new(reviewer=None, task_id="SOLO")
        self.board.transition("codex", "SOLO", "in_progress")
        self.board.transition("codex", "SOLO", "done", note="Xong")
        result = [m for m in self.hub.read_all() if m["kind"] == "result"]
        self.assertEqual((result[0]["from"], result[0]["to"]), ("codex", ["anh"]))
        with self.assertRaises(Forbidden):
            self.board.transition("codex", "SOLO", "in_progress", note="mở lại")
        reopened = self.board.transition("anh", "SOLO", "in_progress", note="thiếu phần âm thanh")
        self.assertEqual(reopened["status"], "in_progress")
        self.assertTrue([m for m in self.hub.read_for("codex") if m["body"].startswith("MỞ LẠI")])

    def test_unblock_notifies_assignee(self):
        self.new()
        self.board.transition("codex", "GAME-1", "blocked", note="Provider 403")
        before = len(self.hub.read_for("codex"))
        self.board.transition("anh", "GAME-1", "in_progress", note="Đã sửa quyền thư mục")
        fresh = self.hub.read_for("codex")[before:]
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["kind"], "task")
        self.assertTrue(fresh[0]["body"].startswith("MỞ CHẶN"))
        self.assertIn("Đã sửa quyền thư mục", fresh[0]["body"])

    def test_blocked_and_cancel(self):
        self.new()
        with self.assertRaises(HubError):
            self.board.transition("codex", "GAME-1", "blocked")  # thiếu note
        self.board.transition("codex", "GAME-1", "blocked", note="Provider 403")
        report = [m for m in self.hub.read_for("anh") if m["body"].startswith("BỊ CHẶN")]
        self.assertEqual(len(report), 1)
        self.board.transition("codex", "GAME-1", "in_progress")
        # Mở chặn: reviewer và creator cũng mở được (anh chốt 28/11 — việc kẹt Tinker tự xử lý)
        self.board.transition("codex", "GAME-1", "blocked", note="Kẹt lần 2")
        self.board.transition("muse", "GAME-1", "in_progress")  # reviewer mở chặn
        self.board.transition("codex", "GAME-1", "blocked", note="Kẹt lần 3")
        with self.assertRaises(Forbidden):
            self.board.transition("claude", "GAME-1", "in_progress")  # người ngoài không mở được
        self.board.transition("anh", "GAME-1", "in_progress")
        self.board.create("muse", "Việc muse giao", "codex", task_id="M-9")
        self.board.transition("codex", "M-9", "blocked", note="Kẹt")
        self.board.transition("muse", "M-9", "in_progress")  # creator (không phải admin) mở chặn
        with self.assertRaises(Forbidden):
            self.board.transition("codex", "GAME-1", "cancelled", note="thôi")  # không phải người tạo
        self.board.transition("anh", "GAME-1", "cancelled", note="Đổi kế hoạch")
        with self.assertRaises(Forbidden):  # mở lại việc đã huỷ chỉ anh làm được
            self.board.transition("codex", "GAME-1", "in_progress")
        with self.assertRaises(Conflict):
            self.board.transition("codex", "GAME-1", "done")

    def test_reassign_and_list_filters(self):
        self.new()
        self.board.create("anh", "Việc khác", "muse", project="kpi")
        moved = self.board.reassign("GAME-1", assignee="claude", clear_reviewer=True)
        self.assertEqual((moved["assignee"], moved["reviewer"]), ("claude", None))
        self.assertEqual(len([m for m in self.hub.read_for("claude") if m["kind"] == "task"]), 1)
        self.assertEqual([t["id"] for t in self.board.list(assignee="muse")], ["T-0002"])
        self.assertEqual([t["id"] for t in self.board.list(project="retro")], ["GAME-1"])
        self.assertEqual(len(self.board.list(status="todo")), 2)
        with self.assertRaises(HubError):
            self.board.list(status="weird")


class TaskContextTests(unittest.TestCase):
    """Tin nằm trong luồng của một việc phải tự mang task_id/project để AI biết đang nói về việc nào."""

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.hub = HubDB(str(self.dir / "hub.sqlite3"))
        self.addCleanup(self.hub.db.close)
        for agent_id in ("codex", "muse"):
            self.hub.register_agent(agent_id, agent_id.title(), "push")
        self.board = hub_tasks.board_for(self.hub)
        self.board.create("anh", "Làm game", "codex", reviewer="muse", task_id="GAME-1", project="retro")
        self.board.create("anh", "Làm KPI", "codex", task_id="KPI-1", project="kpi")

    def test_thread_id_of_a_task_fills_task_id_and_project(self):
        msg = self.hub.post("anh", "nút cờ lỗi", to="codex", thread_id="GAME-1")
        self.assertEqual((msg["task_id"], msg["project"], msg["thread_id"]), ("GAME-1", "retro", "GAME-1"))

    def test_task_id_alone_fills_project(self):
        msg = self.hub.post("anh", "hỏi", to="codex", task_id="KPI-1")
        self.assertEqual((msg["thread_id"], msg["project"]), ("KPI-1", "kpi"))

    def test_replies_inherit_task_and_project(self):
        first = self.hub.post("anh", "hỏi nhỏ", to="codex", task_id="GAME-1")
        reply = self.hub.post("codex", "đã sửa", reply_to=first["id"])
        self.assertEqual((reply["task_id"], reply["project"], reply["thread_id"]), ("GAME-1", "retro", "GAME-1"))

    def test_unrelated_threads_are_untouched(self):
        for thread in ("main", "ui-abc", "legacy-ish"):
            msg = self.hub.post("anh", "chuyện chung " + thread, to="codex", thread_id=thread)
            self.assertNotIn("task_id", msg)
            self.assertNotIn("project", msg)
        msg = self.hub.post("anh", "nói về dự án", to="codex", project="retro")
        self.assertEqual((msg["thread_id"], msg.get("task_id"), msg["project"]), ("main", None, "retro"))

    def test_explicit_project_is_kept(self):
        msg = self.hub.post("anh", "x", to="codex", thread_id="GAME-1", project="khác")
        self.assertEqual(msg["project"], "khác")

    def test_works_before_the_task_table_exists(self):
        fresh = HubDB(str(self.dir / "fresh.sqlite3"))
        self.addCleanup(fresh.db.close)
        fresh.register_agent("codex", "Codex", "push")
        msg = fresh.post("anh", "chưa có bảng việc", to="codex", thread_id="GAME-1")
        self.assertNotIn("task_id", msg)

    def test_list_filters_involves_and_open(self):
        self.board.create("anh", "Việc của muse", "muse", reviewer="codex", task_id="M-1", project="kpi")
        self.board.transition("anh", "KPI-1", "cancelled", note="bỏ")
        ids = lambda **kw: sorted(t["id"] for t in self.board.list(**kw))  # noqa: E731
        self.assertEqual(ids(involves="codex"), ["GAME-1", "KPI-1", "M-1"])
        self.assertEqual(ids(involves="codex", open_only=True), ["GAME-1", "M-1"])
        self.assertEqual(ids(involves="muse", open_only=True), ["GAME-1", "M-1"])
        self.assertEqual(ids(involves="nobody"), [])

    def test_list_summary_truncates_description(self):
        long_desc = "x" * 500
        self.board.create("anh", "Việc dài", "codex", task_id="LONG-1", description=long_desc)
        full = {t["id"]: t for t in self.board.list()}["LONG-1"]
        self.assertEqual(full["description"], long_desc)
        summ = {t["id"]: t for t in self.board.list(summary=True)}["LONG-1"]
        self.assertEqual(summ["description"], "x" * 200)
        # get() vẫn trả toàn văn
        self.assertEqual(self.board.get("LONG-1")["description"], long_desc)


class TaskApiTests(unittest.TestCase):
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
        url = "http://127.0.0.1:%d" % server.server_address[1]
        self.root = agent_client.HubClient(url, self.admin)
        self.clients = {}
        for agent_id, mode in (("codex", "push"), ("muse", "pull")):
            token = self.root._request("POST", "/api/v1/agents", {"id": agent_id, "name": agent_id, "mode": mode})["token"]
            self.clients[agent_id] = agent_client.HubClient(url, token)

    def test_game_scenario_over_http(self):
        codex, muse = self.clients["codex"], self.clients["muse"]
        created = self.root._request("POST", "/api/v1/tasks", {
            "id": "GAME-1", "title": "Làm game dò mìn", "assignee": "codex", "reviewer": "muse",
            "description": "Làm game dò mìn bằng HTML", "project": "retro"})
        self.assertTrue(created["ok"], created)
        inbox = codex.poll()["messages"]
        self.assertEqual(inbox[0]["task_id"], "GAME-1")
        self.assertTrue(codex._request("POST", "/api/v1/tasks/GAME-1/status", {"status": "in_progress"})["ok"])
        handoff = codex._request("POST", "/api/v1/tasks/GAME-1/status", {
            "status": "in_review", "note": "Xong", "files": [{"name": "mines.html", "url": "http://x/m.html"}]})
        self.assertEqual(handoff["task"]["status"], "in_review")
        seen = muse.poll()["messages"]
        self.assertEqual([m["kind"] for m in seen], ["handoff"])
        denied = codex._request("POST", "/api/v1/tasks/GAME-1/status", {"status": "done"})
        self.assertEqual(denied["error"], "FORBIDDEN")
        bad = muse._request("POST", "/api/v1/tasks/GAME-1/status", {"status": "in_progress"})
        self.assertEqual(bad["error"], "BAD_REQUEST")
        done = muse._request("POST", "/api/v1/tasks/GAME-1/status", {"status": "done", "note": "Ổn"})
        self.assertEqual(done["task"]["status"], "done")
        view = muse._request("GET", "/api/v1/tasks/GAME-1")["task"]
        self.assertEqual(len(view["events"]), 4)
        self.assertEqual([t["id"] for t in muse._request("GET", "/api/v1/tasks?status=done")["tasks"]], ["GAME-1"])
        thread = self.root._request("GET", "/api/v1/messages?thread_id=GAME-1&limit=50")["messages"]
        self.assertEqual([m["kind"] for m in thread], ["task", "handoff", "review"])

    def test_involves_and_open_filters_over_http(self):
        codex = self.clients["codex"]
        for task_id, assignee, reviewer in (("A", "codex", "muse"), ("B", "muse", "codex"), ("C", "muse", None)):
            self.root._request("POST", "/api/v1/tasks", {"id": task_id, "title": task_id, "assignee": assignee,
                                                        "reviewer": reviewer, "project": "p" + task_id})
        self.root._request("POST", "/api/v1/tasks/B/status", {"status": "cancelled", "note": "bỏ"})
        got = lambda q: sorted(t["id"] for t in codex._request("GET", "/api/v1/tasks" + q)["tasks"])  # noqa: E731
        self.assertEqual(got("?involves=codex"), ["A", "B"])
        self.assertEqual(got("?involves=codex&open=1"), ["A"])
        self.assertEqual(sorted(t["id"] for t in codex.tasks(involves="muse", open=True)["tasks"]), ["A", "C"])

    def test_http_errors(self):
        codex = self.clients["codex"]
        self.assertEqual(codex._request("GET", "/api/v1/tasks/NOPE")["error"], "NOT_FOUND")
        self.assertEqual(agent_client.HubClient(codex.base, "ah1_sai")._request("GET", "/api/v1/tasks")["error"],
                         "UNAUTHENTICATED")
        self.assertEqual(codex._request("POST", "/api/v1/tasks", {"title": "x", "assignee": "codex"})["error"],
                         "BAD_REQUEST")
        self.assertEqual(codex._request("POST", "/api/v1/tasks/X/assign", {"assignee": "muse"})["error"], "FORBIDDEN")
        made = codex._request("POST", "/api/v1/tasks", {"title": "Nhờ Muse test", "assignee": "muse", "id": "T9"})
        self.assertEqual(made["task"]["created_by"], "codex")  # agent cũng giao việc được cho nhau
        self.assertEqual(self.clients["muse"]._request("POST", "/api/v1/tasks/T9/assign", {"assignee": "codex"})["error"],
                         "FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
