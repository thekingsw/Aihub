"""Test P4: adapter push — AI giả (fake CLI) nhận việc, làm, báo cáo, review, trả lời, chịu lỗi."""
import json
import os
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import agent_runner
import ai_hub
import api_v1
import hub_tasks
from agent_runner import AgentConfig, AgentRunner, AuthError, ConfigError, Defer
from hub_db import HubDB

FAKE_CLI = textwrap.dedent('''
    import os, sys, time
    prompt = sys.argv[-1] if os.environ.get("FAKE_ARG") else sys.stdin.read()
    with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as f:
        f.write("%d\\n" % os.getpid())
    out = []
    if any(k.upper().startswith("AI_HUB") for k in os.environ):
        out.append("ENV_LEAK")
    if "FAKE:SLEEP" in prompt: time.sleep(60)
    if "FAKE:FAIL" in prompt: print("boom"); sys.exit(3)
    if "FAKE:BLOCK" in prompt: print("Thử rồi.\\nSTATUS: BLOCKED: thiếu khoá API"); sys.exit(0)
    if "FAKE:ENVFAIL" in prompt: print("Failed to create unified exec process: timed out after 15000ms connecting runner pipe-in"); sys.exit(0)
    if "FAKE:ECHO" in prompt: out.append(prompt)
    if "FAKE:SILENT" in prompt: print("SILENT"); sys.exit(0)
    if "FAKE:LONG" in prompt: out.append("x" * 5000)
    if "FAKE:TOKEN" in prompt: out.append("token ah1_" + "x" * 30)
    if "FAKE:CWD" in prompt: out.append("CWD=" + os.path.basename(os.getcwd()))
    if "FAKE:CODEXFMT" in prompt:
        print("user\\n" + prompt[:20] + "\\ncodex\\nXong thật\\ntokens used 123"); sys.exit(0)
    if "FAKE:PASS" in prompt and "người review" in prompt: out.append("Đã test.\\nVERDICT: PASS")
    elif "FAKE:REJECT" in prompt and "người review" in prompt: out.append("Lỗi nút cờ.\\nVERDICT: FAIL")
    elif "người review" in prompt and "FAKE:NOVERDICT" in prompt: out.append("Chưa rõ.")
    else: out.append("Đã xong: tạo mines.html")
    print("\\n".join(out))
''')


