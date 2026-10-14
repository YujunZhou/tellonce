"""Bounded opt-in checks share one snapshot and the caller's request context."""
from contextvars import ContextVar
from threading import Barrier, Lock, get_ident
from unittest.mock import patch

import pytest

from checks.test_execution_support import Rule, RuleSnapshot, TaskMemory, Observation, prepare_publication, transport


def memory():
    rules = tuple(Rule('r'+str(i), 1, 'Return a JSON object containing exactly the answer key.') for i in range(8))
    publication = prepare_publication(RuleSnapshot('parallel-owner', 1, rules), transport(), compiler_id='test')
    runtime = TaskMemory(publication, expected_namespace='parallel-owner', task_id='t', public_task='Report.',
                         invoke=lambda _: '["r0","r1","r2","r3","r4","r5","r6","r7"]', training=False)
    runtime.user_boundary('Proceed.')
    return runtime


def test_bounded_parallel_checks_keep_order_snapshot_and_context():
    runtime, observation = memory(), Observation('t', 1, 'delivery', '', delivery='{}')
    publication, selection, invoke = runtime.publication, runtime.selection, runtime.invoke
    deadline = ContextVar('test_parent_deadline', default=None)
    token = deadline.set(12345)
    barrier, lock = Barrier(4), Lock()
    active = peak = 0
    def check(rule, support, observed, owner, *, companions=()):
        # every check sees the other selected rules as companions (2026-10-03, item 32)
        assert {r.atomic_id for r in companions} >= {rule.atomic_id} and len(companions) >= 2
        nonlocal active, peak
        assert observed is observation and owner is invoke
        assert runtime.publication is publication and runtime.selection is selection
        assert deadline.get() == 12345
        # Per-worker context changes must not leak to siblings or the parent.
        deadline.set(rule.atomic_id)
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        barrier.wait(timeout=5)
        return rule.atomic_id
    try:
        with patch('memory_runtime.check_rule', side_effect=check):
            result = runtime.check(observation, max_workers=4, batch=False)
        assert result == tuple('r'+str(i) for i in range(8))
        assert peak == 4 and active == 0 and deadline.get() == 12345
    finally:
        deadline.reset(token)


def test_default_stays_serial_and_parallel_does_not_drop_exceptions():
    runtime, observation = memory(), Observation('t', 1, 'delivery', '', delivery='{}')
    caller = get_ident()
    with patch('memory_runtime.check_rule', side_effect=lambda *a, **k: get_ident()):
        assert runtime.check(observation, batch=False) == (caller,)*8
    with patch('memory_runtime.check_rule', side_effect=ValueError('invalid support')):
        with pytest.raises(ValueError, match='invalid support'):
            runtime.check(observation, max_workers=4, batch=False)


@pytest.mark.parametrize('workers', [True, 0, -1, 17, 1.5])  # cap raised 4 -> 16 on 2026-10-03
def test_parallel_limit_is_explicit_and_bounded(workers):
    with pytest.raises(ValueError):
        memory().check(Observation('t', 1, 'delivery', '', delivery='{}'), max_workers=workers)
