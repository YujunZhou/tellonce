"""Behavioral regressions for role-bound, stage-aware opening obligations."""
from dataclasses import replace
from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from execution_support import check_prompt_payload
from event_obligations import (CapturedEvent, Capture, Obligation, Contract,
                               ObligationSession, ContractError, admit_contract, parse_proposal)


def event(n, text='I will compare dates using the two records; motives are unverified.',
          role='assistant', kind='message', turn='u1'):
    return CapturedEvent('e'+str(n), n, 't', turn, role, kind, text)


def observation(*events, stage='pre_action', complete=True, user='Compare the timeline.',
                action='Read the meeting records', delivery=None, turn='u1'):
    return Capture('t', turn, 'publication-v1', 20, stage, 'Compare the meeting timeline.',
                   user, complete, tuple(events), action, delivery)


def contract(mode='before_first_action'):
    obligation = Obligation('opening', 'When comparing timelines.', 'State the goal and boundaries.',
                            mode, 'Reading the source materials.' if mode=='before_first_action' else '',
                            ('Before reading, state the goal and boundaries.',))
    return Contract('rule-v1', 'test-compiler', 'test-reviewer', (obligation,))


class Semantic:
    """Explicit external-boundary test double, never used in real probes."""
    def __init__(self, scope='applies', bad_ref=False, action='matches', negate=False):
        self.calls=0; self.scope=scope; self.bad_ref=bad_ref; self.action=action; self.negate=negate
    def __call__(self, prompt):
        self.calls+=1
        data=check_prompt_payload(prompt)
        return json.dumps({'scope':self.scope, 'scope_reason':'Controlled public condition.',
            'scope_evidence':[{'id':'user','quote':data['user']}],
            'messages':[{'id':('source-quote' if self.bad_ref else x['id']),
                         'verdict':'violates' if self.negate or 'BAD' in x['text'] else 'satisfies',
                         'quote':x['text'],'reason':'Controlled content judgment.'} for x in data['messages']],
            'actions':[{'id':x['id'],'verdict':self.action,'quote':x['text'],'reason':'Controlled trigger.'}
                       for x in data['actions']]})


