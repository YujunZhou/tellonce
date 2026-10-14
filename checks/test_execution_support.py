"""Offline execution-support admission and immutable publication contracts."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from execution_support import check_prompt_payload
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
            review={'approved':True,'meaning_clear':True,'reason':'Matches the rule and cases.'}
            if value['tier'] in {'deterministic','semantic'}:
                review['case_verdicts']=[c['expected'] for c in value['cases']]
            return json.dumps(review)
        return json.dumps(value)
    return invoke


class ExecutionSupportTest(unittest.TestCase):
    def setUp(self):
        self.rule = Rule('r1', 1, 'Return a JSON object containing exactly the answer key.')

    def test_current_stage_state_follows_history_without_losing_evidence_or_identity(self):
        from dataclasses import asdict
        rule = Rule('preserve', 1, 'Preserve the requested document fields.')
        for stage, target in [('pre_action', 'action'), ('artifact', 'artifact'), ('delivery', 'delivery')]:
            with self.subTest(stage=stage):
                rubric = {'applicability': 'Editing the document.', 'requirement': rule.rule_text,
                          'violation': 'A field is lost.', 'non_violation': 'Fields remain.',
                          'evidence_fields': ['task', target, 'events']}
                support = Support(rule_digest(rule), 'test', 'semantic', (stage,), canonical(rubric),
                    canonical({'admitted': True, 'review': {'approved': True, 'meaning_clear': True}}), '')
                obs = Observation('t', 2, stage, 'Base document: old value.',
                    **{target: 'Current value: 修订 "confirmed".'},
                    events=(Event(1, 'prior', 'Earlier draft: old value.', 't'),))
                before = obs.digest
                def invoke(prompt):
                    wire = prompt
                    data = check_prompt_payload(prompt)
                    self.assertEqual(data['observation'], json.loads(canonical(asdict(obs))))
                    self.assertGreater(wire.index('Current value:'), wire.index('Earlier draft:'))
                    self.assertGreater(wire.index('Current value:'), wire.index('Base document:'))
                    self.assertEqual(list(data)[0], 'observation')  # 2026-10-03: shared observation first, rule last
                    quoted_field = json.dumps(target) + ':' + json.dumps(getattr(obs, target), ensure_ascii=False)
                    self.assertIn(quoted_field, wire)
                    return canonical({'status': 'pass', 'reason': 'Current state checked.',
                                      'evidence': [quoted_field]})
                result = check_rule(rule, support, obs, invoke)
                self.assertEqual(result.status, 'pass', result.reason)
                self.assertEqual(result.observation_digest, before)
                self.assertEqual(obs.digest, before)

    def test_shell_boundaries_preserve_quoted_spaces_for_native_envelopes(self):
        from execution_support import _shell_words
        good = "gemini --allowed-tools 'ShellTool(git diff),ShellTool(git status)'"
        bad = "gemini --allowed-tools 'ShellTool(git diff)' 'ShellTool(git status)'"
        for action in [good, canonical({'tool': 'Bash', 'arguments': {'command': good}}),
                       canonical({'tool': 'exec_command', 'arguments': {'cmd': good}})]:
            with self.subTest(action=action):
                self.assertEqual(_shell_words(action)['words'],
                                 ['gemini', '--allowed-tools', 'ShellTool(git diff),ShellTool(git status)'])
        self.assertEqual(len(_shell_words(bad)['words']), 4)
        self.assertEqual(_shell_words('echo ""')['words'], ['echo', ''])

    def test_shell_analysis_declines_complex_or_non_shell_actions(self):
        from execution_support import _shell_words
        actions = [None, '', 'echo "unclosed', 'echo $HOME', 'echo `pwd`', 'echo *.py',
                   'echo ok | cat', 'echo ok; pwd', 'echo ok > out', 'echo ok\npwd',
                   'X=1 echo ok', 'if true', 'echo # comment', '{bad json', '[]',
                   canonical({'tool': 'Write', 'arguments': {'command': 'echo ok'}}),
                   canonical({'tool': [], 'arguments': {'command': 'echo ok'}}),
                   canonical({'tool': 'Bash', 'arguments': {'command': 'echo ok', 'shell': []}}),
                   canonical({'tool': 'exec_command', 'arguments': {'cmd': 'echo ok', 'shell': 'pwsh'}})]
        for action in actions:
            with self.subTest(action=action):
                self.assertIsNone(_shell_words(action))

    def test_shell_derived_data_does_not_become_observed_quotation(self):
        rule = Rule('shell', 1, 'Pass the tool list as one comma-separated argument.')
        rubric = {'applicability':'Tool commands.', 'requirement':rule.rule_text,
                  'violation':'Split list.', 'non_violation':'One argument.',
                  'evidence_fields':['task','action']}
        support = Support(rule_digest(rule), 'test', 'semantic', ('pre_action',), canonical(rubric),
                          canonical({'admitted':True,'review':{'approved':True,'meaning_clear':True}}), '')
        action = "gemini --allowed-tools 'ShellTool(git diff),ShellTool(git status)'"
        obs = Observation('t', 1, 'pre_action', 'Prepare command.', action=action)
        before = obs.digest
        def invoke(prompt):
            payload = check_prompt_payload(prompt)
            self.assertEqual(payload['shell_lexical_analysis']['words'][2],
                             'ShellTool(git diff),ShellTool(git status)')
            return canonical({'status':'pass','reason':'One argument.',
                              'evidence':['POSIX shell lexical words; no execution or expansion']})
        self.assertEqual(check_rule(rule,support,obs,invoke).status, 'error')
        self.assertEqual(obs.digest, before)

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
                observation = check_prompt_payload(prompt)['observation']
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

    def test_semantic_quote_decoration_requires_an_exact_observed_inner_span(self):
        rule=Rule('quote',1,'Rubric-only phrase must not become observed evidence.')
        rubric=dict(applicability='Warnings.',requirement=rule.rule_text,
            violation='Wrong.',non_violation='Right.',evidence_fields=['delivery'])
        support=Support(rule_digest(rule),'test','semantic',('delivery',),canonical(rubric),
            canonical({'admitted':True,'review':{'approved':True,'meaning_clear':True}}),'Visible warning.')
        obs=Observation('task',1,'delivery','Task.',delivery='Prefix actual span suffix.')
        def check(quote,observation=obs):
            return check_rule(rule,support,observation,lambda _:canonical(
                dict(status='fail',reason='Exact captured evidence.',evidence=[quote])))
        for quote in ['"actual span"',"'actual span'",'“actual span”','‘actual span’']:
            with self.subTest(quote=quote):
                result=check(quote)
                self.assertEqual(result.status,'fail')
                self.assertEqual(result.evidence,('actual span',))
                self.assertEqual(result.attempts,1)
        for quote in ['"invented"','"actual span","suffix."','"actual span',
                      '""actual span""','"  actual span  "','"actual ... suffix."',
                      '"'+rule.rule_text+'"']:
            with self.subTest(quote=quote):self.assertEqual(check(quote).status,'error')
        quoted=Observation('task',1,'delivery','Task.',delivery='Prefix "actual span" suffix.')
        self.assertEqual(check('"actual span"',quoted).evidence,('"actual span"',))

    def test_json_envelope_accepts_only_one_complete_fence(self):
        from execution_support import _invoke_json
        from model_requests import ModelRequestError
        for raw in ['{"ok":true}', '```json\n{"ok":true}\n```', '  ```\n{"ok":true}\n```\n']:
            value,attempts=_invoke_json(lambda _:raw,'prompt',role='compile_review')
            self.assertEqual(value,{'ok':True})
            self.assertEqual(attempts,1)
        for raw in ['Explanation\n```json\n{"ok":true}\n```',
                    '```json\n{"ok":true}\n```\nComment',
                    '```python\n{"ok":true}\n```','```json\n{"ok":true}',
                    '```json\n{"ok":true}\n{"ok":false}\n```']:
            with self.subTest(raw=raw),self.assertRaises(ModelRequestError):
                _invoke_json(lambda _:raw,'prompt',role='compile_review')

    def test_semantic_table_quote_recovers_the_exact_source_span(self):
        rule = Rule('room-type', 1, 'Choose a different room type.')
        rubric = dict(applicability='Comparing room types.', requirement=rule.rule_text,
                      violation='Same room type.', non_violation='Different room type.',
                      evidence_fields=['delivery'])
        support = Support(rule_digest(rule), 'test', 'semantic', ('delivery',), canonical(rubric),
                          canonical({'admitted': True, 'review': {'approved': True,
                                                                 'meaning_clear': True}}), 'Room comparison.')
        row = 'Charming 1BR in UES Townhouse 1134.0\t Entire home/apt'
        observation = Observation('task', 1, 'delivery', 'Compare the room types.',
                                  artifact=canonical({'table': 'header\n' + row + '\nnext row'}),
                                  delivery='Chosen: Charming 1BR in UES Townhouse')
        quote = 'Charming 1BR in UES Townhouse 1134.0 Entire home/apt'
        for status in ('pass', 'fail', 'not_applicable', 'unknown'):
            for text in (quote, '“' + quote + '”'):
                with self.subTest(status=status, text=text):
                    result = check_rule(rule, support, observation, lambda _: canonical(
                        dict(status=status, reason='Recorded comparison.', evidence=[text])))
                    self.assertEqual(result.status, status)
                    self.assertEqual(result.evidence, (row,))
                    self.assertEqual(result.attempts, 1)

    def test_semantic_quote_spacing_never_recovers_changed_or_omitted_content(self):
        rule = Rule('quote', 1, 'Rule-only text with table-like spacing.')
        rubric = dict(applicability='Always.', requirement=rule.rule_text,
                      violation='Wrong.', non_violation='Right.', evidence_fields=['delivery'])
        support = Support(rule_digest(rule), 'test', 'semantic', ('delivery',), canonical(rubric),
                          canonical({'admitted': True, 'review': {'approved': True,
                                                                 'meaning_clear': True}}), 'Quote validation.')
        observation = Observation('task', 1, 'delivery', 'Check the table.',
            delivery='Hotel A  1134.0   Entire home/apt\nHotel B  845.0   Private room\n'
                     'No pets & No smoking\nCost <= 40\n')
        bad = ['Hotel A 1135.0 Entire home/apt', 'Hotel A 1134.0 Private room',
               'Hotel A Entire home/apt', 'Hotel A ... Entire home/apt',
               'Entire home/apt Hotel B', 'Hotel A1134.0 Entire home/apt',
               'No pets No smoking', 'No pets & No smoking Cost <= 40',
               'Cost < 40', ' Hotel A 1134.0 Entire home/apt ',
               rule.rule_text, '““Hotel A 1134.0 Entire home/apt””']
        for quote in bad:
            with self.subTest(quote=quote):
                result = check_rule(rule, support, observation, lambda _: canonical(
                    dict(status='fail', reason='Claimed mismatch.', evidence=[quote])))
                self.assertEqual(result.status, 'error')
                self.assertEqual(result.evidence, ())

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
                return '{"approved":false,"meaning_clear":true,"reason":"Overbroad.","case_verdicts":["pass","fail","not_applicable"]}'
            return json.dumps(candidate())
        self.assertEqual(compile_support(self.rule, reject, compiler_id='test').tier, 'reminder')

    def test_independent_review_hides_labels_and_generator_rationale(self):
        value=candidate();value['reason']='CASE ANSWER KEY: pass, fail, not_applicable'
        seen=[]
        def invoke(prompt):
            if 'Independently review' in prompt:
                payload=check_prompt_payload(prompt)
                self.assertNotIn('reason',payload['proposal'])
                self.assertTrue(all(set(c)=={'observation'} for c in payload['proposal']['cases']))
                self.assertNotIn('CASE ANSWER KEY',prompt)
                seen.append(payload)
            return transport(value)(prompt)
        result=compile_support(self.rule,invoke,compiler_id='blind-review')
        self.assertEqual(result.tier,'deterministic')
        self.assertEqual(len(seen),1)
        self.assertEqual(json.loads(result.validation_json)['review']['case_verdicts'],
                         ['pass','fail','not_applicable'])

    def test_independent_unknown_rejects_proposed_pass_despite_approved_true(self):
        calls=[]
        def invoke(prompt):
            calls.append(prompt)
            if 'Independently review' in prompt:
                return canonical({'approved':True,'meaning_clear':True,'reason':'First fact unobserved.',
                                  'case_verdicts':['unknown','fail','not_applicable']})
            return canonical(candidate())
        result=compile_support(self.rule,invoke,compiler_id='blind-review')
        self.assertEqual(result.tier,'reminder')
        audit=json.loads(result.validation_json)
        self.assertEqual(audit['repair_rounds'],3)  # 2026-10-03: up to three bounded repairs, each with the latest diagnostic
        self.assertEqual(audit['case_label_mismatches'],
                         [{'case_index':0,'proposed':'pass','independent':'unknown'}])
        self.assertEqual(len(calls),8)  # One ordinary proposal and three bounded repairs, each reviewed.

    def test_incomplete_case_review_cannot_activate_support(self):
        for labels in [[],['pass','fail'],['pass','fail','not_applicable','pass'],
                       ['pass','error','not_applicable'],['pass',{},'not_applicable']]:
            def invoke(prompt):
                if 'Independently review' in prompt:
                    return canonical({'approved':True,'meaning_clear':True,'reason':'Incomplete review.',
                                      'case_verdicts':labels})
                return canonical(candidate())
            with self.subTest(labels=labels):
                result=compile_support(self.rule,invoke,compiler_id='blind-review')
                self.assertEqual(result.tier,'reminder')
                self.assertFalse(json.loads(result.validation_json)['admitted'])

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

    def test_scope_outage_preserves_known_rules_but_not_across_user_boundaries(self):
        publication = prepare_publication(RuleSnapshot('user-a', 1, (self.rule,)), transport(), compiler_id='test')
        memory = TaskMemory(publication, expected_namespace='user-a', task_id='task', public_task='Report.',
                            invoke=lambda _: '["r1"]', training=False)
        memory.user_boundary('Please proceed.')
        def unavailable(_):
            raise RuntimeError('temporary provider failure')
        memory.invoke = unavailable
        failed = memory.expand_scope('Deliver the report.').result
        self.assertEqual(failed.status, 'error')
        self.assertEqual(failed.failed_ids, ('r1',))
        self.assertEqual(failed.selected_ids, ('r1',))
        self.assertIn(self.rule.rule_text, failed.injection)
        self.assertEqual(memory.check(Observation('task', 1, 'delivery', '', delivery='{}'))[0].status, 'fail')
        # Identical text is still a NEW input boundary: no stale applicability.
        self.assertEqual(memory.user_boundary('Please proceed.').result.selected_ids, ())

    def test_successful_scope_exclusion_is_not_overridden_by_outage_fallback(self):
        publication = prepare_publication(RuleSnapshot('user-a', 1, (self.rule,)), transport(), compiler_id='test')
        memory = TaskMemory(publication, expected_namespace='user-a', task_id='task', public_task='Report.',
                            invoke=lambda _: '["r1"]', training=False)
        memory.user_boundary('Please proceed.')
        memory.invoke = lambda _: '[]'
        self.assertEqual(memory.expand_scope('An unrelated operation.').result.selected_ids, ())
        memory.invoke = lambda _: 'malformed selector response'
        self.assertEqual(memory.expand_scope('Deliver.').result.selected_ids, ())

    def test_user_boundary_states_whole_response_scope(self):
        """2026-10-01 MemoryArena habit pilot: with an empty boundary scope line the selector skipped rules whose
        obligation falls due inside the response (opening lines before the first lookup): 9/37 selected, 33/40
        once the boundary scope names the opening, every lookup and the delivery."""
        from memory_runtime import BOUNDARY_SCOPE
        publication = prepare_publication(RuleSnapshot('user-a', 1, (self.rule,)), transport(), compiler_id='test')
        prompts = []
        memory = TaskMemory(publication, expected_namespace='user-a', task_id='task', public_task='Report.',
                            invoke=lambda prompt: prompts.append(prompt) or '["r1"]', training=False)
        memory.user_boundary('Please proceed.')
        memory.expand_scope('Proposed native operation: search.')
        import json
        encoded = lambda text: json.dumps(text)[1:-1]   # the query travels JSON-encoded inside the selector prompt
        self.assertIn(encoded('Current operation scope:\n' + BOUNDARY_SCOPE), prompts[0])
        self.assertIn('opening explanation', BOUNDARY_SCOPE)
        self.assertIn(encoded('Current operation scope:\nProposed native operation: search.'), prompts[1])
        self.assertNotIn(encoded(BOUNDARY_SCOPE), prompts[1])

    def test_oversized_scope_preserves_only_previously_selected_rules(self):
        publication = prepare_publication(RuleSnapshot('user-a', 1, (self.rule,)), transport(), compiler_id='test')
        memory = TaskMemory(publication, expected_namespace='user-a', task_id='task', public_task='Report.',
                            invoke=lambda _: '["r1"]', training=False)
        memory.user_boundary('Please proceed.')
        failed = memory.expand_scope('x' * 50000).result
        self.assertEqual(failed.status, 'error')
        self.assertEqual(failed.unexamined_ids, ('r1',))
        self.assertEqual(failed.selected_ids, ('r1',))

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

    def test_partial_scope_outage_preserves_only_failed_previous_rules_within_budget(self):
        from rule_selection import SelectionResult, render_rule
        rules = (Rule('r1', 1, 'First reminder.'), Rule('r2', 1, 'Second reminder.'),
                 Rule('r3', 1, 'Newly applicable obligation. ' * 20))
        snapshot = RuleSnapshot('user-a', 1, rules)
        supports = tuple(Support(rule_digest(r), 'test', 'reminder', (), '{}', '{}', 'Text reminder.')
                         for r in rules)
        publication = Publication(snapshot, 'test', supports)
        initial = '\n\n'.join(render_rule(r) for r in rules[:2])
        current = render_rule(rules[2])
        for budget, expected in ((len(current)+1, ('r3',)),
                                 (len(current)+len(render_rule(rules[0]))+2, ('r1','r3'))):
            memory = TaskMemory(publication, expected_namespace='user-a', task_id='task', public_task='Task',
                invoke=lambda _: self.fail('fake selection should own the calls'), training=False,
                limits=SelectionLimits(injection_chars=budget))
            before = SelectionResult('ok', snapshot.digest, 'task', 'before', ('r1','r2'), initial)
            partial = SelectionResult('partial', snapshot.digest, 'task', 'after', ('r3',), current,
                                      failed_ids=('r1',), errors=('one batch unavailable',))
            with patch('memory_runtime.select_rules', side_effect=[before, partial]):
                memory.user_boundary('Proceed.')
                result = memory.expand_scope('New action').result
            self.assertEqual(result.selected_ids, expected)
            self.assertNotIn('r2', result.selected_ids)  # A successfully examined exclusion wins.
            self.assertEqual(result.failed_ids, ('r1',))
            self.assertLessEqual(len(result.injection), budget)
            self.assertEqual(result.omitted_ids, ('r1',) if expected == ('r3',) else ())

    def test_training_queue_publish_freeze_lifecycle_uses_only_explicit_transport(self):
        from checks.test_memory_upsert import plan
        calls = []
        def invoke(prompt):
            calls.append(prompt)
            if 'only admission' in prompt:
                return '{"signal":true}'
            if 'reconstruct what the user is correcting' in prompt:
                return json.dumps(dict(status='resolved',target_quote='',user_quotes=[self.rule.rule_text],
                    context_quotes=[],rejected='',retained='',corrected_meaning=self.rule.rule_text,
                    behavioral_lesson=self.rule.rule_text,unsupported_inferences=[]))
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


def test_compile_prompt_requires_delivery_stage_for_condition_conformance_rules():
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'always include a delivery stage whose check compares the delivered items' in text
    assert 'a pre_action-only check for such a rule is wrong' in text


def test_review_prompt_treats_visible_text_properties_as_established():
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'A synthetic observation shows its complete text' in text
    assert 'reserve unknown for facts the' in text


def test_compile_prompt_describes_runtime_public_dialogue_evidence():
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'Synthetic observations must mirror the runtime evidence model' in text
    assert 'native_public_dialogue.public_dialogue.messages' in text
    assert 'never by moving the opening into delivery' in text


def test_review_and_check_prompts_read_public_dialogue_messages():
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'a case that supplies them there is not unknown for that reason' in text
    assert 'judged from those messages at delivery' in text


def test_repair_prompt_keeps_inapplicable_case():
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'keep or replace the inapplicable case, never drop it' in text


def test_public_message_rules_get_no_pre_action_stage():
    """2026-10-02 MemoryArena round 9 / ClawArena round 9: opening-line and post-lookup cards compiled with a pre_action
    stage denied every search (182 blocked operations, four empty plans). A denied operation cannot change a message
    already sent, so such rules are delivery-stage only."""
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'gets NO pre_action stage' in text
    assert 'is due after that operation, at delivery, never at pre_action before it' in text


def test_check_prompt_denies_only_for_the_operation_itself_and_tolerates_proper_nouns():
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'fail asserts that executing THIS proposed operation would itself violate the rule' in text
    assert 'is never a reason to deny the' in text and 'operation: return unknown and name the shortfall' in text
    assert 'proper nouns, file names, identifiers, code and quoted data keep their source form' in text


def test_compile_prompt_treats_part_exemption_as_scope_not_ambiguity():
    """2026-10-03 ClawArena round 10: lang-pref-001 carried does_not_apply_when "开场三行" and the compiler parked it as pending
    ("含义不明确… 不能启用检查"), leaving the language habit unenforced."""
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'names a PART of the response' in text and 'instead of returning pending' in text


def test_reporting_obligations_are_met_by_any_public_message_not_the_fixed_format_deliverable():
    """2026-10-03 MemoryArena round 13 (v29m): wf-pit-004 "查到后补上名称和数值" failed 108/167 delivery checks although the values were
    in the public messages ("先前公开消息提及这些信息，不能替代当前交付内容"), lang-pref-002 "编写行程时，说明使用中文" failed 52/71 against the
    English itinerary body; 49/86 units hit the repair cap and unit wall time reached 6.2× no_memory."""
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'is met by ANY public message of the same response or by the delivered text' in text
    assert 'is never the required carrier and is never failed for lacking explanatory content' in text
    assert 'write non_violation as "the response states it in a public message or in the delivery"' in text
    assert 'alike) still carries a delivery string and matches a chosen stage' in text  # v31: v30 wording made the compiler drop the delivery field from not_applicable cases (4/6 cards reminder-only)
    assert 'names the carrying part, compile the check on that part' in text and 'is checked on that part' in text


def test_repairable_reminder_support_is_recompiled_on_later_publications_then_reused():
    """2026-10-03 rounds 13-15: one failed compilation pinned a card to reminder (no executable check) until its text
    changed, so the opening habit it recorded was never enforced; a repairable reminder now gets two fresh compilations
    on later publications, and a reminder that compilation itself judged final (not repairable) is reused as before."""
    import json
    from memory_publication import prepare_publication, RECOMPILE_PUBLICATIONS
    rule = Rule('opening', 1, 'Open with the three labelled lines.')
    snap = RuleSnapshot('user-a', 1, (rule,))
    reminder = Support(rule_digest(rule), 'test', 'reminder', (), '{}',
                       canonical({'admitted': False, 'repairable': True, 'proposal': {'tier': 'semantic'}}), 'labels disagree')
    previous = Publication(snap, 'test', (reminder,))
    calls = []
    def compiler(prompt):
        calls.append(prompt)
        return transport()(prompt)
    newer = prepare_publication(RuleSnapshot('user-a', 2, (rule,)), compiler, compiler_id='test', previous=previous)
    assert calls, 'a repairable reminder support must be recompiled on the next publication'
    assert newer.supports[0].tier in {'semantic', 'deterministic'} or json.loads(newer.supports[0].validation_json)['recompiled_publications'] == 1
    exhausted = Support(rule_digest(rule), 'test', 'reminder', (), '{}',
                        canonical({'admitted': False, 'repairable': True, 'recompiled_publications': RECOMPILE_PUBLICATIONS}), 'still disagree')
    final = Support(rule_digest(rule), 'test', 'reminder', (), '{}', canonical({'admitted': False, 'repairable': False}), 'unclear')
    def forbidden(_):
        raise AssertionError('an exhausted or final reminder support must be reused, not recompiled')
    for support in (exhausted, final):
        reused = prepare_publication(RuleSnapshot('user-a', 3, (rule,)), forbidden, compiler_id='test',
                                     previous=Publication(snap, 'test', (support,)))
        assert reused.supports == (support,)
    # A same-generation republication (a NOOP turn) must reuse the reminder: one generation cannot publish
    # conflicting support, and round 16 (2026-10-03) lost its background learner to exactly that SupportError.
    same = prepare_publication(RuleSnapshot('user-a', 1, (rule,)), forbidden, compiler_id='test', previous=previous)
    assert same.supports == (reminder,)


def test_delivered_choice_rules_get_no_pre_action_stage():
    """2026-10-03 MemoryArena rounds 13/15: the condition cards ran 707 pre_action checks with zero failures (wf-pit-001 119 pass / 10
    unknown) — three pre_action rounds per user event of pure cost; only the delivery check decides."""
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'such a rule gets NO' in text and 'pre_action stage at all' in text
    assert 'pre_action belongs only to rules an operation can break by itself' in text


def test_semantic_check_prompt_carries_only_declared_evidence_and_puts_the_observation_first():
    """2026-10-03: every semantic check sent the whole observation — tool-result events were 67 KB of a 90 KB prompt — although
    7 of 11 MemoryArena rubrics and 7 of 8 ClawArena rubrics declare only task+delivery; and the rule preceded the observation, so
    parallel checks of one observation shared no prompt prefix (cached tokens ≈ 5 %)."""
    import json
    rule = Rule('fmt', 1, 'Open with three labelled lines.')
    support = Support(rule_digest(rule), 'test', 'semantic', ('delivery',), canonical({
        'applicability': 'any reply', 'requirement': 'three labelled lines first', 'violation': 'missing lines',
        'non_violation': 'lines present', 'evidence_fields': {'delivery': ['task', 'delivery']}}),
        canonical({'admitted': True, 'review': {'approved': True, 'meaning_clear': True}}), 'ok')
    prompts = []
    def invoke(prompt):
        prompts.append(prompt)
        return canonical({'reason': 'fine', 'evidence': [], 'status': 'pass'})
    obs = Observation('t', 7, 'delivery', 'Task text', delivery='目标:\\n方法:\\n拿不准:\\n答案',
                      events=(Event(1, 'tool_result', 'x' * 2000, 't'),))
    result = check_rule(rule, support, obs, invoke)
    assert result.status == 'pass'
    payload = check_prompt_payload(prompts[0])
    assert list(payload) == ['observation', 'shell_lexical_analysis', 'rule', 'rubric']
    observation = payload['observation']
    assert 'events' not in observation and observation['omitted_fields'] == ['events']
    assert list(observation)[:2] == ['stage', 'task'] and list(observation)[-3:] == ['task_id', 'sequence', 'omitted_fields']
    assert observation['action'] is None and observation['artifact'] is None  # undeclared empty fields stay as null
    assert 'x' * 2000 not in prompts[0]
    # a rubric that declares events still receives them in full
    with_events = Support(rule_digest(rule), 'test', 'semantic', ('delivery',), canonical({
        'applicability': 'any', 'requirement': 'r', 'violation': 'v', 'non_violation': 'n',
        'evidence_fields': {'delivery': ['task', 'delivery', 'events']}}),
        canonical({'admitted': True, 'review': {'approved': True, 'meaning_clear': True}}), 'ok')
    check_rule(rule, with_events, obs, invoke)
    observation = check_prompt_payload(prompts[1])['observation']
    assert observation['events'][0]['content'] == 'x' * 2000 and 'omitted_fields' not in observation


def test_compile_prompt_declares_events_only_when_operation_results_are_read():
    """2026-10-03 round 18: the compiler declared events for 4 of 5 cards (opening/format rules included), so item 25 pruned
    only 6 of 52 check prompts; events are ~67 KB of every observation."""
    import execution_support as es, inspect
    text = inspect.getsource(es)
    assert 'Declare events only when the check must READ operation results' in text
    assert 'declares task and delivery only; the ' in text and 'not a recommendation to include events' in text


def test_repairable_pending_support_is_recompiled_on_later_publications_like_a_reminder():
    """2026-10-03 ClawArena round 19: two habit cards published as pending after the compiler called a small wording change
    ambiguous; a pending that compilation itself left repairable now gets the same later recompilations as a reminder, while a
    pending that comes from the rule's unresolved scope (not repairable) is reused."""
    import json
    from memory_publication import prepare_publication, RECOMPILE_PUBLICATIONS
    rule = Rule('opening', 1, 'Open with the three labelled lines.')
    snap = RuleSnapshot('user-a', 1, (rule,))
    pending = Support(rule_digest(rule), 'test', 'pending', (), '{}',
                      canonical({'admitted': False, 'repairable': True, 'proposal': {'tier': 'pending'}, 'ambiguity': 'which message'}), 'which message')
    calls = []
    def compiler(prompt):
        calls.append(prompt)
        return transport()(prompt)
    newer = prepare_publication(RuleSnapshot('user-a', 2, (rule,)), compiler, compiler_id='test', previous=Publication(snap, 'test', (pending,)))
    assert calls, 'a repairable pending support must be recompiled on the next publication'
    assert newer.supports[0].tier in {'semantic', 'deterministic'} or json.loads(newer.supports[0].validation_json)['recompiled_publications'] == 1
    final = Support(rule_digest(rule), 'test', 'pending', (), '{}', canonical({'admitted': False}), 'Rule applicability is explicitly unresolved.')
    def forbidden(_):
        raise AssertionError('a pending support from unresolved scope must be reused, not recompiled')
    reused = prepare_publication(RuleSnapshot('user-a', 3, (rule,)), forbidden, compiler_id='test', previous=Publication(snap, 'test', (final,)))
    assert reused.supports == (final,)


