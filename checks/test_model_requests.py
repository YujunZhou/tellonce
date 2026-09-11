from pathlib import Path
import sys
import json
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from model_requests import request_validated, ModelRequestError, exhausted_request, bind_context


class ModelRequestTest(unittest.TestCase):
    def test_shape_and_transport_share_one_retry_budget(self):
        calls=[]
        def invoke(prompt):
            calls.append(prompt)
            if len(calls)==1:
                return 'invalid JSON'
            raise RuntimeError('synthetic private provider detail')
        with self.assertRaises(ModelRequestError) as caught:
            request_validated(invoke,'same input',role='rule_select',validate=json.loads)
        self.assertEqual(calls,['same input','same input'])
        self.assertEqual(caught.exception.attempts,2)
        self.assertNotIn('private provider',str(caught.exception))
        self.assertTrue(exhausted_request(caught.exception))

    def test_managed_owner_receives_explicit_role_and_owns_validation_and_retry(self):
        class Owner:
            def __call__(self,prompt):
                raise AssertionError('unlabelled call')
            def request_validated(self,prompt,*,role,validate,max_attempts):
                self.received=(prompt,role,max_attempts)
                return validate('{"ok":true}'),1
        owner=Owner()
        value,attempts=request_validated(owner,'input',role='compile_support',validate=json.loads)
        self.assertEqual(value,{'ok':True})
        self.assertEqual((attempts,owner.received),(1,('input','compile_support',2)))

    def test_nonretryable_or_deferred_failure_is_not_immediately_retried(self):
        for attributes in ({'retryable':False},{'retry_after_s':3600}):
            calls=[]
            def invoke(prompt):
                calls.append(prompt)
                error=RuntimeError('synthetic')
                for key,value in attributes.items():
                    setattr(error,key,value)
                raise error
            with self.assertRaises(ModelRequestError) as caught:
                request_validated(invoke,'input',role='user_signal',validate=json.loads)
            self.assertEqual(len(calls),1)
            self.assertTrue(exhausted_request(caught.exception))

    def test_failed_experiment_request_is_not_given_five_new_queue_attempts(self):
        from training_memory import TrainingMemory
        for fail_role in ('user_signal','lifecycle_plan'):
            calls=[]
            class Owner:
                def __call__(self,prompt):
                    raise AssertionError('unlabelled call')
                def request_validated(self,prompt,*,role,validate,max_attempts):
                    def once(text):
                        calls.append(role)
                        return 'invalid JSON' if role==fail_role else '{"signal":true}'
                    return request_validated(once,prompt,role=role,validate=validate,max_attempts=max_attempts)
            with tempfile.TemporaryDirectory() as directory:
                memory=TrainingMemory.create(Path(directory),namespace='synthetic-owned',compiler_id='synthetic',invoke=Owner())
                memory.enqueue_user(task_id='task',session_id='session',event_id='event',source_text='Please keep future answers concise.')
                first=memory.process_pending()
                self.assertFalse(first['remaining'])
                self.assertEqual(calls.count(fail_role),2)
                expected=list(calls)
                memory.process_pending()
                memory.finish_training()
                self.assertEqual(calls,expected)

    def test_training_pipeline_labels_learning_and_compilation_without_prompt_inference(self):
        from training_memory import TrainingMemory
        from checks.test_memory_upsert import plan
        text='Please keep future answers concise.'
        roles=[]
        class Owner:
            def __call__(self,prompt):
                raise AssertionError('unlabelled call')
            def request_validated(self,prompt,*,role,validate,max_attempts):
                roles.append(role)
                responses={'user_signal':{'signal':True},
                    'lifecycle_plan':plan('NEW',text,evidence_spans=[text]),
                    'compile_support':{'tier':'reminder','stages':[],'check':{},'cases':[],'reason':'No exact length was supplied.'},
                    'compile_review':{'approved':True,'meaning_clear':True,'reason':'Clear reminder.'}}
                return validate(json.dumps(responses[role])),1
        with tempfile.TemporaryDirectory() as directory:
            memory=TrainingMemory.create(Path(directory),namespace='synthetic-owned',compiler_id='synthetic',invoke=Owner())
            memory.enqueue_user(task_id='task',session_id='session',event_id='event',source_text=text)
            self.assertEqual(roles,[])
            memory.process_pending()
            self.assertEqual(roles,['user_signal','lifecycle_plan','compile_support','compile_review'])

    def test_reopened_background_worker_recovers_each_tasks_explicit_origin(self):
        from training_memory import TrainingMemory
        captured=[]
        class Owner:
            def __init__(self,context=None):
                self.context=context
            def __call__(self,prompt):
                raise AssertionError('unlabelled call')
            def for_context(self,context):
                return Owner(context)
            def request_validated(self,prompt,*,role,validate,max_attempts):
                captured.append((self.context,role))
                response={'signal':True} if role=='user_signal' else {'mutations':[]}
                return validate(json.dumps(response)),1
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            memory=TrainingMemory.create(root,namespace='owned',compiler_id='synthetic',invoke=Owner())
            for number in (1,2):
                memory.enqueue_user(task_id=f'task{number}',session_id=f'session{number}',event_id=f'event{number}',
                                    source_text='Please use the agreed terminology.')
            self.assertEqual(captured,[])
            reopened=TrainingMemory(root,namespace='owned',compiler_id='synthetic',invoke=Owner())
            reopened.process_pending()
            self.assertEqual(len(captured),4)
            for context,role in captured:
                self.assertIsNotNone(context)
                self.assertEqual(context['namespace'],'owned')
                self.assertEqual(context['phase'],'training')
                self.assertEqual(context['scope'],'user_event')
                number=context['task_id'][-1]
                self.assertEqual(context['session_id'],'session'+number)
                self.assertEqual(context['event_id'],'event'+number)

    def test_publication_compilation_is_training_library_work_not_an_arbitrary_task(self):
        from training_memory import TrainingMemory
        from checks.test_memory_upsert import plan
        captured=[]
        text='Please keep future answers concise.'
        class Owner:
            def __init__(self,context=None):
                self.context=context
            def __call__(self,prompt):
                raise AssertionError('unlabelled call')
            def for_context(self,context):
                return Owner(context)
            def request_validated(self,prompt,*,role,validate,max_attempts):
                captured.append((self.context,role))
                values={'user_signal':{'signal':True},'lifecycle_plan':plan('NEW',text,evidence_spans=[text]),
                    'compile_support':{'tier':'reminder','stages':[],'check':{},'cases':[],'reason':'No exact length.'},
                    'compile_review':{'approved':True,'meaning_clear':True,'reason':'Clear.'}}
                return validate(json.dumps(values[role])),1
        with tempfile.TemporaryDirectory() as directory:
            memory=TrainingMemory.create(Path(directory),namespace='owned',compiler_id='synthetic',invoke=Owner())
            memory.enqueue_user(task_id='task',session_id='session',event_id='event',source_text=text)
            memory.process_pending()
            for context,role in captured:
                self.assertEqual(context['phase'],'training')
                if role.startswith('compile_'):
                    self.assertEqual(context['scope'],'publication')
                    self.assertNotEqual(context['task_id'],'task')
                else:
                    self.assertEqual(context['scope'],'user_event')
                    self.assertEqual(context['event_id'],'event')

    def test_test_time_selection_and_scope_expansion_keep_actual_user_event_identity(self):
        from memory_runtime import TaskMemory
        from memory_publication import Publication
        from execution_support import Support, rule_digest
        from rule_selection import Rule, RuleSnapshot
        captured=[]
        class Owner:
            def __init__(self,context=None):
                self.context=context
            def __call__(self,prompt):
                raise AssertionError('unlabelled call')
            def for_context(self,context):
                return Owner(context)
            def request_validated(self,prompt,*,role,validate,max_attempts,request_key=None):
                captured.append((self.context,role,request_key))
                return validate('["r1"]'),1
        rule=Rule('r1',1,'Keep the answer concise.')
        support=Support(rule_digest(rule),'synthetic','reminder',(),'{}','{}','Unspecified exact length.')
        publication=Publication(RuleSnapshot('owned',1,(rule,)),'synthetic',(support,))
        runtime=TaskMemory(publication,expected_namespace='owned',task_id='task',public_task='Task.',invoke=Owner(),training=False)
        with self.assertRaises(ValueError):
            runtime.user_boundary('Task without an event identity.')
        self.assertEqual(captured,[])
        runtime.user_boundary('Task.',session_id='session',event_id='event1')
        runtime.expand_scope('Write a report.')
        runtime.expand_scope('Write a report.')
        runtime.user_boundary('Make it shorter.',session_id='session',event_id='event2')
        self.assertEqual([context['event_id'] for context,role,key in captured],['event1','event1','event1','event2'])
        self.assertTrue(all(context['phase']=='test' and context['task_id']=='task' for context,role,key in captured))
        keys=[key for context,role,key in captured]
        self.assertTrue(all(isinstance(key,str) and key for key in keys))
        self.assertEqual(len(set(keys[:3])),3)
        self.assertEqual(keys[0],keys[3])  # New event owns its own sequence.

    def test_batch_identity_preserves_occurrence_and_batch_without_modifying_prompts(self):
        from rule_selection import Rule,RuleSnapshot,SelectionLimits,select_rules
        seen=[]
        class Owner:
            def __call__(self,prompt):
                raise AssertionError('unlabelled')
            def request_validated(self,prompt,*,role,validate,max_attempts,request_key=None):
                seen.append((request_key,prompt))
                return validate('[]'),1
        snapshot=RuleSnapshot('owned',1,(Rule('a',1,'Use complete sentences.'),Rule('b',1,'Avoid jargon.')))
        select_rules(snapshot,'Task.','task',Owner(),expected_namespace='owned',
                     limits=SelectionLimits(batch_rules=1),request_key='selection:4')
        self.assertEqual([key for key,prompt in seen],['selection:4:batch:0','selection:4:batch:1'])
        self.assertTrue(all('selection:4' not in prompt for key,prompt in seen))

    def test_conflicting_duplicate_or_corrupt_origin_cannot_run_under_another_session(self):
        from training_memory import TrainingMemory
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            memory=TrainingMemory.create(Path(directory),namespace='owned',compiler_id='synthetic',invoke=lambda prompt:calls.append(prompt))
            memory.enqueue_user(task_id='task',session_id='session',event_id='event',source_text='Please keep it concise.')
            with self.assertRaises(ValueError):
                memory.enqueue_user(task_id='task',session_id='other-session',event_id='event',source_text='Please keep it concise.')
            origin=next((memory.root/'.event-origins').glob('*.json'))
            data=json.loads(origin.read_text())
            data['namespace']='other-user'
            origin.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                memory.process_pending()
            self.assertEqual(calls,[])


if __name__=='__main__':
    unittest.main()
