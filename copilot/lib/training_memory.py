"""Explicit experiment-owned learning, publication and freeze lifecycle.

Call process_pending in the owning background worker, never in the solver's
foreground prompt hook. This module does not launch processes or select models.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from execution_support import SupportError, canonical, digest
from memory_publication import (Publication, _atomic_write, _read, _writer_lock,
                                load_publication, prepare_publication, publish, repair_publication)
from memory_store import DB_FILENAME, MemoryStore
import memory_upsert
from rule_selection import load_snapshot
from model_requests import bind_context, learning_recovery_delay


class _RecoveryRequests:
    """A persisted, one-time recovery has distinct bounded transport receipts."""
    def __init__(self, invoke, turn_key):
        self.invoke,self.turn_key=invoke,turn_key

    def __call__(self,prompt):
        raise SupportError('learning recovery needs a labelled durable request owner')

    def request_validated(self,prompt,*,role,validate,max_attempts=2,request_key=None):
        try:
            return self.invoke.request_validated(prompt,role=role,validate=validate,
                max_attempts=max_attempts,**({'request_key':request_key} if request_key is not None else {}))
        except Exception as exc:
            # Completed stages keep their original durable receipts. Only a
            # proven exhausted transient request may consume the recovery key.
            if learning_recovery_delay(exc) is None:
                raise
        key='learning-recovery-1:'+digest({'turn':self.turn_key,'role':role,
                                         'prompt':prompt,'key':request_key})
        return self.invoke.request_validated(prompt,role=role,validate=validate,
            max_attempts=max_attempts,request_key=key)


class _TrainingRequests:
    def __init__(self,memory):
        self.memory=memory

    def __call__(self,prompt):
        raise SupportError('background learning request is missing its persisted user-event origin')

    def for_turn(self,turn):
        memory=self.memory
        origin=json.loads(_read(memory._origin_path(turn['turn_key'])))
        context_keys={'namespace','phase','scope','task_id','session_id','event_id'}
        if (not isinstance(origin,dict) or set(origin)!=context_keys|{'schema','turn_key','source_digest','public_task_digest'} or
                type(origin['schema']) is not int or origin['schema']!=1 or origin['namespace']!=memory.namespace or origin['phase']!='training' or
                origin['scope']!='user_event' or origin['turn_key']!=turn['turn_key'] or
                origin['source_digest']!=digest(turn['source_text']) or
                not isinstance(origin['public_task_digest'],str) or len(origin['public_task_digest'])!=64 or
                any(c not in '0123456789abcdef' for c in origin['public_task_digest']) or
                origin['turn_key']!='experiment-'+digest({'task':origin['task_id'],'event':origin['event_id']})):
            raise SupportError('learning request origin differs from its owned queued user event')
        bound=bind_context(memory.invoke,{key:origin[key] for key in context_keys})
        receipt=MemoryStore(memory.root,legacy_dirs=[]).learning_recovery(turn['turn_key'])
        return _RecoveryRequests(bound,turn['turn_key']) if receipt and receipt['epoch']==1 else bound


class TrainingMemory:
    def __init__(self, root: Path, *, namespace: str, compiler_id: str, invoke):
        if not isinstance(root, Path) or not root.is_absolute() or root.is_symlink():
            raise SupportError('an explicit absolute experiment library is required')
        if not callable(invoke) or not isinstance(compiler_id, str) or not compiler_id:
            raise SupportError('explicit bounded model transport and compiler identity are required')
        # Opening never initializes, relabels, or imports an old library.
        load_snapshot(root, namespace=namespace)
        self.root = root
        self.namespace = namespace
        self.compiler_id = compiler_id
        self.invoke = invoke
        self.publications = root / 'published'
        ready = load_publication(self.publications, expected_namespace=namespace)
        if ready.compiler_id != compiler_id:
            raise SupportError('compiler configuration changed; use a new experiment identity')

    @classmethod
    def create(cls, root: Path, *, namespace: str, compiler_id: str, invoke):
        if not isinstance(root, Path) or not root.is_absolute() or root.is_symlink():
            raise SupportError('an explicit absolute fresh experiment directory is required')
        if (not isinstance(namespace, str) or not namespace.strip()
                or not isinstance(compiler_id, str) or not compiler_id.strip() or not callable(invoke)):
            raise SupportError('explicit experiment, compiler and transport identities are required')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _writer_lock(root):
            if any(p.name != '.publication.lock' for p in root.iterdir()):
                raise SupportError('new experiments must start in an empty directory')
            store = MemoryStore(root, legacy_dirs=[])
            store.initialize(namespace=namespace)
            snapshot = load_snapshot(root, namespace=namespace)
            publication = prepare_publication(snapshot, invoke, compiler_id=compiler_id)
            publish(root/'published', publication, expected_namespace=namespace)
        return cls(root, namespace=namespace, compiler_id=compiler_id, invoke=invoke)

    def enqueue_user(self, *, task_id: str, session_id: str, event_id: str, source_text: str,
                     public_task: str = '', context_reference=None):
        if any(not isinstance(v, str) or not v.strip() for v in (task_id, session_id, event_id, source_text)):
            raise SupportError('task, event and complete user text are required')
        if context_reference is not None and (not isinstance(context_reference, dict)
                or context_reference.get('session_id') != session_id):
            raise SupportError('context reference does not belong to the current task session')
        if not isinstance(public_task, str):
            raise SupportError('public task context must be text')
        turn_key = 'experiment-' + digest({'task': task_id, 'event': event_id})
        context={'namespace':self.namespace,'phase':'training','scope':'user_event',
                 'task_id':task_id,'session_id':session_id,'event_id':event_id}
        # Validate identities locally, without binding a model owner or calling
        # any model in the foreground admission path.
        bind_context(lambda prompt:None,context)
        origin={**context,'schema':1,'turn_key':turn_key,'source_digest':digest(source_text),
                'public_task_digest':digest(public_task)}
        # This short lock serializes admission against closing training; it is
        # never held over model calls or a SQLite transaction.
        with _writer_lock(self.root):
            if (self.root/'closed-to-input.json').exists() or (self.root/'frozen.json').exists():
                raise SupportError('training admission is closed; test feedback cannot update memory')
            origin_path=self._origin_path(turn_key)
            if origin_path.parent.is_symlink():
                raise SupportError('learning origin directory cannot be a symlink')
            origin_path.parent.mkdir(exist_ok=True,mode=0o700)
            if origin_path.exists():
                if json.loads(_read(origin_path))!=origin:
                    raise SupportError('one user event has conflicting learning origins')
            else:
                _atomic_write(origin_path,canonical(origin),0o600)
            return memory_upsert.enqueue(source_text, turn_key=turn_key,
                context='Public current task (context, not additional authorization):\n'+public_task,
                context_reference=context_reference, memory_dir=self.root,
                namespace=self.namespace, spawn_worker=False, force=True, detect_signal=True)

    def _origin_path(self,turn_key):
        directory=self.root/'.event-origins'
        if directory.is_symlink():
            raise SupportError('learning origin directory cannot be a symlink')
        return directory/(digest(turn_key)+'.json')

    def process_pending(self, *, limit: int = 20):
        if type(limit) is not int or limit < 1:
            raise SupportError('positive worker batch limit is required')
        # One background worker owns processing/publication. A separate short
        # admission lock above lets new prompts enqueue while model work runs.
        with _writer_lock(self.publications):
            if (self.root/'frozen.json').exists():
                raise SupportError('frozen memory cannot run a learning worker')
            requests=_TrainingRequests(self)
            store=MemoryStore(self.root,legacy_dirs=[])
            recovered=[]
            def recover(turn_key):
                receipt=store.learning_recovery(turn_key)
                status=store.get_turn(turn_key)['status']
                if receipt and receipt['epoch']==1 and status in {'pending','resolving'}:
                    if receipt['generation']!=store.generation():
                        raise SupportError('a newer memory commit precedes unfinished learning recovery')
                    recovered.append(memory_upsert.resolve_turn(turn_key,memory_dir=self.root,invoke=requests))
                    return
                if (not receipt or receipt['epoch']!=0 or receipt['generation']!=store.generation() or status!='failed' or
                        not callable(getattr(type(self.invoke),'request_validated',None))):
                    return
                delay=receipt['not_before']-time.time()
                if delay>0:
                    time.sleep(min(delay,60))
                if store.resume_failed_learning(turn_key,namespace=self.namespace):
                    recovered.append(memory_upsert.resolve_turn(turn_key,memory_dir=self.root,invoke=requests))
            # A crash after marking failure cannot lose its recovery receipt.
            for receipt in store.learning_recovery():
                recover(receipt['turn_key'])
            batches=[]
            for _ in range(limit):
                if any(row['epoch']==1 for row in store.learning_recovery()):
                    raise SupportError('unfinished recovery must complete before processing newer training inputs')
                result=memory_upsert.drain(self.root,limit=1,invoke=requests)
                batches.extend(result['results'])
                for item in result['results']:
                    if item.get('status')=='failed' and item.get('turn_key'):
                        recover(item['turn_key'])
                if not result['remaining']:
                    break
            result={**result,'results':batches,'count':len(batches),'learning_recoveries':recovered,
                'remaining':bool(memory_upsert._pending_request_paths(self.root) or
                                 store.pending_turn_keys(limit=1) or store.projection_pending())}
            if any((self.root/memory_upsert.INBOX_DIRNAME/'failed').glob('*.failed')):
                raise SupportError('quarantined learning requests require review before continuing this experiment')
            snapshot = load_snapshot(self.root, namespace=self.namespace)
            previous = load_publication(self.publications, expected_namespace=self.namespace)
            compiler=bind_context(self.invoke,{'namespace':self.namespace,'phase':'training','scope':'publication',
                'task_id':'__library__','session_id':'__publication__','event_id':snapshot.digest})
            publication = prepare_publication(snapshot, compiler, compiler_id=self.compiler_id, previous=previous)
        # publish has its own writer lock and refuses version rollback. Only
        # one experiment worker should call this API; the runtime reads JSON.
        publish(self.publications, publication, expected_namespace=self.namespace)
        return {**result, 'publication_digest': publication.digest, 'generation': publication.generation,
                'pending_rule_ids': list(publication.pending_ids)}

    def finish_training(self, *, max_passes: int = 10, batch_limit: int = 20):
        if type(max_passes) is not int or max_passes < 1:
            raise SupportError('positive drain pass limit is required')
        with _writer_lock(self.root):
            if (self.root/'frozen.json').exists():
                return self.frozen_publication()
            _atomic_write(self.root/'closed-to-input.json', canonical({'namespace': self.namespace}), 0o600)
        for _ in range(max_passes):
            outcome = self.process_pending(limit=batch_limit)
            if not outcome['remaining']:
                break
        else:
            raise SupportError('training worker still has pending work; no test snapshot was exported')
        # Serializes with processing while validating that all committed rules
        # and their prepared support are represented in the exported version.
        with _writer_lock(self.publications):
            if any((self.root/memory_upsert.INBOX_DIRNAME).glob('.*.json.tmp.*')):
                raise SupportError('unpublished inbox requests remain; no test snapshot was exported')
            snapshot = load_snapshot(self.root, namespace=self.namespace)
            publication = load_publication(self.publications, expected_namespace=self.namespace)
            if publication.snapshot.digest != snapshot.digest:
                raise SupportError('learning changed after publication; finish the worker before freezing')
            # A reminder/pending support that the last training update left repairable gets its recompilation
            # here instead of on a later generation that a frozen library never has (2026-10-06, item 51:
            # ClawArena round 30 froze the opening-lines card as a reminder after its fifth revision failed once).
            compiler = bind_context(self.invoke, {'namespace': self.namespace, 'phase': 'training', 'scope': 'publication',
                                                  'task_id': '__library__', 'session_id': '__publication__',
                                                  'event_id': snapshot.digest + ':freeze'})
            repairable = any(s.tier in {'reminder', 'pending'} and json.loads(s.validation_json).get('repairable')
                             for s in getattr(publication, 'supports', ()))
            repaired = repair_publication(publication, compiler, compiler_id=self.compiler_id) if repairable else publication
            freeze_recompiled = [rule.atomic_id for rule, before, after in
                                 zip(publication.snapshot.rules, getattr(publication, 'supports', ()), getattr(repaired, 'supports', ()))
                                 if before != after]
            if freeze_recompiled:
                publish(self.publications, repaired, expected_namespace=self.namespace)
                publication = repaired
            store = MemoryStore(self.root, legacy_dirs=[])
            # A clarification the lifecycle asked and nobody answered before the input closed is dismissed here and
            # listed in the audit: the clear part of such a turn was already committed (mixed plans), the ambiguous
            # clause is not learned, and a frozen library has no later user turn that could answer (2026-10-07,
            # item 53: formal sol x xq x ClawArena could never freeze on one parked needs_user turn, 48 retries).
            dismissed_at_freeze = [row['turn_key'] for row in store.needs_user_turns(limit=1000)]
            for turn_key in dismissed_at_freeze:
                store.dismiss_clarification(turn_key)
            with store.connection() as conn:
                counts = {row['status']: row['n'] for row in conn.execute('SELECT status, COUNT(*) AS n FROM turns GROUP BY status')}
            _atomic_write(self.root/'freeze-audit.json', canonical({'namespace': self.namespace,
                'publication_digest': publication.digest, 'turn_outcomes': counts,
                'pending_rule_ids': list(publication.pending_ids), 'freeze_recompiled': freeze_recompiled,
                'dismissed_at_freeze': dismissed_at_freeze}), 0o400)
            self._require_completed_learning(counts, publication)
            _atomic_write(self.root/'frozen.json', publication.to_json(), 0o400)
        return publication

    @staticmethod
    def _require_completed_learning(counts, publication):
        completed = {'projected', 'noop', 'clarified', 'dismissed', 'rejected'}
        incomplete = {key: count for key, count in counts.items() if count and key not in completed}
        if incomplete:
            # Failed requests remain evidence. Draining a scheduler does not
            # turn a failed learner into completed training or grant retries.
            raise SupportError('learning or projection incomplete at freeze: '+canonical(incomplete))
        if publication.pending_ids:
            raise SupportError('rule support is unpublished; no complete test snapshot was exported')

    def frozen_publication(self) -> Publication:
        publication = Publication.from_json(_read(self.root/'frozen.json'), expected_namespace=self.namespace)
        # Reopening a historical receipt must apply the same admission check,
        # without migrating, retrying, or rewriting its preserved database.
        path = self.root/DB_FILENAME
        if path.is_symlink():
            raise SupportError('frozen learning evidence cannot be a symlink')
        conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
        try:
            counts = dict(conn.execute('SELECT status, COUNT(*) FROM turns GROUP BY status'))
        finally:
            conn.close()
        self._require_completed_learning(counts, publication)
        return publication
