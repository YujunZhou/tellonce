"""Impossible compiler fixtures are rejected before spending review/check calls."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from execution_support import check_prompt_payload
from execution_support import compile_support
from rule_selection import Rule


def proposal():
    return {
        'tier': 'semantic', 'stages': ['delivery'], 'reason': 'Observed reply style.',
        'check': {'applicability': 'Every delivered reply.', 'requirement': 'Be concise.',
                  'violation': 'Needless repetition.', 'non_violation': 'Concise reply.',
                  'evidence_fields': {'delivery': ['delivery', 'events']}},
        'cases': [
            {'expected': 'pass', 'observation': {'stage': 'delivery', 'delivery': 'Done.'}},
            {'expected': 'fail', 'observation': {'stage': 'delivery', 'delivery': 'Done. Done. Done.'}},
            {'expected': 'not_applicable', 'observation': {'stage': 'pre_action', 'action': 'Read file.'}},
        ],
    }


class CasePreflightTest(unittest.TestCase):
    def run_candidate(self, candidate):
        calls = []

        def invoke(prompt):
            if 'Independently review' in prompt:
                calls.append('review')
                return json.dumps({'approved': True, 'meaning_clear': True, 'reason': 'Fixture review.',
                                   'case_verdicts': [c['expected'] for c in candidate['cases']]})
            if 'Evaluate ONE learned rule' in prompt:
                calls.append('check')
                obs = check_prompt_payload(prompt)['observation']
                status = 'unknown' if obs['delivery'] is None else (
                    'fail' if obs['delivery'].count('Done.') > 1 else 'pass')
                return json.dumps({'status': status, 'reason': 'Fixture judgment.', 'evidence': []})
            calls.append('compile')
            return json.dumps(candidate)

        result = compile_support(Rule('concise', 1, 'Be concise.'), invoke, compiler_id='offline')
        return result, calls

    def test_malformed_observations_never_reach_model_review(self):
        mutations = [
            lambda v: v['cases'][0]['observation'].update(delivery={'text': 'Done.'}),
            lambda v: v['cases'][0]['observation'].update(events='missing history'),
            lambda v: v['cases'][0]['observation'].update(events=[{'sequence': 100, 'kind': 'prior', 'content': 'future'}]),
            lambda v: v['cases'][0]['observation'].update(events=[{'sequence': 0, 'kind': 'prior', 'content': 'x', 'task_id': 'another-task'}]),
            lambda v: v['cases'][0]['observation'].update(secret_reference='invented'),
            lambda v: v['cases'][0].update(expected='arbitrary'),
            lambda v: v['cases'][0].update(observation=[]),
        ]
        for mutate in mutations:
            candidate = proposal(); mutate(candidate)
            with self.subTest(candidate=candidate):
                result, calls = self.run_candidate(candidate)
                self.assertEqual(calls, ['compile'] * 4)  # one proposal + three bounded repairs (2026-10-03)
                self.assertEqual(result.tier, 'reminder')
                self.assertIsNot(json.loads(result.validation_json).get('admitted'), True)

    def test_required_missing_evidence_cannot_claim_pass_fail_or_inapplicable(self):
        for expected in ('pass', 'fail', 'not_applicable'):
            candidate = proposal()
            candidate['cases'].append({'expected': expected, 'observation': {'stage': 'delivery'}})
            result, calls = self.run_candidate(candidate)
            self.assertEqual(calls, ['compile'] * 4)  # one proposal + three bounded repairs (2026-10-03)
            self.assertIn('evidence', result.reason)

    def test_unknown_and_known_empty_event_history_are_accepted(self):
        candidate = proposal()
        candidate['cases'].append({'expected': 'unknown', 'observation': {'stage': 'delivery'}})
        original = copy.deepcopy(candidate)
        result, calls = self.run_candidate(candidate)
        self.assertEqual(result.tier, 'semantic', result.reason)
        self.assertEqual(calls, ['compile', 'review', 'check', 'check'])
        self.assertEqual(candidate, original)
        self.assertEqual([r['status'] for r in json.loads(result.validation_json)['results']],
                         ['pass', 'fail', 'not_applicable', 'unknown'])

    def test_evidence_stage_map_is_validated_before_review(self):
        candidate = proposal()
        candidate['check']['evidence_fields'] = {'artifact': ['artifact']}
        result, calls = self.run_candidate(candidate)
        self.assertEqual(calls, ['compile'] * 4)  # one proposal + three bounded repairs (2026-10-03)
        self.assertEqual(result.tier, 'reminder')


if __name__ == '__main__':
    unittest.main()
