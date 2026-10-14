"""Bounded compiler repair must preserve admission and frozen memory boundaries."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from execution_support import check_prompt_payload
from execution_support import compile_support
from rule_selection import Rule


def proposal():
    return {'tier': 'deterministic', 'stages': ['delivery'], 'reason': 'Literal key requirement.',
            'check': {'operation': 'json_object_keys', 'required': ['answer'], 'exact': True},
            'cases': [
                {'expected': 'pass', 'observation': {'stage': 'delivery', 'delivery': '{"answer":1}'}},
                {'expected': 'fail', 'observation': {'stage': 'delivery', 'delivery': '{}'}},
                {'expected': 'not_applicable', 'observation': {'stage': 'pre_action', 'action': 'read'}}]}


class CompilerRepairTest(unittest.TestCase):
    def setUp(self):
        self.rule = Rule('r', 1, 'Return a JSON object containing exactly the answer key.')

    def run_compiler(self, bad, *, repair=True, approve=True):
        prompts = []
        def invoke(prompt):
            if 'Independently review' in prompt:
                # The reviewer sees observations without the generator's labels.
                cases = check_prompt_payload(prompt)['proposal']['cases']
                verdicts = []
                for case in cases:
                    obs = case['observation']
                    if obs.get('stage') != 'delivery':
                        verdicts.append('not_applicable')
                        continue
                    try:
                        value = json.loads(obs['delivery'])
                        verdicts.append('pass' if isinstance(value, dict) and set(value) == {'answer'} else 'fail')
                    except (TypeError, ValueError, KeyError):
                        verdicts.append('unknown')
                return json.dumps({'approved': approve, 'meaning_clear': True,
                                   'reason': 'Review result.', 'case_verdicts': verdicts})
            prompts.append(prompt)
            return json.dumps(proposal() if repair and len(prompts) == 2 else bad)
        return compile_support(self.rule, invoke, compiler_id='synthetic'), prompts

    def test_missing_stage_cases_repaired_and_fully_revalidated(self):
        bad = proposal(); bad['cases'] = bad['cases'][:1]
        support, prompts = self.run_compiler(bad)
        self.assertEqual(support.tier, 'deterministic', support.reason)
        self.assertEqual(len(prompts), 2)
        self.assertIn('positive, negative', prompts[1])
        audit = json.loads(support.validation_json)
        self.assertTrue(audit['admitted'])
        self.assertEqual(audit['repair_rounds'], 1)
        self.assertEqual(len(audit['results']), 3)

    def test_incorrect_expected_verdict_repaired_with_failed_case_feedback(self):
        bad = proposal(); bad['cases'][0]['observation']['delivery'] = '{}'
        support, prompts = self.run_compiler(bad)
        self.assertEqual(support.tier, 'deterministic', support.reason)
        self.assertIn('Independent rule-only case labels disagree', prompts[1])

    def test_bad_observation_type_gets_one_repair(self):
        bad = proposal(); bad['cases'][0]['observation']['delivery'] = {'answer': 1}
        support, prompts = self.run_compiler(bad)
        self.assertEqual(support.tier, 'deterministic', support.reason)
        self.assertEqual(len(prompts), 2)

    def test_still_wrong_after_repair_stays_reminder(self):
        bad = proposal(); bad['cases'][0]['observation']['delivery'] = '{}'
        support, prompts = self.run_compiler(bad, repair=False)
        self.assertEqual(support.tier, 'reminder')
        self.assertEqual(len(prompts), 4)  # one proposal + three bounded repairs (2026-10-03), still reminder
        self.assertEqual(json.loads(support.validation_json).get('repair_rounds'), 3)
        self.assertIsNot(json.loads(support.validation_json).get('admitted'), True)

    def test_failed_independent_review_cannot_be_bypassed(self):
        support, prompts = self.run_compiler(proposal(), approve=False)
        self.assertEqual(support.tier, 'reminder')
        self.assertLessEqual(len(prompts), 4)  # bounded: one proposal + at most three repairs

    def test_intentional_reminder_is_not_retried_but_model_pending_is(self):
        """An intentional reminder (clear rule, no reliable observable check) is final. A model-declared pending is a
        proposal defect since 2026-10-03 (ClawArena round 19 published two habit cards as pending after a wording tweak):
        it enters the repair loop with its ambiguity as the diagnosis."""
        value = {'tier': 'reminder', 'stages': [], 'check': {}, 'cases': [], 'reason': 'No reliable check.'}
        support, prompts = self.run_compiler(value)
        self.assertEqual(support.tier, 'reminder'); self.assertEqual(len(prompts), 1)
        value = {'tier': 'pending', 'stages': [], 'check': {}, 'cases': [], 'reason': 'Unclear which message.'}
        support, prompts = self.run_compiler(value)
        self.assertEqual(support.tier, 'deterministic', support.reason); self.assertEqual(len(prompts), 2)
        self.assertIn('Unclear which message', prompts[1])

    def test_exhausted_transport_does_not_start_new_retry_budget(self):
        from model_requests import ModelRequestError
        calls = []
        def invoke(prompt):
            calls.append(prompt)
            raise ModelRequestError('compile_support', 2, ['timeout'])
        support = compile_support(self.rule, invoke, compiler_id='synthetic')
        self.assertEqual(support.tier, 'reminder')
        self.assertEqual(len(calls), 1)


class ModelPendingRepairTest(unittest.TestCase):
    """2026-10-03 ClawArena round 19 (v35c): the opening card and the language card each compiled twice; after a small
    lifecycle UPDATE the compiler called "the separate delivery message" / "immediately after" ambiguous, returned pending
    with compile_attempts=1 and no repair, and both habits went unchecked for the round (the test segment was then refused
    by the admission gate for the two pending rules)."""
    def setUp(self):
        self.rule = Rule('r', 1, 'Return a JSON object containing exactly the answer key.')

    def compiler(self, answers):
        prompts = []
        def invoke(prompt):
            if 'Independently review' in prompt:
                cases = check_prompt_payload(prompt)['proposal']['cases']
                verdicts = []
                for case in cases:
                    obs = case['observation']
                    if obs.get('stage') != 'delivery':
                        verdicts.append('not_applicable'); continue
                    try:
                        value = json.loads(obs['delivery'])
                        verdicts.append('pass' if isinstance(value, dict) and set(value) == {'answer'} else 'fail')
                    except (TypeError, ValueError, KeyError):
                        verdicts.append('unknown')
                return json.dumps({'approved': True, 'meaning_clear': True, 'reason': 'Review result.', 'case_verdicts': verdicts})
            prompts.append(prompt)
            return json.dumps(answers[min(len(prompts) - 1, len(answers) - 1)])
        return invoke, prompts

    def pending(self, reason='"exactly" does not say whether nested keys count.'):
        return {'tier': 'pending', 'stages': [], 'check': {}, 'cases': [], 'reason': reason}

    def test_model_pending_is_repaired_with_the_ambiguity_as_diagnosis(self):
        invoke, prompts = self.compiler([self.pending(), proposal()])
        support = compile_support(self.rule, invoke, compiler_id='synthetic')
        self.assertEqual(support.tier, 'deterministic', support.reason)
        self.assertEqual(len(prompts), 2)
        self.assertIn('nested keys count', prompts[1])
        self.assertIn('Resolve that question by the natural reading', prompts[1])
        audit = json.loads(support.validation_json)
        self.assertTrue(audit['admitted']); self.assertEqual(audit['repair_rounds'], 1)
        self.assertEqual(audit['prior_attempt']['ambiguity'], self.pending()['reason'])

    def test_still_pending_after_all_repairs_stays_pending(self):
        invoke, prompts = self.compiler([self.pending()])
        support = compile_support(self.rule, invoke, compiler_id='synthetic')
        self.assertEqual(support.tier, 'pending'); self.assertEqual(support.stages, ())
        self.assertEqual(len(prompts), 1 + 3)
        self.assertEqual(json.loads(support.validation_json)['repair_rounds'], 3)

    def test_unresolved_scope_pending_is_not_retried(self):
        rule = Rule('r', 1, 'Return a JSON object containing exactly the answer key.', scope='unclear')
        invoke, prompts = self.compiler([proposal()])
        support = compile_support(rule, invoke, compiler_id='synthetic')
        self.assertEqual(support.tier, 'pending'); self.assertEqual(prompts, [])
        self.assertFalse(json.loads(support.validation_json).get('repairable'))

    def test_compile_prompt_states_the_pending_criterion(self):
        invoke, prompts = self.compiler([proposal()])
        compile_support(self.rule, invoke, compiler_id='synthetic')
        self.assertIn('two readings would make DIFFERENT deliveries pass', prompts[0])
        self.assertIn('read in the light of the clauses around it', prompts[0])


class RejectedReminderRepairTest(unittest.TestCase):
    """2026-10-04 MemoryArena round 22 (v42m): the compiler proposed tier reminder for "list every condition as C1, C2 … before
    querying" ("per-item checking cannot be verified"), the independent review rejected that ("the listing and numbering are
    observable"), and the rejected reminder was not repairable — the card had no check and H2 violations were 41 %."""
    def test_reminder_rejected_by_review_is_repaired_and_approved_reminder_is_final(self):
        rule = Rule('r', 1, 'Return a JSON object containing exactly the answer key.')
        def run(approve_reminder):
            prompts = []
            def invoke(prompt):
                if 'Independently review' in prompt:
                    body = check_prompt_payload(prompt)['proposal']
                    if body.get('tier') == 'reminder':
                        return json.dumps({'approved': approve_reminder, 'meaning_clear': True,
                                           'reason': 'The rule has an observable obligation.' if not approve_reminder else 'No observable check.'})
                    verdicts = []
                    for case in body['cases']:
                        obs = case['observation']
                        if obs.get('stage') != 'delivery':
                            verdicts.append('not_applicable'); continue
                        try:
                            value = json.loads(obs['delivery'])
                            verdicts.append('pass' if isinstance(value, dict) and set(value) == {'answer'} else 'fail')
                        except (TypeError, ValueError, KeyError):
                            verdicts.append('unknown')
                    return json.dumps({'approved': True, 'meaning_clear': True, 'reason': 'ok', 'case_verdicts': verdicts})
                prompts.append(prompt)
                if len(prompts) == 1:
                    return json.dumps({'tier': 'reminder', 'stages': [], 'check': {}, 'cases': [], 'reason': 'Cannot verify reliably.'})
                return json.dumps(proposal())
            return compile_support(rule, invoke, compiler_id='synthetic'), prompts
        support, prompts = run(approve_reminder=False)
        self.assertEqual(support.tier, 'deterministic', support.reason)
        self.assertEqual(len(prompts), 2); self.assertIn('observable obligation', prompts[1])
        support, prompts = run(approve_reminder=True)
        self.assertEqual(support.tier, 'reminder'); self.assertEqual(len(prompts), 1)


if __name__ == '__main__':
    unittest.main()


def test_timing_phrases_are_read_against_the_before_first_tool_mark():
    """2026-10-04 learning replay of MemoryArena round 24: the card "查询前先用中文写「目标:」「方法:」「拿不准:」三行" ended
    reminder-only after four attempts; each review rejected a different reading of "before the query" (tied to the first
    tool operation = "stricter than the rule"; action_proposed "is not an executed query"; delivery text cannot satisfy
    it). Compiler and reviewer now share one convention: the before_first_tool mark establishes the timing."""
    import inspect
    import execution_support as es
    text = inspect.getsource(es._compile_support_attempt)
    assert text.count('executable reading of the rule') == 2
    assert 'the mark alone establishes the timing' in text
