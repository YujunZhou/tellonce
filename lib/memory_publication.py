"""Publish a complete rule/support generation at a user-input boundary.

The writable learning database is staging state. Runtime consumers load only
these immutable publications; tests use an exported JSON value, never a live
database. Publication does not infer any private memory location.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import stat
import time
import uuid

from execution_support import Support, SupportError, canonical, compile_support, digest, rule_digest
from rule_selection import RuleSnapshot

MAX_PUBLICATION_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Publication:
    snapshot: RuleSnapshot
    compiler_id: str
    supports: tuple[Support, ...]

    def __post_init__(self):
        if (not isinstance(self.snapshot, RuleSnapshot) or not isinstance(self.compiler_id, str)
                or not self.compiler_id or not isinstance(self.supports, tuple)
                or len(self.supports) != len(self.snapshot.rules)):
            raise SupportError('publication requires one support outcome per rule')
        for rule, support in zip(self.snapshot.rules, self.supports):
            if (not isinstance(support, Support) or support.rule_digest != rule_digest(rule)
                    or support.compiler_id != self.compiler_id):
                raise SupportError('mixed rule revision or compiler in publication')
            if rule.scope == 'unclear' and support.tier != 'pending':
                raise SupportError('unresolved rule scope cannot enter an active publication')
            if support.tier in {'deterministic', 'semantic'}:
                validation = json.loads(support.validation_json)
                if (validation.get('admitted') is not True
                        or validation.get('review', {}).get('approved') is not True
                        or validation.get('review', {}).get('meaning_clear') is not True):
                    raise SupportError('unvalidated executable support cannot be published')

    @property
    def generation(self):
        return self.snapshot.generation

    @property
    def namespace(self):
        return self.snapshot.namespace

    @property
    def active_snapshot(self):
        return RuleSnapshot(self.namespace, self.generation,
                            tuple(rule for rule, support in zip(self.snapshot.rules, self.supports)
                                  if support.tier != 'pending'))

    @property
    def pending_ids(self):
        return tuple(rule.atomic_id for rule, support in zip(self.snapshot.rules, self.supports)
                     if support.tier == 'pending')

    def payload(self):
        return {'schema': 1, 'snapshot': self.snapshot.to_dict(), 'compiler_id': self.compiler_id,
                'supports': [asdict(s) for s in self.supports]}

    @property
    def digest(self):
        return digest(self.payload())

    def to_json(self):
        return canonical({**self.payload(), 'digest': self.digest})

    @classmethod
    def from_json(cls, text: str, *, expected_namespace: str):
        try:
            if len(text.encode()) > MAX_PUBLICATION_BYTES:
                raise SupportError('publication exceeds the size limit')
            value = json.loads(text)
            if set(value) != {'schema', 'snapshot', 'compiler_id', 'supports', 'digest'} or value['schema'] != 1:
                raise SupportError('invalid publication schema')
            snapshot = RuleSnapshot.from_dict(value['snapshot'])
            snapshot.require_namespace(expected_namespace)
            result = cls(snapshot, value['compiler_id'], tuple(Support.from_dict(s) for s in value['supports']))
            if result.digest != value['digest']:
                raise SupportError('publication hash mismatch')
            return result
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            raise SupportError(f'invalid publication: {exc}') from exc


RECOMPILE_PUBLICATIONS = 2


def prepare_publication(snapshot: RuleSnapshot, invoke, *, compiler_id: str,
                        previous: Publication | None = None) -> Publication:
    reusable = {}
    if previous is not None:
        if previous.namespace != snapshot.namespace:
            raise SupportError('cannot reuse another experiment memory')
        if previous.generation > snapshot.generation:
            raise SupportError('cannot compile an older generation over a newer publication')
        if previous.compiler_id == compiler_id:
            reusable = {support.rule_digest: support for support in previous.supports}
    supports = []
    for rule in snapshot.rules:
        previous_support = reusable.get(rule_digest(rule))
        if previous_support is not None and previous_support.tier in {'reminder', 'pending'}:
            audit = json.loads(previous_support.validation_json)
            # A reminder or a model-declared pending that compilation left repairable (label disagreement, a failed
            # synthetic case, a schema slip, a claimed ambiguity) gets a fresh compilation on up to RECOMPILE_PUBLICATIONS
            # later publications (pending joined on 2026-10-03: ClawArena round 19 published two habit cards as pending);
            # before 2026-10-03 one failed compilation pinned the rule to reminder until its text changed.
            # Only a NEWER generation may carry a different support for the same rule: one generation cannot publish
            # conflicting support (publish() and the runtime both enforce it), so a same-generation republication
            # (a NOOP turn) reuses the reminder unchanged — 2026-10-03 round 16 lost its background learner to that.
            if (snapshot.generation > previous.generation and audit.get('repairable')
                    and audit.get('recompiled_publications', 0) < RECOMPILE_PUBLICATIONS):
                fresh = compile_support(rule, invoke, compiler_id=compiler_id)
                if fresh.tier in {'reminder', 'pending'}:
                    record = json.loads(fresh.validation_json)
                    record['recompiled_publications'] = audit.get('recompiled_publications', 0) + 1
                    fresh = Support(fresh.rule_digest, fresh.compiler_id, fresh.tier, fresh.stages,
                                    fresh.check_json, canonical(record), fresh.reason)
                supports.append(fresh)
                continue
        supports.append(previous_support or compile_support(rule, invoke, compiler_id=compiler_id))
    return Publication(snapshot, compiler_id, tuple(supports))


def is_support_repair(current: Publication, candidate: Publication) -> bool:
    """True when `candidate` is `current` with some reminder/pending supports that compilation had left repairable
    replaced by executable support — the same rules, generation, namespace and compiler; nothing else may differ.
    This is the one same-generation replacement publish() and the runtime admit (2026-10-06, item 51): a rule whose
    last compilation failed at the final training update otherwise stays unenforced for the whole test phase."""
    if (current.namespace != candidate.namespace or current.generation != candidate.generation
            or current.snapshot.digest != candidate.snapshot.digest or current.compiler_id != candidate.compiler_id
            or len(current.supports) != len(candidate.supports)):
        return False
    upgraded = 0
    for before, after in zip(current.supports, candidate.supports):
        if before == after:
            continue
        if before.rule_digest != after.rule_digest or before.tier not in {'reminder', 'pending'}:
            return False
        if not json.loads(before.validation_json).get('repairable') or after.tier not in {'deterministic', 'semantic'}:
            return False
        upgraded += 1
    return upgraded > 0


def repair_publication(current: Publication, invoke, *, compiler_id: str) -> Publication:
    """Recompile the repairable reminder/pending supports of `current` once more (the freeze-time round that a
    reminder left by the last training update never gets from prepare_publication, which only recompiles on a later
    generation). Supports that stay reminder/pending are kept as they were; the result is `current` itself when
    nothing could be upgraded."""
    if current.compiler_id != compiler_id:
        raise SupportError('cannot repair support compiled by another compiler')
    supports = []
    for rule, support in zip(current.snapshot.rules, current.supports):
        audit = json.loads(support.validation_json)
        if (support.tier in {'reminder', 'pending'} and audit.get('repairable')
                and audit.get('recompiled_publications', 0) < RECOMPILE_PUBLICATIONS):
            fresh = compile_support(rule, invoke, compiler_id=compiler_id)
            if fresh.tier in {'deterministic', 'semantic'}:
                record = json.loads(fresh.validation_json)
                record['freeze_recompiled'] = True
                supports.append(Support(fresh.rule_digest, fresh.compiler_id, fresh.tier, fresh.stages,
                                        fresh.check_json, canonical(record), fresh.reason))
                continue
        supports.append(support)
    candidate = Publication(current.snapshot, compiler_id, tuple(supports))
    return candidate if is_support_repair(current, candidate) else current


def _directory(root: Path):
    if not isinstance(root, Path) or not root.is_absolute():
        raise SupportError('an explicit absolute publication directory is required')
    if root.is_symlink():
        raise SupportError('publication directory cannot be a symlink')
    return root


def _read(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PUBLICATION_BYTES:
            raise SupportError('publication must be a bounded regular file')
        data = stream.read(MAX_PUBLICATION_BYTES + 1)
    if len(data) > MAX_PUBLICATION_BYTES:
        raise SupportError('publication grew beyond the size limit')
    return data.decode('utf-8')


@contextmanager
def _writer_lock(root: Path):
    lock = root / '.publication.lock'
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(lock, flags, 0o600)
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SupportError('publication lock is not a regular file')
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'0')
        try:
            import fcntl
            def acquire(): fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release(): fcntl.flock(fd, fcntl.LOCK_UN)
        except ImportError:
            import msvcrt
            def acquire():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            def release():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        deadline = time.monotonic() + 30
        while True:
            try:
                acquire()
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise SupportError('publication writer lock timed out')
                time.sleep(.05)
        yield
    finally:
        try:
            if locked:
                release()
        finally:
            os.close(fd)


def _atomic_write(path: Path, text: str, mode: int):
    data = text.encode()
    if len(data) > MAX_PUBLICATION_BYTES:
        raise SupportError('publication exceeds the size limit')
    temp = path.parent / ('.pending-' + uuid.uuid4().hex)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            os.chmod(temp, mode)
        os.replace(temp, path)
        # Freeze/admission markers also use this helper outside publish().
        # Persist the directory entry before a later marker can be committed.
        if hasattr(os, 'O_DIRECTORY'):
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temp.exists():
            temp.unlink()


def publish(root: Path, publication: Publication, *, expected_namespace: str) -> Path:
    root = _directory(root)
    publication.snapshot.require_namespace(expected_namespace)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _writer_lock(root):
        pointer = root / 'current.json'
        if pointer.exists() or pointer.is_symlink():
            current = load_publication(root, expected_namespace=expected_namespace)
            if current.generation > publication.generation:
                raise SupportError('cannot replace a newer publication with an older generation')
            if current.generation == publication.generation and current.digest != publication.digest:
                if not is_support_repair(current, publication):
                    raise SupportError('one generation cannot publish conflicting support; use a new run or generation')
            elif current.generation == publication.generation:
                return root / (publication.digest + '.json')
        destination = root / (publication.digest + '.json')
        if destination.exists() or destination.is_symlink():
            existing = Publication.from_json(_read(destination), expected_namespace=expected_namespace)
            if existing.digest != publication.digest:
                raise SupportError('existing immutable publication conflicts')
        else:
            _atomic_write(destination, publication.to_json(), 0o400)
        _atomic_write(pointer, canonical({'digest': publication.digest, 'namespace': expected_namespace}), 0o600)
        if hasattr(os, 'O_DIRECTORY'):
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return destination


def load_publication(root: Path, *, expected_namespace: str) -> Publication:
    root = _directory(root)
    pointer = json.loads(_read(root / 'current.json'))
    expected = pointer.get('digest')
    if (set(pointer) != {'digest', 'namespace'} or pointer['namespace'] != expected_namespace
            or not isinstance(expected, str) or len(expected) != 64
            or any(c not in '0123456789abcdef' for c in expected)):
        raise SupportError('invalid publication pointer or experiment ownership')
    value = Publication.from_json(_read(root / (expected + '.json')), expected_namespace=expected_namespace)
    if value.digest != expected:
        raise SupportError('publication pointer does not match immutable artifact')
    return value