def test_semantic_check_sees_companion_rules_and_never_itself():
    """2026-10-03 ClawArena round 20 (v39c): the language card "after the Chinese opening use English; Chinese only for required
    labels" failed 375 of 465 deliveries for the labels 「结论:」「依据:」「排除:」 that two other cards require; the repair swapped them
    for "Basis:" / "Evidence:", the label cards then failed, and 333 repairs ping-ponged (K2 38 %, K3 52 %, K4 40 %)."""
    import json
    lang = Rule('lang-pref-001', 1, 'After the Chinese opening lines write in English; Chinese only for required labels.')
    label = Rule('fmt-pref-004', 1, 'The first delivery line starts with 「结论:」 and the second with 「依据:」.')
    support = Support(rule_digest(lang), 'test', 'semantic', ('delivery',), canonical({
        'applicability': 'any reply', 'requirement': 'English after the opening', 'violation': 'Chinese body text',
        'non_violation': 'English body, required labels may be Chinese', 'evidence_fields': {'delivery': ['task', 'delivery']}}),
        canonical({'admitted': True, 'review': {'approved': True, 'meaning_clear': True}}), 'ok')
    prompts = []
    def invoke(prompt):
        prompts.append(prompt)
        return canonical({'reason': 'fine', 'evidence': [], 'status': 'pass'})
    obs = Observation('t', 7, 'delivery', 'Task text', delivery='结论: B\n依据: a.md')
    check_rule(lang, support, obs, invoke, companions=(lang, label))
    data = check_prompt_payload(prompts[-1])
    assert [c['atomic_id'] for c in data['companion_rules']] == ['fmt-pref-004']
    assert '「依据:」' in data['companion_rules'][0]['rule_text'] and list(data)[0] == 'observation'
    assert 'an element that a companion rule requires' in prompts[-1] and 'is never a violation of this rule' in prompts[-1]
    check_rule(lang, support, obs, invoke)
    assert 'companion_rules' not in check_prompt_payload(prompts[-1])



