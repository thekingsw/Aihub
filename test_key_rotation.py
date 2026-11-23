"""Isolated tests for the internal Codex key rotation endpoint."""
import json
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.error import URLError

import tomllib

import ai_hub


class KeyRotationTests(unittest.TestCase):
    token = "internal-test-token"

    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory(dir=ai_hub.BASE))
        self.folder = Path(folder)
        self.config = self.folder / "config.toml"
        self.audit = self.folder / "audit.jsonl"
        self.original = (
            b'[model_providers.OpenAI]\n'
            b'name = "OpenAI-compatible"\n'
            b'experimental_bearer_token = "old-test-value" # preserve this comment\n'
            b'base_url = "https://api.xingchiapi.com/v1"\n'
            b'\n[unrelated]\nkeep = 17\n'
        )
        self.config.write_bytes(self.original)
        self.enterContext(patch.object(ai_hub, "CODEX_CONFIG_PATH", str(self.config)))
        self.enterContext(patch.object(ai_hub, "KEY_ROTATION_AUDIT_PATH", str(self.audit)))
        self.enterContext(patch.dict(os.environ, {"AI_HUB_INTERNAL_TOKEN": self.token}))
        self.enterContext(patch.object(ai_hub.Handler, "log_message"))
        self.server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), ai_hub.Handler))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:%d/internal/rotate-codex-key" % self.server.server_port

    def post(self, key, authorization=None):
        headers = {"Content-Type": "application/json"}
        if authorization is not None:
            headers["Authorization"] = authorization
        request = Request(self.url, data=json.dumps({"api_key": key}).encode("utf-8"),
                          headers=headers, method="POST")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read()
        except HTTPError as exc:
            try:
                return exc.code, exc.read()
            finally:
                exc.close()

    def audit_text(self):
        return self.audit.read_text(encoding="utf-8") if self.audit.exists() else ""

    def test_authentication_rejects_before_key_validation(self):
        key = "synthetic-auth-test-value"
        with patch.object(ai_hub, "verify_xingchi_key") as verify:
            status, body = self.post(key, "Bearer incorrect")
        self.assertEqual(status, 401)
        verify.assert_not_called()
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertNotIn(key.encode(), body)

    def test_rejected_key_does_not_modify_config_or_audit_key(self):
        key = "synthetic-rejected-test-value"
        with patch.object(ai_hub, "verify_xingchi_key", return_value=(False, "rejected")):
            status, body = self.post(key, "Bearer " + self.token)
        self.assertEqual(status, 422)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertNotIn(key.encode(), body)
        self.assertNotIn(key, self.audit_text())

    def test_network_failure_does_not_modify_config(self):
        key = "synthetic-network-test-value"
        with patch.object(ai_hub, "verify_xingchi_key", return_value=(False, "upstream")):
            status, body = self.post(key, "Bearer " + self.token)
        self.assertEqual(status, 502)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertNotIn(key.encode(), body)
        self.assertNotIn(key, self.audit_text())

    def test_verifier_network_failure_is_fail_closed_without_external_request(self):
        class OfflineOpener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                raise URLError("synthetic offline")

        opener = OfflineOpener()
        with patch.object(ai_hub.urllib.request, "build_opener", return_value=opener):
            result = ai_hub.verify_xingchi_key("synthetic-network-test-value")
        self.assertEqual(result, (False, "upstream"))
        self.assertEqual(opener.request.full_url, ai_hub.XINGCHI_MODELS_URL)
        self.assertEqual(opener.timeout, 15)

    def test_verifier_accepts_only_http_200(self):
        class Response:
            def __init__(self, status):
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Opener:
            def __init__(self, status):
                self.status = status

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response(self.status)

        key = "synthetic-http-200-test-value"
        for status, expected in ((200, (True, "")), (204, (False, "upstream"))):
            opener = Opener(status)
            with patch.object(ai_hub.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(ai_hub.verify_xingchi_key(key), expected)
            self.assertEqual(opener.request.full_url, ai_hub.XINGCHI_MODELS_URL)
            self.assertEqual(opener.timeout, 15)
            self.assertEqual(opener.request.get_header("Authorization"), "Bearer " + key)

    def test_backup_failure_does_not_write_config(self):
        key = "synthetic-backup-test-value"
        with patch.object(ai_hub, "verify_xingchi_key", return_value=(True, "")), patch.object(
                ai_hub, "_create_config_backup", side_effect=OSError("synthetic failure")):
            status, body = self.post(key, "Bearer " + self.token)
        self.assertEqual(status, 500)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertNotIn(key.encode(), body)
        self.assertNotIn(key, self.audit_text())

    def test_write_failure_keeps_original_and_completed_backup(self):
        key = "synthetic-write-test-value"
        with patch.object(ai_hub, "verify_xingchi_key", return_value=(True, "")), patch.object(
                ai_hub, "_write_atomic", side_effect=OSError("synthetic failure")):
            status, body = self.post(key, "Bearer " + self.token)
        self.assertEqual(status, 500)
        self.assertEqual(self.config.read_bytes(), self.original)
        backups = list(self.folder.glob("config.toml.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertNotIn(key.encode(), body)
        self.assertNotIn(key, self.audit_text())

    def test_success_backs_up_then_updates_only_target_value(self):
        key = "synthetic-success-test-value"
        with patch.object(ai_hub, "verify_xingchi_key", return_value=(True, "")):
            status, body = self.post(key, "Bearer " + self.token)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertTrue(result["changed"])
        self.assertNotIn(key, body.decode("utf-8"))
        backup = self.folder / result["backup"]
        self.assertEqual(backup.read_bytes(), self.original)
        updated_text = self.config.read_text(encoding="utf-8")
        updated = tomllib.loads(updated_text)
        self.assertEqual(updated["model_providers"]["OpenAI"]["experimental_bearer_token"], key)
        self.assertEqual(updated["model_providers"]["OpenAI"]["base_url"], "https://api.xingchiapi.com/v1")
        self.assertEqual(updated["unrelated"]["keep"], 17)
        self.assertIn("# preserve this comment", updated_text)
        self.assertNotIn(key, self.audit_text())
        event = json.loads(self.audit_text())
        self.assertEqual((event["result"], event["reason"]), ("applied", "models_200"))

    def test_successful_response_reports_length_and_repeated_key_is_noop(self):
        key = "synthetic-repeat-test-value"
        with patch.object(ai_hub, "verify_xingchi_key", return_value=(True, "")):
            first_status, first_body = self.post(key, "Bearer " + self.token)
            self.assertEqual(first_status, 200)
            first = json.loads(first_body)
            self.assertEqual(first["key_length"], len(key))
            self.assertTrue(first["changed"])
            self.assertNotIn(key.encode(), first_body)

            config_after_first = self.config.read_bytes()
            backups_after_first = sorted(self.folder.glob("config.toml.bak.*"))
            self.assertEqual(len(backups_after_first), 1)

            second_status, second_body = self.post(key, "Bearer " + self.token)

        self.assertEqual(second_status, 200)
        second = json.loads(second_body)
        self.assertEqual(second["key_length"], len(key))
        self.assertFalse(second["changed"])
        self.assertIsNone(second["backup"])
        self.assertNotIn(key.encode(), second_body)
        self.assertEqual(self.config.read_bytes(), config_after_first)
        self.assertEqual(sorted(self.folder.glob("config.toml.bak.*")), backups_after_first)

    def test_missing_auth_and_unconfigured_token(self):
        with patch.object(ai_hub, "verify_xingchi_key") as verify:
            self.assertEqual(self.post("synthetic", None)[0], 401)
            with patch.dict(os.environ, {"AI_HUB_INTERNAL_TOKEN": ""}):
                self.assertEqual(self.post("synthetic", "Bearer " + self.token)[0], 401)
            verify.assert_not_called()

    def test_invalid_key_types_and_header_characters(self):
        with patch.object(ai_hub, "verify_xingchi_key") as verify:
            for value in (None, 1, "", " space", "a\nheader", "nonascii-\u1ec7", "a" * 4097):
                with self.subTest(value_type=type(value).__name__):
                    self.assertEqual(self.post(value, "Bearer " + self.token)[0], 400)
            verify.assert_not_called()
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_verifier_http_errors_and_timeout(self):
        for code in (301, 302, 401, 403, 429, 500):
            with self.subTest(code=code):
                opener = unittest.mock.Mock()
                opener.open.side_effect = HTTPError("https://invalid.test", code, "synthetic", {}, None)
                with patch.object(ai_hub.urllib.request, "build_opener", return_value=opener):
                    self.assertEqual(ai_hub.verify_xingchi_key("synthetic"),
                                     (False, "rejected" if code in (401, 403) else "upstream"))
        opener = unittest.mock.Mock()
        opener.open.side_effect = TimeoutError("synthetic")
        with patch.object(ai_hub.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(ai_hub.verify_xingchi_key("synthetic"), (False, "upstream"))
        self.assertIsNone(ai_hub._NoRedirect().redirect_request(None, None, 302, "", {}, "https://invalid.test"))

    def test_actual_replace_failure_retains_backup_and_cleans_temp(self):
        real_replace = os.replace
        def fail_config(source, target):
            if str(target) == str(self.config):
                raise OSError("synthetic replace failure")
            return real_replace(source, target)
        with patch.object(ai_hub, "verify_xingchi_key", return_value=(True, "")), patch.object(ai_hub.os, "replace", side_effect=fail_config):
            self.assertEqual(self.post("synthetic", "Bearer " + self.token)[0], 500)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(list(self.folder.glob("config.toml.bak.*"))[0].read_bytes(), self.original)
        self.assertEqual(list(self.folder.glob(".aihub-*")), [])

    def test_actual_backup_fsync_failure_keeps_original(self):
        with patch.object(ai_hub, "verify_xingchi_key", return_value=(True, "")), patch.object(ai_hub.os, "fsync", side_effect=OSError("synthetic flush failure")):
            self.assertEqual(self.post("synthetic", "Bearer " + self.token)[0], 500)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(list(self.folder.glob("config.toml.bak.*")), [])
        self.assertEqual(list(self.folder.glob(".aihub-*")), [])


if __name__ == "__main__":
    unittest.main()