class RunnerTests(unittest.TestCase):
    admin = "adm"

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.work = self.dir / "work"
        self.work.mkdir()
        (self.dir / "fake_cli.py").write_text(FAKE_CLI, encoding="utf-8")
        self.calls = self.dir / "calls.log"
        self.hub = HubDB(str(self.dir / "hub.sqlite3"))
        self.addCleanup(self.hub.db.close)
        self.tokens = {}
        for agent_id, mode in (("codex", "push"), ("muse", "pull"), ("claude", "push")):
            self.tokens[agent_id] = self.hub.register_agent(agent_id, agent_id.title(), mode, "vai trò " + agent_id)[1]
        self.board = hub_tasks.board_for(self.hub)
        self.enterContext(patch.object(api_v1, "_hub", self.hub))
        self.enterContext(patch.dict(os.environ, {"AI_HUB_INTERNAL_TOKEN": self.admin,
                                                  "AI_HUB_TOKEN_CODEX": self.tokens["codex"]}))
        self.enterContext(patch.object(ai_hub.Handler, "log_message"))
        server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), ai_hub.Handler))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.url = "http://127.0.0.1:%d" % server.server_address[1]
        self.now = 1_000_000.0
        self.enterContext(patch.object(agent_runner, "log", lambda *a: None))

    def runner(self, agent_id="codex", state="state", **overrides):
        raw = {"command": [sys.executable, str(self.dir / "fake_cli.py")], "workdir": str(self.work),
               "env": {"FAKE_CALLS": str(self.calls)}, "timeout_seconds": 20}
        raw.update(overrides)
        return AgentRunner(AgentConfig(agent_id, raw), self.url, self.tokens[agent_id], self.dir / state,
                           self.dir / "runs", clock=lambda: self.now, wait=0)

    def runs(self):
        return len(self.calls.read_text().split()) if self.calls.exists() else 0

    def inbox(self, agent_id, kind=None):
        return [m for m in self.hub.read_for(agent_id) if kind is None or m["kind"] == kind]

    def new_task(self, description="Làm game dò mìn", **kw):
        args = dict(assignee="codex", reviewer="muse", task_id="G1", project=None, description=description)
        args.update(kw)
        return self.board.create("anh", "Làm game", args.pop("assignee"), **args)

    # ------------------------------------------------------------------- làm việc
    def test_work_happy_path(self):
        self.new_task()
        runner = self.runner()
        runner.identify()
        self.assertEqual(runner.name, "Codex")
        self.assertEqual(runner.step(), 1)
        task = self.board.get("G1")
        self.assertEqual([e["to_status"] for e in task["events"]], ["todo", "in_progress", "in_review"])
        handoff = self.inbox("muse", "handoff")
        self.assertEqual(len(handoff), 1)
        self.assertIn("Đã xong: tạo mines.html", handoff[0]["body"])
        self.assertEqual(self.runs(), 1)
        self.assertEqual(runner.step(), 0)  # không chạy lại
        self.assertEqual(self.runs(), 1)

    def test_state_survives_restart(self):
        self.new_task()
        self.runner().step()
        fresh = self.runner()
        self.assertEqual(fresh.state["after"], self.inbox("codex")[0]["id"])
        self.assertEqual(fresh.step(), 0)
        self.assertEqual(self.runs(), 1)

    def test_child_never_sees_hub_tokens_and_output_is_redacted(self):
        self.new_task("FAKE:TOKEN")
        self.runner(env_passthrough=["AI_HUB_TOKEN_CODEX", "AI_HUB_INTERNAL_TOKEN"]).step()
        body = self.inbox("muse", "handoff")[0]["body"]
        self.assertNotIn("ENV_LEAK", body)
        self.assertNotIn("ah1_xxxx", body)
        self.assertIn("[ĐÃ ẨN]", body)
        log = (self.dir / "runs" / "codex" / ("%d.log" % self.inbox("codex")[0]["id"])).read_text(encoding="utf-8")
        self.assertNotIn("ah1_xxxx", log)

    def test_failure_blocks_task_and_tells_anh(self):
        self.new_task("FAKE:FAIL")
        self.runner().step()
        self.assertEqual(self.board.get("G1")["status"], "blocked")
        report = [m for m in self.hub.read_for("anh") if m["body"].startswith("BỊ CHẶN")]
        self.assertEqual(len(report), 1)
        self.assertIn("mã 3", report[0]["body"])

    def test_known_environment_failure_blocks_even_with_exit_code_zero(self):
        self.new_task("FAKE:ENVFAIL")
        self.runner().step()
        task = self.board.get("G1")
        self.assertEqual(task["status"], "blocked")
        self.assertIn("Session 1", task["events"][-1]["note"])

    def test_agent_can_declare_blocked(self):
        self.new_task("FAKE:BLOCK")
        self.runner().step()
        task = self.board.get("G1")
        self.assertEqual(task["status"], "blocked")
        self.assertIn("thiếu khoá API", task["events"][-1]["note"])

    def test_timeout_kills_process_tree_and_blocks(self):
        self.new_task("FAKE:SLEEP")
        started = time.monotonic()
        self.runner(timeout_seconds=1).step()
        self.assertLess(time.monotonic() - started, 10)
        task = self.board.get("G1")
        self.assertEqual(task["status"], "blocked")
        self.assertIn("Quá thời gian", task["events"][-1]["note"])
        pid = int(self.calls.read_text().split()[0])
        time.sleep(.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_missing_command_and_workdir_block_cleanly(self):
        self.new_task()
        self.runner(command=["khong-co-lenh-nay-xyz"]).step()
        self.assertIn("Không chạy được lệnh", self.board.get("G1")["events"][-1]["note"])
        self.new_task(task_id="G2")
        self.runner(state="s2", workdir=str(self.dir / "khong-co")).step()
        self.assertIn("Thư mục làm việc không tồn tại", self.board.get("G2")["events"][-1]["note"])

    def test_long_output_is_truncated_and_saved(self):
        self.new_task("FAKE:LONG")
        self.runner(max_summary_chars=1000).step()
        body = self.inbox("muse", "handoff")[0]["body"]
        self.assertLess(len(body), 1400)
        self.assertIn("bản đầy đủ", body)
        saved = next((self.dir / "runs" / "codex").glob("*.md"))
        self.assertGreater(len(saved.read_text(encoding="utf-8")), 4000)

    def test_project_selects_workdir_and_prompt_via_arg(self):
        project_dir = self.dir / "retro-games"
        project_dir.mkdir()
        self.new_task("FAKE:CWD", project="retro")
        env = {"FAKE_CALLS": str(self.calls), "FAKE_ARG": "1"}
        self.runner(projects={"retro": str(project_dir)}, prompt_via="arg", env=env).step()
        self.assertIn("CWD=retro-games", self.inbox("muse", "handoff")[0]["body"])

    def test_codex_output_filter(self):
        self.new_task("FAKE:CODEXFMT")
        self.runner(output_filter="codex").step()
        self.assertEqual(self.inbox("muse", "handoff")[0]["body"], "Xong thật")

    # --------------------------------------------------------------------- review
    def reviewed_task(self, marker):
        self.board.create("anh", "Việc cần review", "muse", reviewer="codex", task_id="R1", description="x " + marker)
        self.board.transition("muse", "R1", "in_progress")
        self.board.transition("muse", "R1", "in_review", note="Xong, mời review")

    def test_review_pass(self):
        self.reviewed_task("FAKE:PASS")
        self.runner().step()
        task = self.board.get("R1")
        self.assertEqual(task["status"], "done")
        self.assertIn("Đã test.", task["events"][-1]["note"])
        self.assertTrue(self.inbox("muse", "review")[0]["body"].startswith("ĐẠT"))

    def test_review_fail_sends_back_with_reasons(self):
        self.reviewed_task("FAKE:REJECT")
        self.runner().step()
        self.assertEqual(self.board.get("R1")["status"], "in_progress")
        msg = self.inbox("muse", "review")[0]
        self.assertTrue(msg["body"].startswith("CẦN SỬA"))
        self.assertIn("Lỗi nút cờ", msg["body"])

    def test_review_without_verdict_leaves_task_for_anh(self):
        self.reviewed_task("FAKE:NOVERDICT")
        self.runner().step()
        self.assertEqual(self.board.get("R1")["status"], "in_review")
        notes = [m for m in self.hub.read_for("anh") if "Chưa nghiệm thu" in m["body"]]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["kind"], "result")

    def test_rework_loop_gives_reviewer_notes_to_the_worker(self):
        self.new_task("FAKE:ECHO")
        runner = self.runner()
        runner.step()
        self.board.transition("muse", "G1", "in_progress", note="Sửa nút cờ bị lệch")
        self.assertEqual(runner.step(), 1)
        self.assertEqual(self.runs(), 2)
        self.assertEqual(self.board.get("G1")["status"], "in_review")
        self.assertIn("Sửa nút cờ bị lệch", self.inbox("muse", "handoff")[-1]["body"])

    # ----------------------------------------------------------------------- chat
    def test_direct_message_gets_a_reply_in_the_same_thread(self):
        question = self.hub.post("anh", "Em khoẻ không?", to="codex")
        self.runner().step()
        reply = self.inbox("anh", "message")[0]
        self.assertEqual((reply["from"], reply["reply_to"], reply["to"]), ("codex", question["id"], ["anh"]))
        self.assertIn("Đã xong", reply["body"])

    def test_silent_reply_posts_nothing(self):
        self.hub.post("anh", "Cảm ơn nhé FAKE:SILENT", to="codex")
        self.runner().step()
        self.assertEqual(self.inbox("anh"), [])

    def test_chat_rules(self):
        self.hub.post("anh", "gửi cả phòng", to="all")
        self.hub.post("muse", "hỏi nhỏ", to="codex")
        self.runner(chat_from_agents=False).step()
        self.assertEqual(self.runs(), 0)
        self.runner(state="s2", chat=False).step()
        self.assertEqual(self.runs(), 0)
        self.runner(state="s3").step()
        self.assertEqual(self.runs(), 1)  # tin riêng từ muse được trả lời; tin cả phòng thì không

    def test_task_that_is_no_longer_open_is_skipped(self):
        self.new_task()
        self.board.transition("anh", "G1", "cancelled", note="đổi kế hoạch")
        self.runner().step()
        self.assertEqual(self.runs(), 0)

    # ------------------------------------------------------------- hạn mức, lỗi
    def test_hourly_run_limit_defers_without_losing_messages(self):
        self.new_task(task_id="A")
        self.new_task(task_id="B")
        runner = self.runner(max_runs_per_hour=1)
        with self.assertRaises(Defer):
            runner.step()
        self.assertEqual((self.runs(), self.board.get("A")["status"], self.board.get("B")["status"]),
                         (1, "in_review", "todo"))
        self.now += 3601
        self.assertEqual(runner.step(), 1)
        self.assertEqual(self.board.get("B")["status"], "in_review")

    def test_delivery_failure_does_not_rerun_the_ai(self):
        self.new_task()
        runner = self.runner()
        real, failed = runner.client.transition, []

        def flaky(task_id, status, note=None, files=None):
            if status == "in_review" and not failed:
                failed.append(1)
                raise OSError("hub down")
            return real(task_id, status, note, files)

        runner.client.transition = flaky
        with self.assertRaises(OSError):
            runner.step()
        self.assertEqual((self.runs(), self.board.get("G1")["status"]), (1, "in_progress"))
        self.assertEqual(runner.step(), 1)
        self.assertEqual((self.runs(), self.board.get("G1")["status"]), (1, "in_review"))

    def test_turn_limit_conflict_falls_back_to_result_for_anh(self):
        self.new_task()
        runner = self.runner()
        real = runner.client.transition
        runner.client.transition = lambda t, s, n=None, f=None: (
            {"ok": False, "error": "CONFLICT", "message": "đủ 8 lượt"} if s == "in_review" else real(t, s, n, f))
        runner.step()
        self.assertEqual(self.board.get("G1")["status"], "in_progress")
        fallback = [m for m in self.hub.read_for("anh") if "hub không cho đổi" in m["body"]]
        self.assertEqual((len(fallback), fallback[0]["kind"]), (1, "result"))
        self.assertIn("Đã xong: tạo mines.html", fallback[0]["body"])

    def test_disabled_agent_and_wrong_token_are_auth_errors(self):
        runner = self.runner()
        self.hub.set_enabled("codex", False)
        with self.assertRaises(AuthError):
            runner.step()
        wrong = AgentRunner(AgentConfig("muse", {"command": ["x"], "workdir": str(self.work)}), self.url,
                            self.tokens["claude"], self.dir / "s", self.dir / "r")
        with self.assertRaises(AuthError):
            wrong.identify()

    # ---------------------------------------------------------------- cấu hình
    def test_child_env_whitelist(self):
        cfg = AgentConfig("codex", {"command": ["x"], "workdir": ".", "env_passthrough": ["AI_HUB_TOKEN_CODEX", "FOO"],
                                    "env": {"AI_HUB_X": "1", "OK": "2"}})
        with patch.dict(os.environ, {"FOO": "bar", "SECRET_OTHER": "no"}):
            env = agent_runner.child_env(cfg)
        self.assertEqual((env.get("FOO"), env.get("OK")), ("bar", "2"))
        self.assertNotIn("SECRET_OTHER", env)
        self.assertFalse([k for k in env if k.upper().startswith("AI_HUB")])
        self.assertIn("PATH", env)

    def test_config_loading_and_validation(self):
        path = self.dir / "runner.json"
        path.write_text(json.dumps({"hub_url": "http://h:1/", "agents": {"codex": {"command": ["codex", "exec", "-"],
                                    "workdir": "w"}}}), encoding="utf-8")
        config = agent_runner.load_config(path)
        self.assertEqual(config["hub_url"], "http://h:1")
        self.assertEqual(config["agents"]["codex"].token_env, "AI_HUB_TOKEN_CODEX")
        self.assertEqual(config["state_dir"], self.dir / "runner-state")
        for bad in ({"hub_url": "http://h", "agents": {"a": {"command": ["x"], "workdir": "w", "fail_patterns": ["("]}}},
                    {"agents": {}}, {"hub_url": "ftp://x", "agents": {"a": {}}},
                    {"hub_url": "http://h", "agents": {"a": {"command": [], "workdir": "w"}}},
                    {"hub_url": "http://h", "agents": {"a": {"command": ["x"], "workdir": "w", "prompt_via": "tty"}}},
                    {"hub_url": "http://h", "agents": {"a": {"command": ["x"], "workdir": "w", "timeout_seconds": 0}}}):
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ConfigError, msg=str(bad)):
                agent_runner.load_config(path)
        with self.assertRaises(ConfigError):
            agent_runner.load_config(self.dir / "khong-co.json")
        path.write_text(json.dumps({"hub_url": "http://h", "agents": {"codex": {"command": ["x"], "workdir": "w"}}}))
        with patch.dict(os.environ, clear=False) as env:
            env.pop("AI_HUB_TOKEN_CODEX", None)
            with self.assertRaises(ConfigError):
                agent_runner.build_runners(agent_runner.load_config(path))

    def test_extract_codex_reply(self):
        raw = "OpenAI Codex v1\nuser\nlàm việc A\nthinking\nsuy nghĩ\nexec\nls\ncodex\nXong A.\nCòn B.\ntokens used 99\n"
        self.assertEqual(agent_runner.extract_codex_reply(raw), "Xong A.\nCòn B.")
        self.assertEqual(agent_runner.extract_codex_reply("chỉ có chữ thường"), "chỉ có chữ thường")

    def test_main_once_end_to_end(self):
        self.new_task()
        config = self.dir / "runner.json"
        config.write_text(json.dumps({"hub_url": self.url, "agents": {"codex": {
            "command": [sys.executable, str(self.dir / "fake_cli.py")], "workdir": str(self.work),
            "env": {"FAKE_CALLS": str(self.calls)}}}}), encoding="utf-8")
        self.assertEqual(agent_runner.main(["--config", str(config), "--once"]), 0)
        self.assertEqual(self.board.get("G1")["status"], "in_review")
        with self.assertRaises(SystemExit):
            agent_runner.main(["--config", str(config), "--once", "--agent", "ghost"])


if __name__ == "__main__":
    unittest.main()