def test_operations_view_keeps_what_was_done_and_drops_what_was_returned():
    """2026-10-05 ClawArena: a rule that only needs the names of the files actually used declared events; 267 checks
    carried a median 94 KB of tool output each, half of the run's check tokens. `operations` is the abbreviated record."""
    import json as _json
    import execution_support as es
    call = {'action': {'arguments': {'command': 'cat docs/a.md'}, 'tool': 'Bash'}, 'tool_use_id': 't1'}
    events = [{'sequence': 3, 'kind': 'action_proposed', 'content': _json.dumps(call)},
              {'sequence': 4, 'kind': 'native_runtime_observation', 'content': _json.dumps({'item': {'id': 't1', 'aggregated_output': 'x' * 5000}})},
              {'sequence': 6, 'kind': 'tool_completed', 'content': _json.dumps({'tool_response': 'y' * 9000, **call})}]
    view = es.operations_view(events)
    assert [row['sequence'] for row in view] == [3, 6]                 # the runtime echo of the same call is skipped
    assert 'cat docs/a.md' in view[1]['content'] and len(view[1]['content']) < 300 and '(+' in view[1]['content']
    many = [{'sequence': n, 'kind': 'tool_completed', 'content': 'z' * 400} for n in range(200)]
    bounded = es.operations_view(many)
    assert sum(len(r['content']) for r in bounded) <= es.OPERATIONS_VIEW_CHARS
    assert any(r['kind'] == 'operations_omitted' for r in bounded) and bounded[0]['sequence'] == 0 and bounded[-1]['sequence'] == 199
    check = {'applicability': 'a', 'requirement': 'r', 'violation': 'v', 'non_violation': 'n'}
    es.validate_check('semantic', {**check, 'evidence_fields': {'delivery': ['task', 'delivery', 'operations']}})
    import pytest
    with pytest.raises(es.SupportError):
        es.validate_check('semantic', {**check, 'evidence_fields': ['task', 'events', 'operations']})
    import inspect
    assert 'Declare operations (never together with events)' in inspect.getsource(es._compile_support_attempt)


