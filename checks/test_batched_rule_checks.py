"""Rules that read the same evidence are judged in one call, each with its own verdict (2026-10-05).

ClawArena, eight learned rules: one call per rule resent the observation for every rule — 1,978 calls and 14.8 M prompt
tokens on 120 units. A group that cannot be read back falls back to the one-rule check, so no verdict is ever lost."""
import json

from checks.test_execution_support import Rule, Observation
from execution_support import Support, canonical, check_rules, rule_digest
from execution_support import check_prompt_payload

ADMITTED = canonical({'admitted': True, 'review': {'approved': True, 'meaning_clear': True}})


def job(name, fields, text='Write the opening line.'):
    rule = Rule(name, 1, text)
    rubric = {'applicability': 'always', 'requirement': text, 'violation': 'absent', 'non_violation': 'present',
              'evidence_fields': {'delivery': fields}}
    return rule, Support(rule_digest(rule), 'test', 'semantic', ('delivery',), canonical(rubric), ADMITTED, '')


def observation():
    return Observation('t', 1, 'delivery', 'Task text.', delivery='目标: write the report\nBody in English.')


def test_one_call_per_evidence_view_with_separate_verdicts_in_order():
    jobs = [job('a', ['task', 'delivery']), job('ops', ['task', 'delivery', 'operations']), job('b', ['task', 'delivery']),
            job('c', ['task', 'delivery'])]
    prompts = []
    def invoke(prompt):
        prompts.append(prompt)
        ids = [entry['rule']['atomic_id'] for entry in check_prompt_payload(prompt)['rules']] \
            if '"rules":[' in prompt else None
        if ids is None:
            return json.dumps({'reason': 'single', 'evidence': [], 'status': 'pass'})
        return json.dumps({'verdicts': [{'atomic_id': i, 'reason': 'r-' + i, 'evidence': ['目标: write the report'] if i == 'a' else [],
                                         'status': 'fail' if i == 'b' else 'pass'} for i in ids]})
    results = check_rules(jobs, observation(), invoke, companions=tuple(rule for rule, _ in jobs))
    assert [r.status for r in results] == ['pass', 'pass', 'fail', 'pass']
    assert [r.reason for r in results] == ['r-a', 'single', 'r-b', 'r-c']
    assert results[0].evidence == ('目标: write the report',)
    assert [r.rule_digest for r in results] == [support.rule_digest for _, support in jobs]
    assert len(prompts) == 2                                   # a+b+c together; the lone operations rule on its own
    grouped = next(p for p in prompts if '"rules":[' in p)
    assert grouped.count('"observation"') >= 1 and grouped.count('目标: write the report') == 1   # observation sent once
    body = check_prompt_payload(grouped)
    assert [entry['rule']['atomic_id'] for entry in body['rules']] == ['a', 'b', 'c']
    assert [c['atomic_id'] for c in body['companion_rules']] == ['ops']        # group members are not repeated as companions


def test_unreadable_group_and_ungrounded_verdict_fall_back_to_the_single_check():
    jobs = [job('a', ['task', 'delivery']), job('b', ['task', 'delivery']), job('c', ['task', 'delivery'])]
    calls = {'group': 0, 'single': 0}
    def broken(prompt):
        if '"rules":[' in prompt:
            calls['group'] += 1
            return json.dumps({'verdicts': [{'atomic_id': 'a', 'reason': '', 'evidence': [], 'status': 'pass'}]})   # two missing
        calls['single'] += 1
        return json.dumps({'reason': 'alone', 'evidence': [], 'status': 'pass'})
    results = check_rules(jobs, observation(), broken)
    assert [r.reason for r in results] == ['alone'] * 3 and calls['single'] == 3 and calls['group'] >= 1
    def ungrounded(prompt):
        if '"rules":[' in prompt:
            return json.dumps({'verdicts': [
                {'atomic_id': 'a', 'reason': 'ok', 'evidence': [], 'status': 'pass'},
                {'atomic_id': 'b', 'reason': 'invented', 'evidence': ['text that is not in the observation'], 'status': 'fail'},
                {'atomic_id': 'c', 'reason': 'ok', 'evidence': [], 'status': 'pass'}]})
        return json.dumps({'reason': 'rechecked alone', 'evidence': [], 'status': 'pass'})
    results = check_rules(jobs, observation(), ungrounded)
    assert [r.reason for r in results] == ['ok', 'rechecked alone', 'ok']


def test_groups_are_capped_and_local_decisions_need_no_call():
    jobs = [job('r%d' % i, ['task', 'delivery']) for i in range(8)]
    reminder_rule = Rule('rem', 1, 'Be kind.')
    jobs.append((reminder_rule, Support(rule_digest(reminder_rule), 'test', 'reminder', (), '{}', '{}', 'not checkable')))
    sizes = []
    def invoke(prompt):
        ids = [entry['rule']['atomic_id'] for entry in check_prompt_payload(prompt)['rules']]
        sizes.append(len(ids))
        return json.dumps({'verdicts': [{'atomic_id': i, 'reason': 'ok', 'evidence': [], 'status': 'pass'} for i in ids]})
    results = check_rules(jobs, observation(), invoke)
    assert sizes == [6, 2] and [r.status for r in results] == ['pass'] * 8 + ['unknown']
    assert [r.status for r in check_rules(jobs, observation(), None)] == ['error'] * 8 + ['unknown']
