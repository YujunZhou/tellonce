"""Task-local consumption of an immutable, jointly published memory version."""
from __future__ import annotations

from dataclasses import dataclass

from execution_support import Observation, SupportError, check_rule, digest
from memory_publication import Publication
from rule_selection import SelectionError, SelectionLimits, SelectionResult, _rendered_length, render_rule, select_rules
from model_requests import bind_context


@dataclass(frozen=True)
class BoundarySelection:
    publication_digest: str
    result: SelectionResult


def all_rule_text(publication: Publication, *, max_chars: int) -> str:
    """Ablation A must fit completely or fail explicitly, never truncate."""
    if type(max_chars) is not int or max_chars < 1:
        raise SupportError('positive all-rule input budget is required')
    rules = publication.active_snapshot.rules
    required = sum(_rendered_length(rule) for rule in rules) + max(0, len(rules)-1)*2
    if required > max_chars:
        raise SupportError('all-rule ablation exceeds its declared context budget')
    return '\n\n'.join(render_rule(rule) for rule in rules)


class TaskMemory:
    """One instance per independent task; no task state is exported as memory.

    Publication changes are accepted only at explicit user-input boundaries in
    training. During a turn, scope expansion reuses the pinned publication.
    """

    def __init__(self, publication: Publication, *, expected_namespace: str,
                 task_id: str, public_task: str, invoke, training: bool,
                 execution_enabled: bool = True, limits: SelectionLimits | None = None):
        publication.snapshot.require_namespace(expected_namespace)
        if not isinstance(task_id, str) or not task_id or not isinstance(public_task, str):
            raise SupportError('task identity and public task text are required')
        if type(training) is not bool or type(execution_enabled) is not bool:
            raise SupportError('training and execution flags must be boolean')
        self._publication = publication
        self.namespace = expected_namespace
        self.task_id = task_id
        self.public_task = public_task
        self.invoke = invoke
        self._base_invoke = invoke
        self.training = training
        self.execution_enabled = execution_enabled
        self.limits = limits or SelectionLimits()
        self._latest_input = ''
        self._selection: BoundarySelection | None = None
        self._selection_sequence = 0

    @property
    def publication(self):
        return self._publication

    @property
    def selection(self):
        return self._selection

    def _select(self, operation: str = '') -> BoundarySelection:
        query = ('Current public task:\n' + self.public_task
                 + '\nLatest user input:\n' + self._latest_input
                 + '\nCurrent operation scope:\n' + operation)
        snapshot = self._publication.active_snapshot
        previous = self._selection.result if self._selection else None
        sequence=self._selection_sequence
        self._selection_sequence+=1
        try:
            result = select_rules(snapshot, query, self.task_id, self.invoke,
                                  expected_namespace=self.namespace, previous=previous, limits=self.limits,
                                  request_key=f'selection:{sequence}')
        except SelectionError as exc:
            # A budget/serialization failure is visible, but cannot prevent
            # answering the user's task or silently substitute the whole library.
            result = SelectionResult('error', snapshot.digest, self.task_id, digest(query), (), '',
                                      unexamined_ids=tuple(rule.atomic_id for rule in snapshot.rules),
                                      errors=(str(exc),))
        self._selection = BoundarySelection(self._publication.digest, result)
        return self._selection

    def user_boundary(self, user_input: str, *, ready_publication: Publication | None = None,
                      session_id: str = '', event_id: str = ''):
        if not isinstance(user_input, str):
            raise SupportError('user input must be text')
        publication=self._publication
        if ready_publication is not None:
            ready_publication.snapshot.require_namespace(self.namespace)
            if not self.training and ready_publication.digest != self._publication.digest:
                raise SupportError('test-time memory is frozen')
            if ready_publication.generation < self._publication.generation:
                raise SupportError('cannot roll memory back at a user boundary')
            if (ready_publication.generation == self._publication.generation
                    and ready_publication.digest != self._publication.digest):
                raise SupportError('one generation has conflicting execution support')
            publication = ready_publication
        invoke=self.invoke
        if session_id or event_id or callable(getattr(type(self._base_invoke),'for_context',None)):
            invoke=bind_context(self._base_invoke,{'namespace':self.namespace,
                'phase':'training' if self.training else 'test','scope':'user_event','task_id':self.task_id,
                'session_id':session_id,'event_id':event_id})
        self._publication,self.invoke=publication,invoke
        self._latest_input = user_input
        self._selection_sequence = 0
        return self._select()

    def expand_scope(self, operation: str):
        if self._selection is None:
            raise SupportError('begin with a user-input boundary before expanding scope')
        if not isinstance(operation, str) or not operation.strip():
            raise SupportError('scope expansion requires the actual proposed operation')
        return self._select(operation)

    def describe_rules(self, rule_digests: tuple[str, ...]) -> dict[str, str]:
        """Explain checked rules from this pinned version, preserving exceptions.

        Known blocks can outlive an operation's selection, so the host may
        request a previously checked digest. No other rules are returned.
        """
        if (not isinstance(rule_digests,tuple) or any(not isinstance(value,str) or not value for value in rule_digests)
                or len(set(rule_digests)) != len(rule_digests)):
            raise SupportError('explicit distinct checked rule digests are required')
        requested = set(rule_digests)
        active = {rule.atomic_id for rule in self._publication.active_snapshot.rules}
        result = {support.rule_digest:render_rule(rule)
                  for rule,support in zip(self._publication.snapshot.rules,self._publication.supports)
                  if support.rule_digest in requested and rule.atomic_id in active}
        if set(result) != requested:
            raise SupportError('cannot describe a foreign or inactive rule from this publication')
        return result

    def check(self, observation: Observation):
        if observation.task_id != self.task_id:
            raise SupportError('cannot inspect a different task through this memory instance')
        if not self.execution_enabled:
            return ()
        if self._selection is None or self._selection.publication_digest != self._publication.digest:
            raise SupportError('checks require selection from the current pinned publication')
        selected = set(self._selection.result.selected_ids)
        return tuple(check_rule(rule, support, observation, self.invoke)
                     for rule, support in zip(self._publication.snapshot.rules, self._publication.supports)
                     if rule.atomic_id in selected and support.tier in {'deterministic', 'semantic'}
                     and observation.stage in support.stages)
