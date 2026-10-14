"""Bounded outage recovery must preserve instruction order and frozen memory."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from memory_store import MemoryStore,MemoryStoreError
from model_requests import ModelRequestError,learning_recovery_delay
from training_memory import TrainingMemory,_TrainingRequests
from execution_support import SupportError
from memory_publication import load_publication
import memory_upsert
from checks.test_model_requests import interpretation
from checks.test_memory_upsert import plan

TEXT={'old':'Keep future answers concise.','new':'Use detailed explanations in future answers.'}


class Owner:
    def __init__(self, state, context=None):
        self.state,self.context=state,context

    def __call__(self,prompt):
        raise AssertionError('unlabelled model request')

    def for_context(self,context):
        return Owner(self.state,dict(context))

    def request_validated(self,prompt,*,role,validate,max_attempts=2,request_key=None):
        event=self.context['event_id']
        cache_key=(event,role,request_key,prompt)
        if cache_key in self.state.setdefault('failed',set()):
            raise ModelRequestError(role,2,[self.state.get('failure','provider_timeout')]*2)
        if cache_key in self.state.setdefault('replies',{}):
            return validate(self.state['replies'][cache_key]),1
        self.state['calls'].append((event,role,request_key))
        if (event=='old' and role==self.state.get('fail_role','user_signal') and
                (request_key is None or self.state.get('permanent'))):
            self.state['failed'].add(cache_key)
            raise ModelRequestError(role,2,[self.state.get('failure','provider_timeout')]*2)
        text=TEXT.get(event,'')
        values={'user_signal':{'signal':True},'correction_interpretation':interpretation(text),
            'compile_support':{'tier':'reminder','stages':[],'check':{},'cases':[],'reason':'Qualitative reminder.'},
            'compile_review':{'approved':True,'meaning_clear':True,'reason':'Clear reminder.'}}
        if role in {'lifecycle_plan','lifecycle_select'}:
            rules=MemoryStore(self.state['root'],legacy_dirs=[]).snapshot()[1]
            values['lifecycle_select']=[r['atomic_id'] for r in rules]
            values['lifecycle_plan']=plan('UPDATE' if rules else 'NEW',text,
                target_ids=[rules[0]['atomic_id']] if rules else [],evidence_spans=[text])
        reply=json.dumps(values[role])
        self.state['replies'][cache_key]=reply
        return validate(reply),1


class LearningRecoveryTest(unittest.TestCase):
    def create(self,root,**overrides):
        state=dict(root=root,calls=[],**overrides)
        memory=TrainingMemory.create(root,namespace='owned',compiler_id='synthetic',invoke=Owner(state))
        return memory,state

    def enqueue(self,memory,event):
        memory.enqueue_user(task_id='task',session_id='session',event_id=event,source_text=TEXT[event])

    def test_only_transient_failures_receive_bounded_recovery(self):
        for code in ['provider_timeout','provider_rate_limited','provider_service_unavailable','response_validation_failed']:
            self.assertEqual(learning_recovery_delay(ModelRequestError('user_signal',2,[code])),5)
        # answers rejected by the validator twice get the same single recovery epoch (2026-10-04)
        self.assertEqual(learning_recovery_delay(ModelRequestError('lifecycle_plan',2,['response_validation_failed','response_validation_failed'])),5)
        for code in ['provider_text_failed','text_input_budget','provider_authorization_failed',
                     'provider_credit_exhausted','request_deadline','invalid_json','http_text_failed']:
            self.assertIsNone(learning_recovery_delay(ModelRequestError('user_signal',2,[code])))
        self.assertIsNone(learning_recovery_delay(ModelRequestError('user_signal',2,['provider_timeout'],retry_after_s=61)))

    def test_recovery_finishes_before_newer_conflicting_instruction(self):
        for role in ['user_signal','correction_interpretation','lifecycle_plan']:
            with self.subTest(role=role),tempfile.TemporaryDirectory() as tmp:
                memory,state=self.create(Path(tmp),fail_role=role)
                self.enqueue(memory,'old');self.enqueue(memory,'new')
                with patch.object(memory_upsert,'learning_recovery_delay',return_value=0):
                    outcome=memory.process_pending()
                self.assertFalse(outcome['remaining'])
                self.assertEqual(outcome['learning_recoveries'][0]['status'],'projected')
                old_recovery=next(i for i,c in enumerate(state['calls']) if c[0]=='old' and c[2])
                newer=next(i for i,c in enumerate(state['calls']) if c[0]=='new')
                self.assertLess(old_recovery,newer)
                frozen=memory.finish_training()
                self.assertEqual([r.rule_text for r in frozen.active_snapshot.rules],[TEXT['new']])
                self.assertEqual(json.loads((memory.root/'freeze-audit.json').read_text())['turn_outcomes'],{'projected':2})

    def test_recovery_exhaustion_is_not_reset_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory,state=self.create(Path(tmp),permanent=True)
            self.enqueue(memory,'old')
            with patch.object(memory_upsert,'learning_recovery_delay',return_value=0):
                memory.process_pending()
                for _ in range(3):
                    memory=TrainingMemory(Path(tmp),namespace='owned',compiler_id='synthetic',invoke=Owner(state))
                    memory.process_pending()
            calls=[c for c in state['calls'] if c[1]=='user_signal']
            self.assertEqual(len(calls),2)  # Two logical epochs, each bounded to two physical attempts.
            with self.assertRaises(SupportError):
                memory.finish_training()
            self.assertFalse((memory.root/'frozen.json').exists())
            self.assertEqual(json.loads((memory.root/'freeze-audit.json').read_text())['turn_outcomes'],{'failed':1})

    def test_crash_before_and_after_consuming_epoch_resumes_before_new_input(self):
        for consume in [False,True]:
            with self.subTest(consume=consume),tempfile.TemporaryDirectory() as tmp:
                memory,state=self.create(Path(tmp))
                self.enqueue(memory,'old')
                with patch.object(memory_upsert,'learning_recovery_delay',return_value=0):
                    memory_upsert.drain(memory.root,limit=1,invoke=_TrainingRequests(memory))
                store=MemoryStore(memory.root,legacy_dirs=[])
                receipt=store.learning_recovery()[0]
                if consume:
                    self.assertTrue(store.resume_failed_learning(receipt['turn_key'],namespace='owned'))
                self.enqueue(memory,'new')
                reopened=TrainingMemory(Path(tmp),namespace='owned',compiler_id='synthetic',invoke=Owner(state))
                reopened.process_pending()
                self.assertEqual([r.rule_text for r in reopened.finish_training().active_snapshot.rules],[TEXT['new']])

    def test_newer_commit_and_namespace_prevent_old_recovery_and_block_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory,state=self.create(Path(tmp))
            self.enqueue(memory,'old')
            with patch.object(memory_upsert,'learning_recovery_delay',return_value=0):
                memory_upsert.drain(memory.root,limit=1,invoke=_TrainingRequests(memory))
            store=MemoryStore(memory.root,legacy_dirs=[])
            key=store.learning_recovery()[0]['turn_key']
            with self.assertRaises(MemoryStoreError):
                store.resume_failed_learning(key,namespace='another-user')
            self.enqueue(memory,'new')
            memory_upsert.drain(memory.root,limit=1,invoke=_TrainingRequests(memory))
            self.assertFalse(store.resume_failed_learning(key,namespace='owned'))
            with self.assertRaises(SupportError):
                memory.finish_training()
            publication=load_publication(memory.publications,expected_namespace=memory.namespace)
            self.assertEqual([r.rule_text for r in publication.active_snapshot.rules],[TEXT['new']])
            self.assertFalse(store.resume_failed_learning(key,namespace='owned'))
            self.assertFalse((memory.root/'frozen.json').exists())


if __name__=='__main__':
    unittest.main()
