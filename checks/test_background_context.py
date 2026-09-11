from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import memory_judge
import memory_store
import memory_upsert
import memory_upsert_hook
import transcript_adapter


def entry(role, text, session='one'):
    return json.dumps({'type': role, 'sessionId': session,
                       'message': {'content': text}}, ensure_ascii=False) + '\n'


class BackgroundContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.transcript = self.root / 'one.jsonl'
        self.transcript.write_text(entry('user', 'Please draft the report.') +
                                   entry('assistant', 'Here is a very long report.'))
        self.event = {'session_id': 'one', 'transcript_path': str(self.transcript),
                      'prompt': 'Too long, keep these reports short.', 'turn_id': 't1'}

    def reference(self):
        return transcript_adapter.capture_context_reference(self.event)

    def test_hook_enqueues_reference_without_parsing_transcript(self):
        with mock.patch.object(memory_upsert, 'hooks_enabled', return_value=True), \
             mock.patch.object(memory_upsert_hook.pt_platform, 'is_child_session', return_value=False), \
             mock.patch.object(transcript_adapter, 'read_transcript', side_effect=AssertionError('foreground read')), \
             mock.patch.object(memory_upsert, 'enqueue', return_value={'status': 'queued'}) as enqueue:
            result = memory_upsert_hook.enqueue_from_hook(self.event, 'prompt')
        self.assertEqual(result['status'], 'queued')
        self.assertTrue(enqueue.call_args.kwargs['detect_signal'])
        self.assertEqual(enqueue.call_args.kwargs['context_reference']['session_id'], 'one')
        self.assertNotIn('long report', enqueue.call_args.kwargs['context'])
        self.assertNotIn('clarification_candidates', enqueue.call_args.kwargs)

    def test_hook_does_not_stringify_non_text_prompts_as_user_authorization(self):
        with mock.patch.object(memory_upsert, 'hooks_enabled', return_value=True), \
             mock.patch.object(memory_upsert_hook.pt_platform, 'is_child_session', return_value=False), \
             mock.patch.object(memory_upsert, 'enqueue') as enqueue:
            result = memory_upsert_hook.enqueue_from_hook({**self.event, 'prompt': {'content': 'Remember this.'}}, 'prompt')
        self.assertEqual(result['status'], 'invalid_prompt')
        enqueue.assert_not_called()

    def test_codex_session_metadata_from_another_conversation_is_rejected(self):
        self.transcript.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'other'}})+'\n'+
                                   json.dumps({'type': 'response_item', 'payload': {'type': 'message',
                                       'role': 'user', 'content': 'Different conversation.'}})+'\n')
        self.assertEqual(transcript_adapter.read_context_reference(self.reference())['status'], 'unavailable')

    def test_automatic_retrieval_never_reads_pending_other_conversations(self):
        import retrieve_inject
        with mock.patch.object(memory_store.MemoryStore, 'needs_user_turns',
                               side_effect=AssertionError('cross-conversation read')):
            self.assertEqual(retrieve_inject._render_pending_clarifications('one'), '')

    def test_experimental_store_rejects_facts_even_with_direct_commit(self):
        from checks.test_memory_upsert import plan
        store = memory_store.MemoryStore(self.root/'experiment')
        store.initialize(namespace='run/data/user/method/model')
        source = 'This task has answer 42.'
        store.ensure_turn('fact', source)
        value = plan('NEW', source, evidence_spans=[source])
        value['mutations'][0]['record']['type'] = 'reference'
        with self.assertRaises(memory_store.InvalidPlanError):
            store.commit_plan('fact', source, value, store.snapshot()[0])
        self.assertEqual(store.snapshot()[1], [])

    def test_experimental_resolver_receives_correction_policy(self):
        store = memory_store.MemoryStore(self.root/'experiment')
        store.initialize(namespace='run/data/user/method/model')
        store.ensure_turn('preference', 'Keep reports short.')
        seen = []
        def judge(source, rules, context, *, policy, extra_evidence_sources=None):
            seen.append(policy)
            return {'mutations': []}
        result = memory_upsert.resolve_turn('preference', memory_dir=store.memory_dir, judge_func=judge)
        self.assertEqual(result['status'], 'noop')
        self.assertEqual(seen, ['corrections'])

    def test_correction_prompt_preserves_scope_without_enabling_fact_learning(self):
        prompt = memory_judge.build_prompt('Keep reports short.', [], policy='corrections')
        self.assertIn('Do not store task answers', prompt)
        self.assertNotIn('Persist only durable preferences, recurring pitfalls, friction, user facts', prompt)

    def test_ambiguous_signal_looks_back_only_in_captured_current_conversation(self):
        from checks.test_memory_upsert import plan
        self.transcript.write_text(entry('user', 'For reports, short means under 200 words.')
                                   + ''.join(entry('assistant', 'padding '*100) for _ in range(35)))
        ref = self.reference()
        with self.transcript.open('a') as f:
            f.write(entry('assistant', 'FUTURE_SECRET'))
        queued = memory_upsert.enqueue('Please follow that report length.', turn_key='lookback',
            memory_dir=self.root/'memory', spawn_worker=False, force=True,
            detect_signal=True, context_reference=ref)
        contexts = []
        def judge(source, rules, context):
            contexts.append(context)
            return plan('NEEDS_USER') if len(contexts) == 1 else {'mutations': []}
        def excerpts(source, conversation):
            self.assertIn('under 200 words', conversation)
            self.assertNotIn('FUTURE_SECRET', conversation)
            return ['For reports, short means under 200 words.']
        with mock.patch.object(memory_judge, 'classify_user_signal', return_value=True), \
             mock.patch.object(memory_judge, 'find_context_excerpts', side_effect=excerpts) as lookup:
            result = memory_upsert.ingest_request(queued['request_file'], memory_dir=self.root/'memory', judge_func=judge)
        self.assertEqual(result['status'], 'noop')
        lookup.assert_called_once()
        self.assertNotIn('under 200 words', contexts[0])
        self.assertIn('under 200 words', contexts[1])

    def test_referent_lookup_rejects_invented_and_oversized_quotes(self):
        for value in ('["invented"]', '["'+('a'*8001)+'"]'):
            with mock.patch.object(memory_judge, '_invoke_cli', return_value=value):
                with self.assertRaises(memory_judge.MemoryJudgeError):
                    memory_judge.find_context_excerpts('That is wrong.', 'a'*9000)

    def test_lifecycle_never_truncates_conditions_or_caps_relevant_ids_at_eight(self):
        exception = 'x'*2000 + ' except for the explicitly requested appendix'
        rules = [{'atomic_id': f'r-{i}', 'rule_text': 'Keep reports short.',
                  'does_not_apply_when': exception} for i in range(10)]
        for compact in (False, True):
            self.assertEqual(memory_judge._rule_for_prompt(rules[0], compact)['does_not_apply_when'], exception)
        ids = [r['atomic_id'] for r in rules]
        with mock.patch.object(memory_judge, '_invoke_cli', return_value=json.dumps(ids)):
            self.assertEqual(memory_judge._select_lifecycle_candidates('Use shorter reports.', rules, ''), rules)
        with mock.patch.object(memory_judge, '_invoke_cli', return_value='["unknown"]') as invoke:
            with self.assertRaises(memory_judge.MemoryJudgeError):
                memory_judge._select_lifecycle_candidates('Use shorter reports.', rules, '')
            self.assertEqual(invoke.call_count, 2)

    def test_explicit_transport_never_uses_operator_backend_or_test_plan(self):
        from checks.test_memory_upsert import plan
        source = 'Keep reports short.'
        store = memory_store.MemoryStore(self.root/'experiment')
        store.initialize(namespace='run/data/user/method/model')
        queued = memory_upsert.enqueue(source, turn_key='isolated', memory_dir=store.memory_dir,
            namespace=store.get_namespace(), force=True, spawn_worker=False, detect_signal=True)
        calls = []
        def invoke(prompt):
            calls.append(prompt)
            if 'only admission' in prompt:
                return '{"signal":true}'
            return json.dumps(plan('NEW', source, evidence_spans=[source]))
        with mock.patch.object(memory_judge, '_invoke_cli', side_effect=AssertionError('operator backend')), \
             mock.patch.dict(os.environ, {'PT_TEST_MEMORY_UPSERT_PLAN':'INVALID OPERATOR OVERRIDE'}):
            result = memory_upsert.ingest_request(queued['request_file'], memory_dir=store.memory_dir, invoke=invoke)
        self.assertEqual(result['status'], 'projected')
        self.assertEqual(len(calls), 2)
        self.assertIn('Do not store task answers', calls[1])

    def test_stop_never_reads_or_learns_from_assistant(self):
        with mock.patch.object(memory_upsert, 'hooks_enabled', return_value=True), \
             mock.patch.object(memory_upsert_hook.pt_platform, 'is_child_session', return_value=False), \
             mock.patch.object(transcript_adapter, 'read_transcript', side_effect=AssertionError('stop read')), \
             mock.patch.object(memory_upsert, 'enqueue') as enqueue:
            result = memory_upsert_hook.enqueue_from_hook(self.event, 'stop')
        self.assertEqual(result['status'], 'prompt_only')
        enqueue.assert_not_called()

    def test_background_cannot_read_future_or_other_session(self):
        ref = self.reference()
        with self.transcript.open('a') as stream:
            stream.write(entry('assistant', 'FUTURE_SECRET'))
        context = transcript_adapter.read_context_reference(ref)['context']
        self.assertIn('long report', context)
        self.assertNotIn('FUTURE_SECRET', context)
        self.transcript.write_text(entry('assistant', 'OTHER_SESSION', session='two'))
        self.assertNotIn('OTHER_SESSION', transcript_adapter.read_context_reference(ref)['context'])

    def test_replaced_file_is_not_followed(self):
        ref = self.reference()
        replacement = self.root / 'replacement.jsonl'
        replacement.write_text(entry('assistant', 'UNRELATED'))
        replacement.replace(self.transcript)
        result = transcript_adapter.read_context_reference(ref)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['context'], '')

    def test_in_place_rewrite_then_growth_cannot_replace_captured_context(self):
        ref = self.reference()
        self.transcript.write_text(entry('assistant', 'LATER_CONTENT' * 100))
        result = transcript_adapter.read_context_reference(ref)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('LATER_CONTENT', result['context'])

    def test_no_id_event_uses_stable_boundary_key(self):
        self.event.pop('turn_id')
        first = memory_upsert_hook._event_key(self.event, 'prompt', self.reference())
        self.assertEqual(first, memory_upsert_hook._event_key(self.event, 'prompt', self.reference()))
        with self.transcript.open('a') as stream:
            stream.write(entry('assistant', 'New context.'))
        self.assertNotEqual(first, memory_upsert_hook._event_key(self.event, 'prompt', self.reference()))

    def test_ambiguous_event_identity_is_reported_instead_of_conflating_turns(self):
        event = {'session_id': 'one', 'prompt': 'Yes.'}
        with mock.patch.object(memory_upsert, 'hooks_enabled', return_value=True), \
             mock.patch.object(memory_upsert_hook.pt_platform, 'is_child_session', return_value=False), \
             mock.patch.object(memory_upsert_hook, '_log_hook_error') as log, \
             mock.patch.object(memory_upsert, 'enqueue') as enqueue:
            result = memory_upsert_hook.enqueue_from_hook(event, 'prompt')
        self.assertEqual(result['status'], 'missing_event_identity')
        enqueue.assert_not_called()
        log.assert_called_once()

    def test_non_scalar_event_id_and_unbound_timestamp_are_rejected(self):
        self.assertEqual(memory_upsert_hook._event_key({'session_id': 'one', 'turn_id': {}}, 'prompt'), '')
        self.assertEqual(memory_upsert_hook._event_key({'timestamp': 1234, 'prompt': 'Yes'}, 'prompt'), '')
        self.assertEqual(memory_upsert_hook._event_key({'session_id': 'one', 'timestamp': {}}, 'prompt'), '')

    def test_oldest_fingerprinted_page_has_no_unreachable_cursor(self):
        self.transcript.write_text(''.join(entry('assistant', 'message-'+str(i)+'x'*100) for i in range(800)))
        ref = self.reference()
        self.assertGreater(ref['start'], 0)
        page = transcript_adapter.read_context_reference(ref)
        self.assertTrue(page['truncated'])
        self.assertIsNone(page['next_before'])

    def test_lease_heartbeat_cannot_extend_another_worker_or_expired_lease(self):
        store = memory_store.MemoryStore(self.root/'lease-memory')
        store.initialize()
        store.ensure_turn('lease', 'Keep reports short.')
        owner = store.claim_turn('lease', lease_seconds=30)
        self.assertTrue(store.renew_lease('lease', owner))
        self.assertFalse(store.renew_lease('lease', 'not-owner'))
        with mock.patch.object(memory_store.time, 'time', return_value=10**12):
            self.assertFalse(store.renew_lease('lease', owner))

    def test_codex_response_item_is_context_but_tool_output_is_not_user_source(self):
        rows = [
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
              'content': [{'type': 'input_text', 'text': 'Write a short report.'}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
              'content': [{'type': 'output_text', 'text': 'A long report.'}]}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output',
              'output': 'TOOL_NOT_USER_INSTRUCTION'}},
        ]
        self.transcript.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        text = transcript_adapter.read_context_reference(self.reference())['context']
        self.assertIn('User: Write a short report.', text)
        self.assertIn('Assistant: A long report.', text)
        self.assertNotIn('TOOL_NOT_USER_INSTRUCTION', text)

    def test_invalid_signal_response_does_not_count_as_no_signal(self):
        with mock.patch.object(memory_judge, '_invoke_cli', return_value='{"signal":"false"}'):
            with self.assertRaises(memory_judge.MemoryJudgeError):
                memory_judge.classify_user_signal('Please keep it short.')

    def test_signal_error_retries_without_reading_context(self):
        queued = memory_upsert.enqueue('Too long.', turn_key='signal-error', memory_dir=self.root/'memory',
                                      spawn_worker=False, force=True, detect_signal=True,
                                      context_reference=self.reference())
        with mock.patch.object(memory_judge, 'classify_user_signal', side_effect=RuntimeError('offline')), \
             mock.patch.object(transcript_adapter, 'read_context_reference', side_effect=AssertionError('untriggered read')):
            result = memory_upsert.ingest_request(queued['request_file'], memory_dir=self.root/'memory')
        self.assertEqual(result['status'], 'pending')
        self.assertIn('offline', result['error'])

    def test_install_moves_enqueue_off_stop_without_removing_other_hooks(self):
        import _install_merge_settings as installer
        settings = self.root / 'settings.json'
        hooks_dir = str(self.root / 'hooks')
        settings.write_text(json.dumps({'hooks': {'Stop': [{'_pt_managed': True, 'hooks': [
            {'type': 'command', 'command': hooks_dir+'/memory-upsert-enqueue.sh'},
            {'type': 'command', 'command': 'some-other-hook'},
        ]}]}}))
        installer.cmd_add(str(settings), hooks_dir)
        value = json.loads(settings.read_text())['hooks']
        self.assertTrue(any(h['command'] == 'some-other-hook' for group in value['Stop'] for h in group['hooks']))
        self.assertFalse(any('memory-upsert-enqueue' in h['command'] for group in value['Stop'] for h in group['hooks']))
        self.assertEqual(sum('memory-upsert-enqueue' in h['command'] for group in value['UserPromptSubmit'] for h in group['hooks']), 1)

    def test_backward_paging_has_fixed_boundary(self):
        self.transcript.write_text(''.join(entry('assistant', f'message-{i}') for i in range(30)))
        ref = self.reference()
        first = transcript_adapter.read_context_reference(ref, max_bytes=500)
        self.assertIn('message-29', first['context'])
        self.assertTrue(first['truncated'])
        second = transcript_adapter.read_context_reference(ref, before=first['next_before'], max_bytes=500)
        self.assertNotIn('message-29', second['context'])
        self.assertTrue(second['context'])

    def test_non_signal_does_not_read_context_or_call_resolver(self):
        queued = memory_upsert.enqueue('What is 2+2?', turn_key='plain', memory_dir=self.root/'memory',
                                      spawn_worker=False, force=True, detect_signal=True,
                                      context_reference=self.reference())
        with mock.patch.object(memory_judge, 'classify_user_signal', return_value=False), \
             mock.patch.object(transcript_adapter, 'read_context_reference', side_effect=AssertionError('untriggered read')), \
             mock.patch.object(memory_judge, 'judge_plan', side_effect=AssertionError('untriggered extraction')):
            result = memory_upsert.ingest_request(queued['request_file'], memory_dir=self.root/'memory')
        self.assertEqual(result['status'], 'noop')
        self.assertEqual(memory_store.MemoryStore(self.root/'memory').snapshot()[1], [])

    def test_positive_signal_resolves_context_once_and_preserves_source(self):
        queued = memory_upsert.enqueue(self.event['prompt'], turn_key='correction', memory_dir=self.root/'memory',
                                      spawn_worker=False, force=True, detect_signal=True,
                                      context_reference=self.reference())
        captured = []
        def judge(source, rules, context):
            captured.append((source, context))
            return {'mutations': []}
        with mock.patch.object(memory_judge, 'classify_user_signal', return_value=True) as signal:
            result = memory_upsert.ingest_request(queued['request_file'], memory_dir=self.root/'memory', judge_func=judge)
            memory_upsert.resolve_turn('correction', memory_dir=self.root/'memory', judge_func=judge)
        self.assertEqual(result['status'], 'noop')
        signal.assert_called_once_with(self.event['prompt'])
        self.assertEqual(captured[0][0], self.event['prompt'])
        self.assertIn('long report', captured[0][1])


if __name__ == '__main__':
    unittest.main()