class ObligationRegression(unittest.TestCase):
    def run_check(self, obs, *, mode='before_first_action', model=None):
        return ObligationSession(contract(mode), model or Semantic()).check(obs)[0]

    def test_missing_opening_requires_complete_capture(self):
        self.assertEqual(self.run_check(observation()).status, 'fail')
        self.assertEqual(self.run_check(observation(complete=False)).status, 'unknown')

    def test_tool_quote_is_not_assistant_action(self):
        obs=observation(event(1, role='tool', kind='result'))
        self.assertEqual(self.run_check(obs).status, 'fail')

    def test_early_opening_passes_at_delivery_without_repeating_it(self):
        obs=observation(event(1),event(2,'Read records',kind='action'),stage='delivery',
                        action=None,delivery='Meeting A preceded B.')
        self.assertEqual(self.run_check(obs).status, 'pass')
        self.assertEqual(self.run_check(obs,mode='response_start').status, 'pass')

    def test_late_opening_is_irreparable_history_not_repeated_revision(self):
        obs=observation(event(1,'Read records',kind='action'),event(2),stage='delivery',
                        action=None,delivery='Final answer')
        result=self.run_check(obs)
        self.assertEqual(result.status,'fail');self.assertFalse(result.repairable)

    def test_pending_violation_can_be_repaired_before_dispatch(self):
        result=self.run_check(observation())
        self.assertEqual(result.status,'fail');self.assertTrue(result.repairable)

    def test_response_start_not_due_at_pre_action(self):
        model=Semantic()
        self.assertEqual(self.run_check(observation(),mode='response_start',model=model).status,'not_due')
        self.assertEqual(model.calls,0)

    def test_nonmatching_action_does_not_make_future_obligation_due(self):
        self.assertEqual(self.run_check(observation(),model=Semantic(action='unmatched')).status,'not_due')

    def test_unknown_action_does_not_become_confirmed_violation(self):
        self.assertEqual(self.run_check(observation(),model=Semantic(action='unknown')).status,'unknown')

    def test_explicit_override_is_not_violation(self):
        self.assertEqual(self.run_check(observation(user='Return only letters.'),
                                       model=Semantic(scope='overridden')).status,'not_applicable')

    def test_invented_message_identity_is_rejected(self):
        result=self.run_check(observation(event(1)),model=Semantic(bad_ref=True))
        self.assertEqual(result.status,'error')

    def test_later_message_cannot_repair_bad_response_start(self):
        obs=observation(event(1,'BAD opening'),event(2),stage='delivery',action=None,delivery='Final')
        result=self.run_check(obs,mode='response_start')
        self.assertEqual(result.status,'fail');self.assertFalse(result.repairable)

    def test_current_delivery_is_distinct_from_prior_commentary(self):
        obs=observation(event(1),stage='delivery',action=None,delivery='BAD final')
        self.assertEqual(self.run_check(obs,mode='delivery').status,'fail')

    def test_prior_turn_events_cannot_satisfy_current_obligation(self):
        with self.assertRaises(ContractError): observation(event(1,turn='old'))

    def test_duplicate_or_future_events_rejected(self):
        for events in ((event(1),event(1)),(event(22),),(event(3),event(2))):
            with self.subTest(events=events), self.assertRaises(ContractError): observation(*events)

    def test_completed_tool_results_do_not_recheck_satisfied_once_per_turn(self):
        model=Semantic(); session=ObligationSession(contract(),model)
        started=event(2,'Read the meeting records',kind='action')
        self.assertEqual(session.check(observation(event(1),started))[0].status,'pass')
        obs=observation(event(1),started,event(3,'Source says BAD',role='tool',kind='result'),
                        action='Read another record')
        result=session.check(obs)[0]
        self.assertEqual(result.status,'pass');self.assertTrue(result.reused);self.assertEqual(model.calls,1)

    def test_relevant_changed_evidence_or_incomplete_capture_invalidates_reuse(self):
        started=event(2,'Read the meeting records',kind='action')
        original=observation(event(1),started)
        for changed in (observation(event(1,'BAD opening'),started),
                        replace(original,complete=False),replace(original,user='New request'),
                        replace(original,publication_id='publication-v2')):
            with self.subTest(changed=changed):
                model=Semantic(); session=ObligationSession(contract(),model)
                session.check(original);result=session.check(changed)[0]
                self.assertFalse(result.reused)

    def test_delivery_changes_are_rechecked_and_do_not_borrow_pass(self):
        model=Semantic();session=ObligationSession(contract('delivery'),model)
        obs=observation(stage='delivery',action=None,delivery='Good final')
        self.assertEqual(session.check(obs)[0].status,'pass')
        self.assertTrue(session.check(obs)[0].reused)
        result=session.check(replace(obs,delivery='BAD final'))[0]
        self.assertEqual(result.status,'fail');self.assertEqual(model.calls,2)

    def test_false_applicability_is_not_success_and_unknown_is_not_violation(self):
        for scope,expected in [('inapplicable','not_applicable'),('unknown','unknown')]:
            self.assertEqual(self.run_check(observation(),model=Semantic(scope=scope)).status,expected)

    def test_wrong_role_action_metadata_is_rejected(self):
        with self.assertRaises(ContractError):event(1,role='tool',kind='message')

    def test_whole_contract_evaluates_clauses_separately(self):
        first=contract().obligations[0]
        final=replace(first,id='final',mode='delivery',action_condition='')
        session=ObligationSession(replace(contract(),obligations=(first,final)),Semantic())
        results=session.check(observation(event(1)))
        self.assertEqual([x.status for x in results],['pass','not_due'])

    def test_missing_action_is_unknown(self):
        self.assertEqual(self.run_check(observation(action=None)).status,'unknown')

    def test_unknown_or_error_does_not_poison_future_checks(self):
        model=Semantic(scope='unknown');session=ObligationSession(contract(),model)
        obs=observation(event(1));self.assertEqual(session.check(obs)[0].status,'unknown')
        model.scope='applies';self.assertEqual(session.check(obs)[0].status,'pass')
        self.assertEqual(model.calls,2)

    def test_quote_from_another_real_event_cannot_be_swapped(self):
        model=Semantic()
        def bad(prompt):
            value=json.loads(model(prompt));value['messages'][0]['quote']='Invented opening'
            return json.dumps(value)
        self.assertEqual(self.run_check(observation(event(1)),model=bad).status,'error')

    def test_new_turn_never_reuses_previous_pass(self):
        model=Semantic();session=ObligationSession(contract(),model)
        started=event(2,'Read the meeting records',kind='action')
        session.check(observation(event(1),started))
        result=session.check(observation(turn='u2'))[0]
        self.assertEqual(result.status,'fail');self.assertFalse(result.reused)

    def test_only_current_delivery_proof_cannot_latch_as_history(self):
        model=Semantic();session=ObligationSession(contract('response_start'),model)
        obs=observation(stage='delivery',action=None,delivery='Good final')
        self.assertEqual(session.check(obs)[0].status,'pass')
        self.assertEqual(session.check(replace(obs,delivery='BAD final'))[0].status,'fail')

    def test_reused_proof_keeps_original_capture_binding(self):
        session=ObligationSession(contract(),Semantic())
        started=event(2,'Read the meeting records',kind='action')
        result=session.check(observation(event(1),started))[0]
        again=session.check(observation(event(1),started,event(3,role='tool',kind='result')))[0]
        self.assertTrue(again.reused)
        self.assertEqual(result.proof_capture_digest,again.proof_capture_digest)
        self.assertEqual(len(again.proof_capture_digest),64)

    def test_changed_pending_action_is_classified_again(self):
        model=Semantic();session=ObligationSession(contract(),model)
        obs=observation(event(1))
        first=session.check(obs)[0]
        self.assertEqual(first.status,'pass')
        self.assertIn('@action',first.evidence_ids)
        # The boundary classifier now sees a non-triggering operation. A
        # proposed read was never proof that any read actually happened.
        model.action='unmatched'
        again=session.check(replace(obs,action='Print the working directory'))[0]
        self.assertEqual(again.status,'not_due')
        self.assertFalse(again.reused)
        self.assertEqual(model.calls,2)

    def test_cancelled_pending_action_is_not_historical_evidence_at_delivery(self):
        model=Semantic();session=ObligationSession(contract(),model)
        obs=observation(event(1))
        self.assertEqual(session.check(obs)[0].status,'pass')
        final=replace(obs,stage='delivery',action=None,delivery='No search was needed.')
        again=session.check(final)[0]
        self.assertEqual(again.status,'not_due')
        self.assertFalse(again.reused)
        self.assertEqual(model.calls,2)

    def test_admission_requires_review_and_source_binding(self):
        from dataclasses import asdict
        item=asdict(contract().obligations[0]);item['source_quotes']=list(item['source_quotes'])
        rule={'rule_text':item['source_quotes'][0]}
        proposal={'status':'supported','reason':'Exact source','obligations':[item]}
        review={'approved':True,'meaning_clear':True,'reason':'Complete source reviewed'}
        admitted=admit_contract(rule,proposal,review,compiler_id='compiler',reviewer_id='reviewer')
        self.assertEqual(admitted.obligations[0].mode,'before_first_action')
        with self.assertRaises(ContractError):
            admit_contract(rule,proposal,{**review,'approved':False},compiler_id='compiler',reviewer_id='reviewer')
        with self.assertRaises(ContractError):parse_proposal(proposal,{'rule_text':'Another rule'})

    def test_unsupported_proposal_cannot_sneak_in_an_executable_clause(self):
        from dataclasses import asdict
        item=asdict(contract().obligations[0]);item['source_quotes']=list(item['source_quotes'])
        with self.assertRaises(ContractError):
            parse_proposal({'status':'unsupported','reason':'no','obligations':[item]},
                           {'rule_text':item['source_quotes'][0]})


if __name__=='__main__':unittest.main()
