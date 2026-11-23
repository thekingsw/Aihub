"""Offline regression tests; never launches Codex or contacts the mailbox."""
import tempfile
import io
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import subprocess
import emi_worker as worker


class WorkerReportTests(unittest.TestCase):
    def test_all_turns_without_prompt_tools_or_tokens(self):
        output = ('user\nPROMPT PRIVATE\ncodex\nFirst answer\n'
                  'exec\nTOOL OUTPUT\ncodex\nSecond answer\ncodex\n'
                  'tokens used / 48,474\n')
        self.assertEqual(worker.extract_agent_reply(output), 'First answer\nSecond answer')

    def test_warning_and_separate_token_count(self):
        output = ('codex\nwarning: Codex is ignoring config\nuser (config)\n'
                  '`config`\n\nAnswer\ntokens used\n48,474\n')
        self.assertEqual(worker.extract_agent_reply(output), 'Answer')
        self.assertEqual(worker.extract_agent_reply(''), '')

    def test_output_saved_and_internal_token_redacted(self):
        with tempfile.TemporaryDirectory(dir=worker.BASE) as folder:
            with patch.object(worker, 'BASE', folder), patch.object(worker, 'read_token', return_value='secret'):
                worker.save_codex_output({'task_id': 'test'}, b'answer secret')
            self.assertEqual((Path(folder) / 'emi-codex-test.log').read_bytes(), b'answer [REDACTED_INTERNAL_TOKEN]')

    def test_full_fallback_survives_delivery_failure(self):
        with tempfile.TemporaryDirectory(dir=worker.BASE) as folder:
            answer = 'Full answer ' * 500
            with patch.dict(worker.PROJECTS, {'test': folder}), patch.object(worker, 'TASK_DONE', folder), \
                 patch.object(worker, 'run_codex', return_value=(True, 'codex\n' + answer)), \
                 patch.object(worker, 'api_post_message', side_effect=OSError('offline')), patch.object(worker, 'log'):
                self.assertFalse(worker.process_task('unused', {'task_id': 'test', 'project': 'test'}))
            self.assertEqual((Path(folder) / 'emi-report-test.md').read_text(encoding='utf-8'), answer.strip())

    def test_report_file_has_priority_and_is_retained(self):
        with tempfile.TemporaryDirectory(dir=worker.BASE) as folder:
            report = Path(folder) / 'emi-report-test.md'
            def fake_run(*args):
                report.write_text('File report', encoding='utf-8')
                return True, 'codex\nFallback'
            with patch.dict(worker.PROJECTS, {'test': folder}), patch.object(worker, 'run_codex', side_effect=fake_run), \
                 patch.object(worker, 'api_post_message', return_value={'id': 'test'}) as post, patch.object(worker, 'log'):
                self.assertTrue(worker.process_task('unused', {'task_id': 'test', 'project': 'test'}))
                self.assertIn('emi-report-test.md', post.call_args.args[-1])
            self.assertTrue(report.exists())

    def test_zero_exit_with_broken_runner_is_failure(self):
        proc = Mock(returncode=0)
        proc.communicate.return_value = (('Failed to create unified exec process: ' + worker.RUNNER_FAILURE).encode(), None)
        with patch.object(worker.subprocess, 'Popen', return_value=proc), \
             patch.object(worker, 'save_codex_output'), patch.object(worker, 'log'):
            ok, output = worker.run_codex({'task_id': 'test'}, 'unused.md')
        self.assertFalse(ok)
        self.assertIn(worker.RUNNER_FAILURE, output)

    def test_log_survives_windows_redirect_encoding(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='cp1252')
        with tempfile.TemporaryDirectory(dir=worker.BASE) as folder:
            log_path = Path(folder) / 'worker.log'
            with patch.object(worker, 'LOG_FILE', str(log_path)), patch.object(worker.sys, 'stdout', stream):
                worker.log('Khởi động tiếng Việt')
            self.assertIn('Khởi động tiếng Việt', log_path.read_text(encoding='utf-8'))
            stream.flush()
            self.assertIn(b'\\u', raw.getvalue())

    def test_mentioning_old_failure_does_not_mark_success_as_failed(self):
        proc = Mock(returncode=0)
        proc.communicate.return_value = (('STATE.md: ' + worker.RUNNER_FAILURE + '\nexec succeeded in 200ms').encode(), None)
        with patch.object(worker.subprocess, 'Popen', return_value=proc), \
             patch.object(worker, 'save_codex_output'), patch.object(worker, 'log'):
            ok, output = worker.run_codex({'task_id': 'test'}, 'unused.md')
        self.assertTrue(ok)

    def test_lock_blocks_second_process_and_releases_on_close(self):
        code = "import emi_worker as w; w.BASE = __import__('sys').argv[1]; lock = w.acquire_worker_lock()"
        with tempfile.TemporaryDirectory(dir=worker.BASE) as folder:
            with patch.object(worker, 'BASE', folder):
                first = worker.acquire_worker_lock()
            try:
                second = subprocess.run([worker.sys.executable, '-c', code, folder], capture_output=True)
                self.assertNotEqual(second.returncode, 0)
            finally:
                first.close()
            third = subprocess.run([worker.sys.executable, '-c', code, folder], capture_output=True)
            self.assertEqual(third.returncode, 0, third.stderr)

    def test_timeout_preserves_output(self):
        proc = Mock()
        proc.communicate.side_effect = [subprocess.TimeoutExpired('codex', 1), (b'codex\nPartial answer', None)]
        with patch.object(worker.subprocess, 'Popen', return_value=proc), \
             patch.object(worker, 'save_codex_output') as save, patch.object(worker, 'log'):
            ok, output = worker.run_codex({'task_id': 'test'}, 'unused.md')
        self.assertFalse(ok)
        self.assertIn('Partial answer', output)
        save.assert_called_once()
        proc.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()
