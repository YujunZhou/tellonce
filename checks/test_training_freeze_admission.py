"""Offline regression for terminal learning failures hidden by a drained queue."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
import training_memory
from execution_support import SupportError
from memory_publication import load_publication
from memory_store import MemoryStore
from training_memory import TrainingMemory


def prepare(tmp_path, status):
    def no_model(_prompt):
        raise AssertionError('Freeze admission must not invent extra model calls')
    memory = TrainingMemory.create(tmp_path/'library', namespace='synthetic-a',
                                   compiler_id='offline', invoke=no_model)
    store = MemoryStore(memory.root, legacy_dirs=[])
    store.ensure_turn('turn-a','Please use concrete examples.',expected_namespace=memory.namespace)
    with store.connection() as connection:
        connection.execute('UPDATE turns SET status=?,last_error=? WHERE turn_key=?',
            (status,'synthetic unavailable' if status=='failed' else None,'turn-a'))
    return memory,store


def test_unanswered_clarification_is_dismissed_at_freeze_and_audited(tmp_path):
    """2026-10-07: a formal ClawArena group could never freeze on one parked needs_user turn (the clear part of the
    mixed plan was committed; the ambiguous clause waited for a user who never comes once input is closed)."""
    memory,store = prepare(tmp_path,'needs_user')
    memory.process_pending = lambda **kwargs: {'remaining':False}
    memory.finish_training()
    assert (memory.root/'frozen.json').exists()
    assert store.get_turn('turn-a')['status'] == 'dismissed'
    audit = json.loads((memory.root/'freeze-audit.json').read_text())
    assert audit['dismissed_at_freeze'] == ['turn-a'] and audit['turn_outcomes'] == {'dismissed': 1}


@pytest.mark.parametrize('status',['failed','committed'])
def test_drained_queue_does_not_hide_unfinished_learning(tmp_path,status):
    memory,store = prepare(tmp_path,status)
    # A drained inbox only means nothing remains to schedule.
    memory.process_pending = lambda **kwargs: {'remaining':False}
    before = dict(store.get_turn('turn-a'))
    with pytest.raises(SupportError,match='learning|projection'):
        memory.finish_training()
    assert not (memory.root/'frozen.json').exists()
    assert dict(store.get_turn('turn-a')) == before
    assert json.loads((memory.root/'freeze-audit.json').read_text())['turn_outcomes'][status] == 1


def test_existing_frozen_receipt_cannot_bypass_learning_admission(tmp_path):
    memory,store = prepare(tmp_path,'failed')
    publication = load_publication(memory.publications,expected_namespace=memory.namespace)
    (memory.root/'frozen.json').write_text(publication.to_json())
    before = (memory.root/'frozen.json').read_bytes()
    with pytest.raises(SupportError,match='learning'):
        memory.finish_training()
    with pytest.raises(SupportError,match='learning'):
        memory.frozen_publication()
    assert (memory.root/'frozen.json').read_bytes() == before


def test_noop_is_a_valid_learning_outcome(tmp_path):
    memory,_ = prepare(tmp_path,'noop')
    memory.process_pending = lambda **kwargs: {'remaining':False}
    frozen = memory.finish_training()
    assert memory.finish_training().digest == frozen.digest


def test_pending_compiled_support_blocks_freeze(tmp_path,monkeypatch):
    memory,_ = prepare(tmp_path,'noop')
    publication = load_publication(memory.publications,expected_namespace=memory.namespace)
    incomplete = SimpleNamespace(snapshot=publication.snapshot,digest=publication.digest,
                                 pending_ids=('r1',),to_json=publication.to_json)
    memory.process_pending = lambda **kwargs: {'remaining':False}
    monkeypatch.setattr(training_memory,'load_publication',lambda *args,**kwargs: incomplete)
    with pytest.raises(SupportError,match='support'):
        memory.finish_training()
    assert not (memory.root/'frozen.json').exists()
