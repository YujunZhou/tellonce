"""Background learning stays light, and a refused legacy import never hides rules."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))

import memory_judge  # noqa: E402
import memory_store  # noqa: E402
import memory_upsert  # noqa: E402

RULE = (
    "---\n"
    "atomic_id: wf-pref-501\n"
    "type: preference\n"
    "domain: workflow\n"
    "scope: global\n"
    "description: Use three-line tables in LaTeX.\n"
    "confidence: high\n"
    "---\n"
    "Use booktabs three-line tables.\n"
)


def _retrieve(memory_dir: Path, home: Path) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PT_', 'B5_'))}
    env.update(HOME=str(home), B5_MEMORY_DIR=str(memory_dir), B5_STATE_DIR=str(home),
               B5_OBS_LOG_DIR=str(home), B5_PROJECT_ROOT=str(home))
    proc = subprocess.run(
        [sys.executable, str(ROOT / 'lib' / 'retrieve_inject.py')],
        input=json.dumps({'prompt': 'make the results table', 'session_id': 's1',
                          'hook_event_name': 'UserPromptSubmit'}),
        capture_output=True, text=True, env=env, cwd=str(home), timeout=60)
    return proc.stdout


class LegacyImportCases(unittest.TestCase):
    def test_non_rule_markdown_does_not_block_import(self):
        with tempfile.TemporaryDirectory() as td:
            memory_dir = Path(td)
            (memory_dir / 'wf-pref-501.md').write_text(RULE, encoding='utf-8')
            (memory_dir / 'INDEX_global.md').write_text('# index\n- a link\n', encoding='utf-8')
            store = memory_store.MemoryStore(memory_dir)
            store.initialize()
            self.assertEqual([r['atomic_id'] for r in store.all_records()], ['wf-pref-501'])
            self.assertEqual((memory_dir / 'INDEX_global.md').read_text(encoding='utf-8'),
                             '# index\n- a link\n')

    def test_refused_import_still_injects_markdown_rules(self):
        with tempfile.TemporaryDirectory() as td:
            memory_dir = Path(td) / 'memory'
            home = Path(td) / 'home'
            memory_dir.mkdir()
            home.mkdir()
            (memory_dir / 'wf-pref-501.md').write_text(RULE, encoding='utf-8')
            (memory_dir / 'pref_broken.md').write_text('rule without frontmatter', encoding='utf-8')
            with self.assertRaises(memory_store.MemoryStoreError):
                memory_store.MemoryStore(memory_dir).initialize()
            self.assertTrue((memory_dir / memory_store.DB_FILENAME).exists())
            self.assertIn('wf-pref-501', _retrieve(memory_dir, home))


class InboxOrderCases(unittest.TestCase):
    def test_requests_written_at_the_same_instant_keep_enqueue_order(self):
        # Regression: two requests inside one clock tick used to sort by their
        # random suffix, so a newer instruction could be processed first.
        with tempfile.TemporaryDirectory() as td:
            memory_dir = Path(td)
            paths = []
            with mock.patch.object(memory_upsert.time, 'time_ns', return_value=1_700_000_000_000_000_000), \
                    mock.patch.object(memory_upsert.time, 'time', return_value=1_700_000_000.0):
                for text in ('old', 'new'):
                    path, published = memory_upsert._write_inbox_request({'source_text': text}, memory_dir)
                    self.assertTrue(published)
                    paths.append(path)
            pending = memory_upsert._pending_request_paths(memory_dir)
            self.assertEqual(pending, paths)
            self.assertEqual([json.loads(p.read_text(encoding='utf-8'))['source_text'] for p in pending],
                             ['old', 'new'])


class LeanCliCallCases(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in list(os.environ):
            if key.startswith(('PT_MEMORY_UPSERT', 'B5_MEMORY_UPSERT')):
                del os.environ[key]

    def _run(self, returncodes):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            code = returncodes[len(calls) - 1]
            return subprocess.CompletedProcess(cmd, code, stdout='```json\n{"signal": true}\n```',
                                               stderr='' if code == 0 else 'error: unknown option')
        with mock.patch.object(memory_judge.subprocess, 'run', side_effect=fake_run):
            result = memory_judge._invoke_cli('prompt')
        return result, calls

    def test_claude_call_uses_small_model_and_lean_flags(self):
        result, calls = self._run([0])
        self.assertEqual(json.loads(result), {'signal': True})
        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        self.assertEqual(cmd[cmd.index('--model') + 1], 'haiku')
        self.assertEqual(cmd[cmd.index('--tools') + 1], '')
        self.assertIn('--system-prompt', cmd)
        self.assertIn('--strict-mcp-config', cmd)

    def test_rejected_lean_call_retries_once_with_plain_call(self):
        result, calls = self._run([1, 0])
        self.assertEqual(json.loads(result), {'signal': True})
        self.assertEqual(len(calls), 2)
        self.assertNotIn('--tools', calls[1])
        self.assertNotIn('--model', calls[1])

    def test_explicit_model_is_kept(self):
        os.environ['PT_MEMORY_UPSERT_MODEL'] = 'sonnet'
        _result, calls = self._run([0])
        cmd = calls[0]
        self.assertEqual(cmd[cmd.index('--model') + 1], 'sonnet')

    def test_code_fence_strip_leaves_plain_text_alone(self):
        self.assertEqual(memory_judge._strip_code_fence('{"a": 1}'), '{"a": 1}')
        self.assertEqual(memory_judge._strip_code_fence('```\n[1]\n```'), '[1]')
        self.assertEqual(memory_judge._strip_code_fence('x ```a``` y ```b```'), 'x ```a``` y ```b```')


if __name__ == '__main__':
    unittest.main()
