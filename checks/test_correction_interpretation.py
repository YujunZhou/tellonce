"""Regression tests for the correction interpretation/lifecycle boundary."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
import correction_interpretation as interpretation
import learning_policy
import memory_judge
from model_requests import ModelRequestError

SOURCE = 'With the snapshot, that restore is unnecessary.'
CONTEXT = 'User: Keep the audit. Assistant: The snapshot is mounted. Should I restore another copy?'
def meaning(status='resolved'):
    return dict(status=status, target_quote='Should I restore another copy?',
        user_quotes=[SOURCE], context_quotes=['The snapshot is mounted.'],
        rejected='Restoring another copy', retained='Mounted snapshot and audit',
        corrected_meaning='No extra restore while the snapshot is present.',
        behavioral_lesson='Avoid duplicating an already satisfied prerequisite.',unsupported_inferences=[])

class Owner:
    def __init__(self, values): self.values=list(values); self.calls=[]
    def __call__(self, prompt): raise AssertionError('request must have a role')
    def request_validated(self, prompt, *, role, validate, max_attempts):
        self.calls.append((role,prompt))
        return validate(json.dumps(self.values.pop(0))),1

class InterpretationTests(unittest.TestCase):
    def test_source_and_context_attribution(self):
        self.assertEqual(interpretation.validate(SOURCE,CONTEXT,meaning()),meaning())
        for key, value in [('user_quotes',['The snapshot is mounted.']),
                           ('context_quotes',['Invented']),('target_quote','Invented')]:
            bad=meaning(); bad[key]=value
            with self.assertRaises(ValueError): interpretation.validate(SOURCE,CONTEXT,bad)

    def test_omitted_prefix_cannot_support_evidence(self):
        bad=meaning(); bad['target_quote']='Older proposal'
        with self.assertRaises(ValueError):
            interpretation.validate(SOURCE,'Older proposal'+'x'*7000+CONTEXT,bad)

    def test_resolved_requires_user_evidence(self):
        bad=meaning();bad['user_quotes']=[]
        with self.assertRaises(ValueError): interpretation.validate(SOURCE,CONTEXT,bad)

    def test_self_contained_preference_needs_no_fabricated_context_quote(self):
        source='Only change the requested section; preserve unrelated accepted details.'
        context='Section one: revised.\nNotes: unchanged.\nSection two: accepted.'
        value=dict(status='resolved',target_quote='',user_quotes=[source],context_quotes=[],
            rejected='Changing unrelated accepted details',retained='Accepted unrelated details',
            corrected_meaning='The user requests a local edit with unrelated details preserved.',
            behavioral_lesson=source,unsupported_inferences=['A prior section was actually changed.'])
        self.assertEqual(interpretation.validate(source,context,value),value)
        for quote in ('Section one: revised.\n...\nSection two: accepted.',
                      'Section one: revised.\nSection two: accepted.'):
            bad={**value,'context_quotes':[quote]}
            with self.assertRaisesRegex(ValueError,'invented or misattributed'):
                interpretation.validate(source,context,bad)

    def test_full_context_and_original_source_reach_final_judge(self):
        context='Earlier agreed requirement: retain encryption. '+'x'*7000+CONTEXT
        owner=Owner([meaning(),{'mutations':[]}])
        result=memory_judge.judge_plan(SOURCE,[],context,policy=learning_policy.CORRECTIONS,invoke=owner)
        self.assertEqual([r for r,p in owner.calls],['correction_interpretation','lifecycle_plan'])
        self.assertIn('Earlier agreed requirement: retain encryption.',owner.calls[1][1])
        self.assertIn(SOURCE,owner.calls[1][1])
        self.assertEqual(result['correction_interpretation'],meaning())

    def test_long_correction_context_retains_latest_proposal(self):
        context='Obsolete opening proposal. '+'x'*22000+CONTEXT
        owner=Owner([meaning(),{'mutations':[]}])
        memory_judge.judge_plan(SOURCE,[],context,policy=learning_policy.CORRECTIONS,invoke=owner)
        for role,prompt in owner.calls:
            self.assertIn('Should I restore another copy?',prompt)
            self.assertNotIn('Obsolete opening proposal.',prompt)
        self.assertIn('Earlier conversation context omitted',owner.calls[-1][1])
        bounded=memory_judge._learning_context(context,learning_policy.CORRECTIONS)
        self.assertLessEqual(len(bounded),20000)
        self.assertIn(CONTEXT,bounded)
        self.assertEqual(memory_judge._learning_context(context,learning_policy.GENERAL),context[:20000])
        self.assertIn('Should I restore another copy?',memory_judge._selector_prompt(SOURCE,bounded,[]))

    def test_ambiguous_interpretation_does_not_bypass_existing_rules(self):
        rule=dict(atomic_id='prior-rule',rule_text='Preserve agreed requirements.',status='active')
        owner=Owner([meaning('ambiguous'),{'mutations':[]}])
        memory_judge.judge_plan(SOURCE,[rule],CONTEXT,policy=learning_policy.CORRECTIONS,invoke=owner)
        self.assertIn('prior-rule',owner.calls[1][1])

    def test_failure_is_not_successful_noop(self):
        with mock.patch.object(interpretation,'resolve',side_effect=ModelRequestError('correction_interpretation',2,['invalid'])):
            with self.assertRaisesRegex(memory_judge.MemoryJudgeError,'interpretation unavailable'):
                memory_judge.judge_plan(SOURCE,[],CONTEXT,policy=learning_policy.CORRECTIONS,invoke=Owner([]))

    def test_general_or_contextless_learning_keeps_single_request(self):
        for policy,context in [(learning_policy.GENERAL,CONTEXT),(learning_policy.CORRECTIONS,'')]:
            owner=Owner([{'mutations':[]}])
            memory_judge.judge_plan(SOURCE,[],context,policy=policy,invoke=owner)
            self.assertEqual([r for r,p in owner.calls],['lifecycle_plan'])

    def test_interpretation_cannot_authorize_a_memory(self):
        record=dict(name='audit',description='Preserve audit.',type='pitfall',domain='workflow',
            scope='global',scope_anchor='',condition='',confidence='high',rule_text='Preserve audit.',
            applies_when='',does_not_apply_when='(none)',body='Preserve audit.')
        plan={'mutations':[dict(operation='NEW',target_ids=[],record=record,
            evidence_spans=['Mounted snapshot and audit'],applicability_evidence='',children=[])]}
        owner=Owner([meaning(),plan])
        with self.assertRaises(memory_judge.MemoryJudgeError):
            memory_judge.judge_plan(SOURCE,[],CONTEXT,policy=learning_policy.CORRECTIONS,invoke=owner)

    def test_unmanaged_cli_retains_interpretation(self):
        with mock.patch.object(memory_judge,'_invoke_cli',side_effect=[json.dumps(meaning()),'{"mutations":[]}']), mock.patch.object(memory_judge,'_setting',return_value=''):
            result=memory_judge.judge_plan(SOURCE,[],CONTEXT,policy=learning_policy.CORRECTIONS)
        self.assertEqual(result['correction_interpretation'],meaning())

    def test_oversized_hypothesis_cannot_overflow_prompt(self):
        size=len(memory_judge.build_prompt(SOURCE,[],CONTEXT,policy=learning_policy.CORRECTIONS))
        owner=Owner([meaning()])
        with mock.patch.object(memory_judge,'MAX_PROMPT_CHARS',size+10):
            result=memory_judge.judge_plan(SOURCE,[],CONTEXT,policy=learning_policy.CORRECTIONS,invoke=owner)
        self.assertEqual(result['mutations'][0]['operation'],'NEEDS_USER')
        self.assertEqual(len(owner.calls),1)

if __name__=='__main__': unittest.main()
