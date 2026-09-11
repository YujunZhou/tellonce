"""Scoped, frozen tellonce selection; no network or operator memory."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from rule_selection import (Rule, RuleSnapshot, SelectionLimits, SelectionError,
                            select_rules, load_snapshot)


def rule(index, **values):
    return Rule(atomic_id=f'rule-{index}', revision=1, rule_text=f'Rule {index}', **values)


class RuleSelectionTest(unittest.TestCase):
    def setUp(self):
        self.snap = RuleSnapshot('experiment/user-a/dataset-a', 2, (rule(1), rule(2)))

    def test_zero_matches_is_success_not_failure(self):
        result = select_rules(self.snap, 'task one', 'task-id', lambda prompt: '[]', expected_namespace=self.snap.namespace)
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.selected_ids, ())
        self.assertEqual(result.attempts, 1)

    def test_semantic_failure_retries_once_and_is_not_zero_match(self):
        seen = []
        def broken(prompt):
            seen.append(prompt)
            return 'not json'
        result = select_rules(self.snap, 'task one', 'task-id', broken, expected_namespace=self.snap.namespace)
        self.assertEqual(result.status, 'error')
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(set(result.failed_ids), {'rule-1', 'rule-2'})

    def test_failure_never_imports_previous_task_or_user(self):
        previous = select_rules(self.snap, 'same prompt', 'first', lambda _: '["rule-1"]', expected_namespace=self.snap.namespace)
        def broken(_):
            raise RuntimeError('offline')
        for snap, task in ((self.snap, 'second'), (RuleSnapshot('other-user', 2, self.snap.rules), 'first')):
            result = select_rules(snap, 'same prompt', task, broken, previous=previous, expected_namespace=snap.namespace)
            self.assertEqual(result.selected_ids, ())
        result = select_rules(self.snap, 'same prompt', 'first', broken, previous=previous, expected_namespace=self.snap.namespace)
        self.assertEqual(result.selected_ids, ('rule-1',))
        self.assertEqual(result.status, 'error')

    def test_new_instructions_do_not_reuse_unverified_previous_applicability(self):
        previous = select_rules(self.snap, 'write a short report', 'first', lambda _: '["rule-1"]', expected_namespace=self.snap.namespace)
        result = select_rules(self.snap, 'this time expand the report', 'first', lambda _: 'bad', previous=previous, expected_namespace=self.snap.namespace)
        self.assertEqual(result.selected_ids, ())

    def test_all_candidates_are_evaluated_and_full_exceptions_preserved(self):
        exception = 'x' * 1500 + ' except for a user-requested detailed appendix'
        rules = tuple(rule(i, does_not_apply_when=exception) for i in range(45))
        snap = RuleSnapshot('one', 1, rules)
        seen = []
        def choose(prompt):
            seen.append(prompt)
            return '["rule-44"]' if '"atomic_id": "rule-44"' in prompt else '[]'
        result = select_rules(snap, 'write an appendix', 'task', choose, expected_namespace=snap.namespace,
                              limits=SelectionLimits(batch_rules=10, prompt_chars=30000))
        self.assertEqual(result.selected_ids, ('rule-44',))
        self.assertEqual(len(seen), 5)
        self.assertIn(exception, result.injection)
        self.assertTrue(all(exception in p for p in seen))

    def test_unknown_ids_are_errors_not_silently_ignored(self):
        result = select_rules(self.snap, 'task', 'id', lambda _: '["rule-1","invented"]', expected_namespace=self.snap.namespace)
        self.assertEqual(result.status, 'error')
        self.assertEqual(result.selected_ids, ())

    def test_budget_omits_whole_rules_and_reports_ids(self):
        large = RuleSnapshot('one', 1, (rule(1, does_not_apply_when='z'*9000),))
        result = select_rules(large, 'task', 'id', lambda _: '["rule-1"]', expected_namespace=large.namespace,
                              limits=SelectionLimits(injection_chars=100))
        self.assertEqual(result.injection, '')
        self.assertEqual(result.omitted_ids, ('rule-1',))
        self.assertEqual(result.status, 'partial')

    def test_unexamined_candidates_are_reported_when_batch_budget_exhausted(self):
        result = select_rules(self.snap, 'task', 'id', lambda _: '[]', expected_namespace=self.snap.namespace,
                              limits=SelectionLimits(batch_rules=1, max_batches=1))
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.unexamined_ids, ('rule-2',))
        self.assertEqual(result.attempts, 1)

    def test_snapshot_roundtrip_is_checked_and_no_raw_source_is_exposed(self):
        value = self.snap.to_dict()
        self.assertEqual(RuleSnapshot.from_dict(value), self.snap)
        value['rules'][0]['rule_text'] = 'changed'
        with self.assertRaises(SelectionError):
            RuleSnapshot.from_dict(value)
        self.assertNotIn('source_text', json.dumps(self.snap.to_dict()))
        with self.assertRaises(SelectionError):
            RuleSnapshot.from_json(self.snap.to_json(), expected_namespace='another-user')
        self.assertEqual(RuleSnapshot.from_json(self.snap.to_json(), expected_namespace=self.snap.namespace), self.snap)
        with self.assertRaises(SelectionError):
            select_rules(self.snap, 'task', 'id', lambda _: '[]', expected_namespace='another-user')

    def test_missing_store_is_not_an_empty_library_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SelectionError):
                load_snapshot(Path(tmp), namespace='explicit-user')

    def test_snapshot_reads_one_committed_generation_and_export_is_frozen(self):
        import memory_store
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_store.MemoryStore(tmp)
            store.initialize()
            store.bind_namespace('one-user')
            def add(key, text):
                store.ensure_turn(key, text)
                generation, _ = store.snapshot()
                store.commit_plan(key, text, {'mutations': [{
                    'operation': 'NEW', 'target_ids': [], 'evidence_spans': [text],
                    'record': {'name': key, 'description': text, 'rule_text': text,
                               'body': text, 'scope': 'global', 'scope_anchor': '',
                               'type': 'preference', 'domain': 'workflow',
                               'condition': '', 'applies_when': '', 'does_not_apply_when': '(none)',
                               'confidence': 'high'},
                }]}, generation)
            add('first', 'Keep reports short.')
            original_connect = sqlite3.connect
            inserted = []
            class InterleavedConnection(sqlite3.Connection):
                def execute(self, sql, *args):
                    cursor = super().execute(sql, *args)
                    if sql.startswith('SELECT key,value') and not inserted:
                        inserted.append(True)
                        add('second', 'Use metric units.')
                    return cursor
            def connect(*args, **kwargs):
                if kwargs.get('uri'):
                    kwargs['factory'] = InterleavedConnection
                return original_connect(*args, **kwargs)
            with mock.patch.object(sqlite3, 'connect', side_effect=connect):
                frozen = load_snapshot(Path(tmp), namespace='one-user')
            self.assertEqual(frozen.generation, 1)
            self.assertEqual(len(frozen.rules), 1)
            current = load_snapshot(Path(tmp), namespace='one-user')
            self.assertEqual(current.generation, 2)
            self.assertEqual(len(current.rules), 2)
            self.assertEqual(len(RuleSnapshot.from_dict(frozen.to_dict()).rules), 1)
            removed_id = current.rules[0].atomic_id
            source = 'Retire that saved report preference.'
            store.ensure_turn('retire', source)
            store.commit_plan('retire', source, {'mutations': [{
                'operation': 'ARCHIVE', 'target_ids': [removed_id], 'record': {},
                'evidence_spans': [source], 'reason': 'User explicitly retires the rule.',
            }]}, current.generation)
            after_retire = load_snapshot(Path(tmp), namespace='one-user')
            self.assertNotIn(removed_id, [r.atomic_id for r in after_retire.rules])
            self.assertIn(removed_id, [r.atomic_id for r in frozen.rules])

    def test_namespace_cannot_relabel_another_users_library(self):
        import memory_store
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_store.MemoryStore(tmp)
            store.initialize()
            store.bind_namespace('user-a')
            self.assertEqual(load_snapshot(Path(tmp), namespace='user-a').rules, ())
            with self.assertRaises(SelectionError):
                load_snapshot(Path(tmp), namespace='user-b')
            with self.assertRaises(memory_store.MemoryStoreError):
                store.bind_namespace('user-b')
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_store.MemoryStore(tmp)
            store.initialize()
            store.ensure_turn('old', 'A prior user instruction.')
            with self.assertRaises(memory_store.MemoryStoreError):
                store.bind_namespace('new-user')

    def test_batch_budget_bounds_prompt_construction_work(self):
        import rule_selection
        snapshot = RuleSnapshot('one', 1, tuple(rule(i) for i in range(1000)))
        with mock.patch.object(rule_selection, 'selection_prompt', wraps=rule_selection.selection_prompt) as build:
            result = select_rules(snapshot, 'task', 'id', lambda _: '[]', expected_namespace=snapshot.namespace,
                                  limits=SelectionLimits(batch_rules=2, max_batches=1))
        self.assertLess(build.call_count, 10)
        self.assertEqual(len(result.unexamined_ids), 998)

    def test_namespaced_init_and_restart_never_import_legacy_markdown(self):
        import memory_store
        import memory_upsert
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'wf-pref-001.md').write_text('---\natomic_id: wf-pref-001\nrule_text: DO_NOT_IMPORT\n---\n')
            store = memory_store.MemoryStore(root)
            with mock.patch.object(memory_store, 'parse_frontmatter', side_effect=AssertionError('legacy import')):
                store.initialize(namespace='new-user')
                memory_upsert._store(root, namespace='new-user')
            self.assertEqual(load_snapshot(root, namespace='new-user').rules, ())

    def test_actual_valid_markdown_is_not_imported_by_namespaced_initialization(self):
        import memory_store
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'wf-pref-001.md').write_text('''---
atomic_id: wf-pref-001
name: another-user-rule
description: Keep other users separate.
type: preference
domain: workflow
scope: global
confidence: high
rule_text: Keep other users separate.
---
This came from a different library.
''')
            store = memory_store.MemoryStore(root)
            store.initialize(namespace='fresh-experiment')
            store.initialize(namespace='fresh-experiment')
            self.assertEqual(store.snapshot()[1], [])

    def test_large_query_is_rejected_before_json_encoding(self):
        with mock.patch.object(json.JSONEncoder, 'iterencode', side_effect=AssertionError('oversized allocation')):
            with self.assertRaises(SelectionError):
                select_rules(self.snap, 'q'*100000, 'id', lambda _: '[]',
                             expected_namespace=self.snap.namespace, limits=SelectionLimits(prompt_chars=2000))

    def test_escaped_oversized_text_is_rejected_before_encoding(self):
        import rule_selection
        with mock.patch.object(json, 'dumps', side_effect=AssertionError('oversized escaped allocation')):
            self.assertIsNone(rule_selection._bounded_json('\x00'*1000, 2000))
            self.assertIsNone(rule_selection._bounded_json({'text': '\x00'*1000}, 2000))

    def test_injection_omission_does_not_materialize_large_rule(self):
        import rule_selection
        snap = RuleSnapshot('one', 1, (rule(1, does_not_apply_when='x'*8000),))
        with mock.patch.object(rule_selection, 'render_rule', side_effect=AssertionError('oversized render')):
            result = select_rules(snap, 'task', 'id', lambda _: '["rule-1"]', expected_namespace='one',
                                  limits=SelectionLimits(injection_chars=100))
        self.assertEqual(result.omitted_ids, ('rule-1',))

    def test_drain_rejects_uninitialized_namespaced_request_before_legacy_import(self):
        import memory_upsert
        import memory_store
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'legacy.md').write_text('---\nrule_text: other user\n---\n')
            queued = memory_upsert.enqueue('Keep reports short.', turn_key='uninitialized',
                                          memory_dir=root, namespace='fresh', force=True, spawn_worker=False)
            with mock.patch.object(memory_store.MemoryStore, '_import_legacy_if_empty', side_effect=AssertionError('legacy read')):
                with self.assertRaises(memory_store.MemoryStoreError):
                    memory_upsert.drain(root)
            self.assertFalse((root/'.tellonce.sqlite3').exists())
            self.assertTrue(Path(queued['request_file']).exists())

    def test_two_namespaces_cannot_initialize_one_directory(self):
        import memory_store
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as tmp:
            def initialize(namespace):
                try:
                    memory_store.MemoryStore(tmp).initialize(namespace=namespace)
                    return namespace
                except memory_store.MemoryStoreError:
                    return None
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(initialize, ['one', 'two']))
            self.assertEqual(sum(value is not None for value in results), 1)
            owner = next(value for value in results if value)
            self.assertEqual(load_snapshot(Path(tmp), namespace=owner).namespace, owner)

    def test_every_emitted_prompt_fits_the_budget_without_field_truncation(self):
        limits = SelectionLimits(prompt_chars=2400)
        snap = RuleSnapshot('user-a', 1, tuple(rule(i, condition='\x00'*50) for i in range(6)))
        prompts = []
        def invoke(prompt):
            prompts.append(prompt)
            return '[]'
        select_rules(snap, 'task', 'id', invoke, expected_namespace='user-a', limits=limits)
        self.assertTrue(prompts)
        self.assertTrue(all(len(prompt) <= limits.prompt_chars for prompt in prompts))

    def test_wrong_namespace_in_queued_request_cannot_learn_into_store(self):
        import memory_store
        import memory_upsert
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = memory_store.MemoryStore(root)
            store.initialize(namespace='user-a')
            queued = memory_upsert.enqueue('Keep reports short.', turn_key='wrong-user',
                                          memory_dir=root, namespace='user-b', force=True, spawn_worker=False)
            with self.assertRaises(memory_store.MemoryStoreError):
                memory_upsert.ingest_request(queued['request_file'], memory_dir=root,
                                             judge_func=lambda *_: {'mutations': []})
            self.assertIsNone(store.get_turn('wrong-user'))
            queued = memory_upsert.enqueue('Keep reports short.', turn_key='missing-user',
                                          memory_dir=root, force=True, spawn_worker=False)
            with self.assertRaises(memory_store.MemoryStoreError):
                memory_upsert.ingest_request(queued['request_file'], memory_dir=root,
                                             judge_func=lambda *_: {'mutations': []})
            self.assertIsNone(store.get_turn('missing-user'))
            with mock.patch.dict(memory_upsert.os.environ, {'PT_MEMORY_NAMESPACE': 'user-a'}):
                with self.assertRaises(memory_store.MemoryStoreError):
                    memory_upsert.ingest_request(queued['request_file'], memory_dir=root,
                                                 judge_func=lambda *_: {'mutations': []})

    def test_enqueue_does_not_invent_request_ownership_from_environment(self):
        import memory_upsert
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(memory_upsert.os.environ, {'PT_MEMORY_NAMESPACE': 'user-a'}):
                queued = memory_upsert.enqueue('Keep reports short.', turn_key='missing',
                                              memory_dir=tmp, force=True, spawn_worker=False)
            self.assertEqual(json.loads(Path(queued['request_file']).read_text())['namespace'], '')

    def test_namespaced_queue_never_attaches_operator_project_context(self):
        import memory_upsert
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(memory_upsert.path_config, 'get_project_root',
                                   side_effect=AssertionError('operator project read')):
                queued = memory_upsert.enqueue('Keep reports short.', turn_key='owned', namespace='user-a',
                                              memory_dir=tmp, force=True, spawn_worker=False)
            self.assertEqual(json.loads(Path(queued['request_file']).read_text())['project_root'], '')

    def test_apply_plan_requires_explicit_matching_ownership(self):
        import memory_upsert
        import memory_store
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_store.MemoryStore(tmp)
            store.initialize(namespace='user-a')
            for supplied in ('', 'user-b', None, 1):
                with mock.patch.dict(memory_upsert.os.environ, {'PT_MEMORY_NAMESPACE': 'user-a'}):
                    with self.assertRaises(memory_store.MemoryStoreError):
                        memory_upsert.apply_plan('Keep reports short.', {'mutations': []},
                                                 turn_key='rejected', memory_dir=tmp, namespace=supplied)
                self.assertIsNone(store.get_turn('rejected'))
            result = memory_upsert.apply_plan('Keep reports short.', {'mutations': []},
                                              turn_key='accepted', memory_dir=tmp, namespace='user-a')
            self.assertEqual(result['status'], 'noop')

    def test_apply_plan_cli_preserves_request_identity(self):
        import memory_upsert
        with tempfile.TemporaryDirectory() as tmp:
            request, plan = Path(tmp)/'request.json', Path(tmp)/'plan.json'
            request.write_text(json.dumps({'source_text': 'Keep reports short.', 'namespace': 'user-a'}))
            plan.write_text('{"mutations": []}')
            with mock.patch.object(memory_upsert, 'apply_plan', return_value={'status': 'noop'}) as apply:
                with mock.patch.object(memory_upsert, '_print_json'):
                    self.assertEqual(memory_upsert.main(['apply-plan', '--request-file', str(request),
                                                        '--plan-file', str(plan), '--memory-dir', tmp]), 0)
                self.assertEqual(apply.call_args.kwargs['namespace'], 'user-a')


if __name__ == '__main__':
    unittest.main()