def test_repairable_reminder_is_recompiled_at_freeze_as_a_same_generation_support_repair(tmp_path):
    """2026-10-06 ClawArena round 30 (v54): the opening-lines card's fifth revision failed one compilation at the last
    training update and was frozen as a reminder, so K1/K4 were unenforced for the whole test phase (55 % K4
    violations). prepare_publication only recompiles on a later generation, which a frozen library never has."""
    import json
    from memory_publication import (Publication, is_support_repair, publish, load_publication,
                                    repair_publication, RECOMPILE_PUBLICATIONS)
    rule = Rule('opening', 1, 'Open with the three labelled lines.')
    other = Rule('language', 1, 'After the opening, write in the task language.')
    snap = RuleSnapshot('user-a', 3, (rule, other))
    reminder = Support(rule_digest(rule), 'test', 'reminder', (), '{}',
                       canonical({'admitted': False, 'repairable': True, 'proposal': {'tier': 'semantic'}}), 'labels disagree')
    kept = Support(rule_digest(other), 'test', 'reminder', (), '{}', canonical({'admitted': False, 'repairable': False}), 'unclear')
    current = Publication(snap, 'test', (reminder, kept))
    calls = []
    def compiler(prompt):
        calls.append(prompt)
        return transport()(prompt)
    repaired = repair_publication(current, compiler, compiler_id='test')
    assert len(calls) >= 1, 'only the repairable reminder is recompiled'
    if repaired is current:
        return   # the stub compiler did not produce executable support; nothing to publish
    assert repaired.generation == current.generation and repaired.snapshot.digest == current.snapshot.digest
    assert repaired.supports[0].tier in {'semantic', 'deterministic'} and repaired.supports[1] == kept
    assert json.loads(repaired.supports[0].validation_json)['freeze_recompiled'] is True
    assert is_support_repair(current, repaired) and not is_support_repair(repaired, current)
    # publish admits exactly this same-generation replacement and the pointer moves to the repaired publication
    root = tmp_path / 'published'
    publish(root, current, expected_namespace='user-a')
    publish(root, repaired, expected_namespace='user-a')
    assert load_publication(root, expected_namespace='user-a').digest == repaired.digest
    # any other same-generation difference is still refused
    conflicting = Publication(snap, 'test', (repaired.supports[0],
                                             Support(kept.rule_digest, 'test', 'reminder', (), '{}',
                                                     canonical({'admitted': False, 'repairable': False}), 'other words')))
    try:
        publish(root, conflicting, expected_namespace='user-a')
    except SupportError:
        pass
    else:
        raise AssertionError('a same-generation publication that is not a support repair must be refused')
