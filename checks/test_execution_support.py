"""Offline execution-support admission and immutable publication contracts."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from rule_selection import Rule, RuleSnapshot
from execution_support import Event, Observation, Support, compile_support, check_rule, SupportError, canonical, rule_digest
from memory_publication import prepare_publication, publish, load_publication, Publication
from memory_runtime import TaskMemory, all_rule_text
from rule_selection import SelectionLimits
from training_memory import TrainingMemory


def candidate():
    return {'tier': 'deterministic', 'stages': ['delivery'], 'reason': 'Explicit JSON keys.',
            'check': {'operation': 'json_object_keys', 'required': ['answer'], 'exact': True},
            'cases': [
                {'expected': 'pass', 'observation': {'stage': 'delivery', 'delivery': '{"answer":1}'}},
                {'expected': 'fail', 'observation': {'stage': 'delivery', 'delivery': '{}'}},
                {'expected': 'not_applicable', 'observation': {'stage': 'pre_action', 'action': 'read'}},
            ]}


def transport(value=None):
    value = candidate() if value is None else value
    def invoke(prompt):
        if 'Independently review' in prompt:
            return '{"approved":true,"meaning_clear":true,"reason":"Matches the rule and cases."}'
        return json.dumps(value)
    return invoke


class ExecutionSupportTest(unittest.TestCase):
    def setUp(self):
        self.rule = Rule('r1', 1, 'Return a JSON object containing exactly the answer key.')

    def test_semantic_multistage_compilation_requires_only_current_stage_evidence(self):
        rule = Rule('separate', 1, 'Keep the two people separate in plans, files and responses.')
        fields = {'pre_action': ['task', 'action'], 'artifact': ['task', 'artifact'],
                  'delivery': ['task', 'delivery', 'events']}
        value = {'tier': 'semantic', 'stages': list(fields), 'reason': 'Observable at each stage.',
            'check': {'applicability': 'When discussing both people.', 'requirement': rule.rule_text,
                'violation': 'People are blended.', 'non_violation': 'People are kept separate.',
                'evidence_fields': fields}, 'cases': []}
        for stage, target in (('pre_action', 'action'), ('artifact', 'artifact'), ('delivery', 'delivery')):
            for status in ('pass', 'fail', 'not_applicable'):
                value['cases'].append({'expected': status, 'observation': {
                    'stage': stage, 'task': 'Both people.', target: status}})
        seen = []
        def invoke(prompt):
            if 'Evaluate ONE learned rule' in prompt:
                observation = json.loads(prompt.split('\n', 1)[1])['observation']
                target = {'pre_action': 'action', 'artifact': 'artifact', 'delivery': 'delivery'}[observation['stage']]
                seen.append(observation)
                return canonical({'status': observation[target], 'reason': 'Synthetic verdict.',
                                  'evidence': [observation[target]]})
            return transport(value)(prompt)
        support = compile_support(rule, invoke, compiler_id='stage-evidence-test')
        self.assertEqual(support.tier, 'semantic', support.reason)
        self.assertEqual(len(seen), 9)
        self.assertTrue(json.loads(support.validation_json)['admitted'])
        observation = Observation('new-task', 1, 'pre_action', 'Both people.', action='pass')
        self.assertEqual(check_rule(rule, support, observation, invoke).status, 'pass')
        absent = Observation('new-task', 2, 'pre_action', 'Both people.')
        before = len(seen)
        self.assertEqual(check_rule(rule, support, absent, invoke).status, 'unknown')
        self.assertEqual(len(seen), before)

    def test_semantic_stage_evidence_schema_cannot_silently_omit_a_stage(self):
        check = {'applicability': 'All tasks.', 'requirement': 'Separate people.',
                 'violation': 'Blended.', 'non_violation': 'Separate.',
                 'evidence_fields': {'pre_action': ['action']}}
        for fields in ({'pre_action': ['action']}, {'pre_action': []},
                       {'pre_action': ['action', 'action'], 'delivery': ['delivery']},
                       {'pre_action': ['future'], 'delivery': ['delivery']},
                       {'unknown_stage': ['action']}):
            with self.subTest(fields=fields), self.assertRaises(SupportError):
                Support(rule_digest(self.rule), 'test', 'semantic', ('pre_action', 'delivery'),
                        canonical({**check, 'evidence_fields': fields}), '{}', 'Invalid map.')

    def test_semantic_file_evidence_can_quote_actual_multiline_content_in_structured_artifact(self):
        rule=Rule('lines',1,'Keep the supplied two lines unchanged.')
        rubric={'applicability':'When writing this file.','requirement':'Keep both lines.',
                'violation':'A line was changed.','non_violation':'Both lines are unchanged.',
                'evidence_fields':['artifact']}
        support=Support(rule_digest(rule),'synthetic','semantic',('artifact',),canonical(rubric),
            canonical({'admitted':True,'review':{'approved':True,'meaning_clear':True}}),'Visible text.')
        text='first line\nsecond line'
        observation=Observation('task',1,'artifact','Write the file.',
            artifact=canonical({'path':'result.txt','content':text,'kind':'text'}))
        calls=[]
        def invoke(prompt):
            calls.append(prompt)
            return canonical({'status':'pass','reason':'Both actual lines are present.','evidence':[text]})
        result=check_rule(rule,support,observation,invoke)
        self.assertEqual(result.status,'pass')
        self.assertEqual(result.evidence,(text,))
        self.assertEqual(len(calls),1)

    def test_semantic_evidence_accepts_exact_serialized_observation_fields_only(self):
        rule = Rule('people', 1, 'Analyze Yuki and Hannah separately.')
        rubric = {'applicability': 'Both Yuki and Hannah.', 'requirement': rule.rule_text,
                  'violation': 'Blending people.', 'non_violation': 'Separate analysis.',
                  'evidence_fields': {'delivery': ['task', 'delivery']}}
        support = Support(rule_digest(rule), 'synthetic', 'semantic', ('delivery',), canonical(rubric),
            canonical({'admitted': True, 'review': {'approved': True, 'meaning_clear': True}}), 'Visible people.')
        observation = Observation('task', 1, 'delivery', "Analyze Omar's situation.",
                                  delivery="Omar's solution is a document checklist.")
        def verdict(quote):
            return lambda _: canonical({'status': 'not_applicable', 'reason': 'Only Omar is involved.',
                                         'evidence': [quote]})
        result = check_rule(rule, support, observation, verdict('"task":"Analyze Omar\'s situation."'))
        self.assertEqual(result.status, 'not_applicable')
        for quote in ('"task":"Analyze Hannah\'s situation."',
                      '"requirement":"Analyze Yuki and Hannah separately."'):
            with self.subTest(quote=quote):
                result = check_rule(rule, support, observation, verdict(quote))
                self.assertEqual(result.status, 'error')

    def test_deleted_artifact_does_not_fail_a_predicate_on_existing_file_name(self):
        rule=Rule('name',1,'Never name a file secret.txt.')
        admitted=canonical({'admitted':True,'review':{'approved':True,'meaning_clear':True}})
        check={'operation':'json_field','target':'artifact','path':['path'],
               'predicate':'basename_not_in','value':['secret.txt']}
        support=Support(rule_digest(rule),'synthetic','deterministic',('artifact',),canonical(check),admitted,'Name.')
        deleted=Observation('task',1,'artifact','Task.',artifact=canonical({'path':'secret.txt','content':None,'kind':'deleted'}))
        self.assertEqual(check_rule(rule,support,deleted).status,'not_applicable')
        no_delete=Rule('delete',1,'Never delete any files.')
        check.update(path=['kind'],predicate='not_equals',value='deleted')
        support=Support(rule_digest(no_delete),'synthetic','deterministic',('artifact',),canonical(check),admitted,'No deletion.')
        self.assertEqual(check_rule(no_delete,support,deleted).status,'fail')

    def test_compilation_prepares_validated_support_before_target_exists(self):
        support = compile_support(self.rule, transport(), compiler_id='test-compiler')
        self.assertEqual(support.tier, 'deterministic')
        result = check_rule(self.rule, support, Observation('task1', 1, 'delivery', 'Solve task.', delivery='{}'))
        self.assertEqual(result.status, 'fail')
        good = check_rule(self.rule, support, Observation('task2', 1, 'delivery', 'Another task.', delivery='{"answer":2}'))
        self.assertEqual(good.status, 'pass')
        self.assertNotEqual(result.observation_digest, good.observation_digest)

    def test_repair_description_uses_only_requested_pinned_rule_with_all_conditions(self):
        scoped = Rule('scoped',1,'Use the approved report format.',scope='project',scope_anchor='Project A',
                      condition='When preparing a report.',does_not_apply_when='Except informal drafts.')
        publication = prepare_publication(RuleSnapshot('repair-owner',1,(self.rule,scoped)),transport(),compiler_id='test')
        runtime = TaskMemory(publication,expected_namespace='repair-owner',task_id='t',public_task='Report.',
                             invoke=lambda prompt:'[]',training=False)
        selected = runtime.describe_rules((rule_digest(scoped),))
        self.assertEqual(set(selected),{rule_digest(scoped)})
        for text in ('Use the approved report format.','Project A','When preparing a report.','Except informal drafts.'):
            self.assertIn(text,selected[rule_digest(scoped)])
        self.assertNotIn(self.rule.rule_text,str(selected))
        with self.assertRaises(SupportError):
            runtime.describe_rules(('foreign-hash',))

    def test_broken_self_tests_and_unreviewed_checkers_become_reminders(self):
        bad = candidate()
        bad['cases'][0]['expected'] = 'fail'
        support = compile_support(self.rule, transport(bad), compiler_id='test')
        self.assertEqual(support.tier, 'reminder')
        def reject(prompt):
            if 'Independently review' in prompt:
                return '{"approved":false,"meaning_clear":true,"reason":"Overbroad."}'
            return json.dumps(candidate())
        self.assertEqual(compile_support(self.rule, reject, compiler_id='test').tier, 'reminder')

    def test_structured_file_check_binds_target_and_missing_evidence_is_unknown(self):
        rule = Rule('path', 1, 'Never target a file named secrets.env in any action.')
        value = {'tier': 'deterministic', 'stages': ['pre_action'], 'reason': 'Literal file name.',
                 'check': {'operation': 'json_field', 'target': 'action', 'path': ['arguments', 'path'],
                           'predicate': 'basename_not_in', 'value': ['secrets.env']},
                 'cases': [
                     {'expected': 'pass', 'observation': {'stage': 'pre_action', 'action': '{"arguments":{"path":"/work/a.py"}}'}},
                     {'expected': 'fail', 'observation': {'stage': 'pre_action', 'action': '{"arguments":{"path":"/work/secrets.env"}}'}},
                     {'expected': 'not_applicable', 'observation': {'stage': 'delivery', 'delivery': 'Done.'}}]}
        support = compile_support(rule, transport(value), compiler_id='test')
        self.assertEqual(support.tier, 'deterministic')
        for path, expected in [('/x/secrets.env', 'fail'), ('C:\\x\\secrets.env', 'fail'),
                               ('/x/other.env', 'pass'), (None, 'unknown'), ({}, 'unknown')]:
            result = check_rule(rule, support, Observation('t', 1, 'pre_action', '',
                                action=json.dumps({'arguments': {'path': path}})))
            self.assertEqual(result.status, expected)
        for action in ('{}', 'not JSON', '{"arguments":{}}'):
            self.assertEqual(check_rule(rule, support, Observation('t', 1, 'pre_action', '', action=action)).status, 'unknown')

    def test_negative_structured_predicates_do_not_pass_non_scalar_evidence(self):
        for predicate, expected in [('not_equals', 'forbidden'), ('none_of', ['forbidden'])]:
            support = Support(rule_digest(self.rule), 'test', 'deterministic', ('delivery',),
                canonical({'operation': 'json_field', 'target': 'delivery', 'path': ['answer'],
                           'predicate': predicate, 'value': expected}),
                canonical({'admitted': True, 'review': {'approved': True, 'meaning_clear': True}}), 'Synthetic predicate.')
            for evidence in (None, [], {}, 1.5):
                result = check_rule(self.rule, support, Observation('t', 1, 'delivery', '',
                                   delivery=json.dumps({'answer': evidence})))
                self.assertEqual(result.status, 'unknown')

    def test_meaning_ambiguity_does_not_activate_even_as_reminder(self):
        support = compile_support(self.rule, transport({'tier':'pending', 'stages':[], 'check':{},
            'cases':[], 'reason':'Cannot determine which format is meant.'}), compiler_id='test')
        self.assertEqual(support.tier, 'pending')
        malformed = {'tier':'pending', 'stages':[], 'check':{}, 'cases':[], 'reason':'Ambiguous.',
                     'unsupported_extra_field': 'bad shape'}
        self.assertEqual(compile_support(self.rule, transport(malformed), compiler_id='test').tier, 'pending')
        unclear_scope = Rule('unclear', 1, 'Use that format for those tasks.', scope='unclear')
        pending = compile_support(unclear_scope, transport(), compiler_id='test')
        self.assertEqual(pending.tier, 'pending')
        with self.assertRaises(SupportError):
            Publication(RuleSnapshot('user-a', 1, (unclear_scope,)), 'test',
                        (Support(rule_digest(unclear_scope), 'test', 'reminder', (), '{}', '{}', 'Still unclear.'),))

    def test_condition_or_exception_cannot_be_dropped_by_simple_deterministic_check(self):
        scoped = Rule('r1', 1, self.rule.rule_text, does_not_apply_when='Except when I request prose.')
        self.assertEqual(compile_support(scoped, transport(), compiler_id='test').tier, 'reminder')

    def test_different_rule_revision_cannot_use_stale_support(self):
        support = compile_support(self.rule, transport(), compiler_id='test')
        with self.assertRaises(SupportError):
            check_rule(Rule('r1', 2, 'Return prose.'), support,
                       Observation('task', 1, 'delivery', '', delivery='prose'))

    def test_unvalidated_runtime_checker_and_future_events_are_rejected(self):
        support = Support(rule_digest(self.rule), 'test', 'deterministic', ('delivery',),
                          canonical(candidate()['check']), '{}', 'No admission evidence.')
        with self.assertRaises(SupportError):
            check_rule(self.rule, support, Observation('task', 1, 'delivery', '', delivery='{}'))
        with self.assertRaises(SupportError):
            Observation('task', 1, 'delivery', '', events=(Event(2, 'tool', 'future', 'task'),))
        with self.assertRaises(SupportError):
            Observation('task', 2, 'delivery', '', events=(Event(1, 'tool', 'other task', 'other'),))

    def test_semantic_check_preserves_event_evidence_and_bounds_technical_retry(self):
        value = {'tier': 'semantic', 'stages': ['pre_action'], 'reason': 'Earlier event is required.',
                 'check': {'applicability': 'Before publishing.', 'requirement': 'Run checks first.',
                           'violation': 'Publishing without earlier checks.',
                           'non_violation': 'Earlier checks exist or this is not publishing.',
                           'evidence_fields': ['action', 'events']},
                 'cases': [
                     {'expected': 'pass', 'observation': {'stage': 'pre_action', 'action': 'publish',
                         'events': [{'sequence': 1, 'kind': 'tool_result', 'content': 'checks passed'}]}},
                     {'expected': 'fail', 'observation': {'stage': 'pre_action', 'action': 'publish'}},
                     {'expected': 'not_applicable', 'observation': {'stage': 'delivery', 'delivery': 'Done.'}},
                 ]}
        rule = Rule('checks-first', 1, 'Run checks before publishing.')
        def invoke(prompt):
            if 'Evaluate ONE learned rule' in prompt:
                return json.dumps({'status': 'pass' if 'checks passed' in prompt else 'fail',
                                   'reason': 'Checked actual earlier events.', 'evidence': ['publish']})
            return transport(value)(prompt)
        support = compile_support(rule, invoke, compiler_id='test')
        self.assertEqual(support.tier, 'semantic')
        observation = Observation('task', 2, 'pre_action', '', action='publish',
                                  events=(Event(1, 'tool_result', 'checks passed', 'task'),))
        self.assertEqual(check_rule(rule, support, observation, invoke).status, 'pass')
        before = support.digest
        calls = []
        def broken(prompt):
            calls.append(prompt)
            return '{"status":"fail","reason":"guess","evidence":["invented"]}'
        result = check_rule(rule, support, observation, broken)
        self.assertEqual((result.status, result.attempts, len(calls)), ('error', 2, 2))
        self.assertEqual(support.digest, before)
        absent = Observation('task', 3, 'pre_action', '')
        self.assertEqual(check_rule(rule, support, absent, broken).status, 'unknown')

    def test_publication_has_rules_and_support_from_one_version_and_is_frozen(self):
        snap = RuleSnapshot('run/data/user/method/model', 1, (self.rule,))
        publication = prepare_publication(snap, transport(), compiler_id='test')
        frozen = publication.to_json()
        with tempfile.TemporaryDirectory() as tmp:
            publish(Path(tmp), publication, expected_namespace=snap.namespace)
            current = load_publication(Path(tmp), expected_namespace=snap.namespace)
            self.assertEqual(current.to_json(), frozen)
            newer = prepare_publication(RuleSnapshot(snap.namespace, 2, ()), transport(), compiler_id='test')
            publish(Path(tmp), newer, expected_namespace=snap.namespace)
            with self.assertRaises(SupportError):
                publish(Path(tmp), publication, expected_namespace=snap.namespace)
            self.assertEqual(Publication.from_json(frozen, expected_namespace=snap.namespace).generation, 1)
            with self.assertRaises(SupportError):
                Publication.from_json(frozen, expected_namespace='another-user')

    def test_incremental_compilation_reuses_only_exact_rule_and_compiler(self):
        snap = RuleSnapshot('user-a', 1, (self.rule,))
        previous = prepare_publication(snap, transport(), compiler_id='test')
        def forbidden(_):
            raise AssertionError('unchanged compiler support should be reused')
        newer = prepare_publication(RuleSnapshot('user-a', 2, (self.rule,)), forbidden,
                                    compiler_id='test', previous=previous)
        self.assertEqual(newer.supports, previous.supports)
        pending = prepare_publication(RuleSnapshot('user-a', 3, (self.rule,)),
            transport({'tier':'pending','stages':[],'check':{},'cases':[],'reason':'Ambiguous.'}),
            compiler_id='new-compiler', previous=previous)
        self.assertEqual(pending.active_snapshot.rules, ())
        self.assertEqual(pending.pending_ids, ('r1',))

    def test_publication_rejects_tampered_artifacts_and_symlink_pointers(self):
        snap = RuleSnapshot('user-a', 1, (self.rule,))
        publication = prepare_publication(snap, transport(), compiler_id='test')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = publish(root, publication, expected_namespace='user-a')
            artifact.chmod(0o600)
            artifact.write_text(publication.to_json().replace('Explicit JSON keys.', 'Tampered reason.'))
            with self.assertRaises(SupportError):
                load_publication(root, expected_namespace='user-a')
            (root/'current.json').unlink()
            (root/'current.json').symlink_to(artifact)
            with self.assertRaises((SupportError, OSError)):
                load_publication(root, expected_namespace='user-a')

    def test_task_boundary_pins_support_and_test_rejects_memory_updates(self):
        first = prepare_publication(RuleSnapshot('user-a', 1, (self.rule,)), transport(), compiler_id='test')
        later = prepare_publication(RuleSnapshot('user-a', 2, ()), transport(), compiler_id='test')
        memory = TaskMemory(first, expected_namespace='user-a', task_id='task1', public_task='Solve it.',
                            invoke=lambda _: '["r1"]', training=True)
        memory.user_boundary('Please proceed.')
        memory.expand_scope('Now deliver the answer.')
        self.assertEqual(memory.publication.digest, first.digest)
        self.assertEqual(memory.check(Observation('task1', 1, 'delivery', '', delivery='{}'))[0].status, 'fail')
        memory.user_boundary('Next round.', ready_publication=later)
        self.assertEqual(memory.check(Observation('task1', 2, 'delivery', '', delivery='{}')), ())
        frozen = TaskMemory(first, expected_namespace='user-a', task_id='task2', public_task='Other task.',
                            invoke=lambda _: '[]', training=False)
        with self.assertRaises(SupportError):
            frozen.user_boundary('Correction.', ready_publication=later)
        self.assertEqual(frozen.user_boundary('Correction.').result.selected_ids, ())
        with self.assertRaises(SupportError):
            frozen.check(Observation('task1', 1, 'delivery', '', delivery='{}'))

    def test_all_rule_ablation_never_silently_truncates(self):
        publication = prepare_publication(RuleSnapshot('user-a', 1, (self.rule,)), transport(), compiler_id='test')
        self.assertIn(self.rule.rule_text, all_rule_text(publication, max_chars=10000))
        with self.assertRaises(SupportError):
            all_rule_text(publication, max_chars=10)

    def test_oversized_task_selection_is_visible_error_without_blocking_answer(self):
        publication = prepare_publication(RuleSnapshot('user-a', 1, (self.rule,)), transport(), compiler_id='test')
        def forbidden(_):
            raise AssertionError('oversized query should never be sent')
        memory = TaskMemory(publication, expected_namespace='user-a', task_id='task', public_task='x'*10000,
                            invoke=forbidden, training=False, limits=SelectionLimits(prompt_chars=1000))
        selection = memory.user_boundary('Proceed.')
        self.assertEqual(selection.result.status, 'error')
        self.assertEqual(selection.result.injection, '')
        self.assertEqual(selection.result.unexamined_ids, ('r1',))

    def test_training_queue_publish_freeze_lifecycle_uses_only_explicit_transport(self):
        from checks.test_memory_upsert import plan
        calls = []
        def invoke(prompt):
            calls.append(prompt)
            if 'only admission' in prompt:
                return '{"signal":true}'
            if 'semantic memory resolver' in prompt:
                return json.dumps(plan('NEW', self.rule.rule_text, evidence_spans=[self.rule.rule_text]))
            return transport()(prompt)
        with tempfile.TemporaryDirectory() as tmp:
            memory = TrainingMemory.create(Path(tmp), namespace='run/dataset/user/method/model',
                                           compiler_id='test', invoke=invoke)
            self.assertEqual(calls, [])
            memory.enqueue_user(task_id='task', session_id='session', event_id='u1', source_text=self.rule.rule_text)
            self.assertEqual(calls, [])
            ready = load_publication(memory.publications, expected_namespace=memory.namespace)
            self.assertEqual(ready.active_snapshot.rules, ())
            outcome = memory.process_pending()
            self.assertFalse(outcome['remaining'])
            frozen = memory.finish_training()
            self.assertEqual(len(frozen.active_snapshot.rules), 1)
            self.assertTrue((memory.root/'freeze-audit.json').exists())
            call_count = len(calls)
            self.assertEqual(memory.finish_training().digest, frozen.digest)
            with self.assertRaises(SupportError):
                memory.enqueue_user(task_id='test', session_id='session-test', event_id='u1', source_text='A test correction.')
            with self.assertRaises(SupportError):
                memory.process_pending()
            self.assertEqual(len(calls), call_count)
            with self.assertRaises(SupportError):
                TrainingMemory.create(Path(tmp), namespace='other-user', compiler_id='test', invoke=invoke)

    def test_unpublished_and_quarantined_requests_cannot_disappear_at_freeze(self):
        for relative in ('.tellonce-inbox/.request.json.tmp.partial', '.tellonce-inbox/failed/request.failed'):
            with tempfile.TemporaryDirectory() as tmp:
                memory = TrainingMemory.create(Path(tmp), namespace='user-a', compiler_id='test', invoke=transport())
                path = memory.root/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"incomplete":')
                with self.assertRaises(SupportError):
                    memory.finish_training()
                self.assertFalse((memory.root/'frozen.json').exists())

    def test_named_worker_never_resolves_operator_private_memory_configuration(self):
        import memory_upsert
        with tempfile.TemporaryDirectory() as tmp:
            memory = TrainingMemory.create(Path(tmp), namespace='user-a', compiler_id='test', invoke=transport())
            with patch.object(memory_upsert.path_config, 'get_memory_dir', side_effect=AssertionError('private config lookup')):
                memory_upsert._store(memory.root, namespace=memory.namespace)
                memory_upsert._store(memory.root)  # Internal resolve_turn opens an already owned store.
                memory.process_pending()


if __name__ == '__main__':
    unittest.main()
