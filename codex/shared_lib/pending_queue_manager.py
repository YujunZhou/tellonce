#!/usr/bin/env python3
"""Pending memory queue manager.

3 commands:
  promote — Stop hook: scan observations.jsonl tail, find detected=True + saved=pending
            entries older than PROMOTE_AGE_MIN, append to pending_queue.jsonl (dedup
            by source_obs_entry_id). If queue >= ALERT_LEN_THRESHOLD, write
            PENDING_ALERT.md + stderr warn (advisory, no exit-2 blocking).
  inject  — UserPromptSubmit hook: read pending_queue.jsonl, format
            additionalContext text listing unfinalized entries. stdout text only.
  prune   — Maintenance: scan memory dir; if a queue entry's proposed_atomic_id
            now exists as a memory file, drop it from the queue (resolved).
            Supports `--force <queue_entry_id>` to drop entries unconditionally
            (used for entries with proposed_atomic_id=<unknown> or non-canonical
            ids that auto-prune cannot detect; user decides NOOP/discard manually).

Defensive: any failure → exit 0 silently (never block hooks).
Rationale: pending entries can be lost if a session crashes mid-turn.
This gate guarantees pending entries survive any crash via the queue file.
"""
import contextlib
try:
    import fcntl
except ImportError:
    fcntl = None  # Windows
import json
import os
import re
import sys
import glob
import shutil
import uuid
from datetime import datetime, timezone, timedelta

import sys as _sys
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config
import redaction  # redact secrets in queue entries

OBS_LOG = path_config.get_observations_log_path()
QUEUE = path_config.get_pending_queue_path()
ALERT = path_config.get_pending_alert_path()
ERROR_LOG = path_config.get_pending_error_log_path()
MEMORY_DIR = path_config.get_memory_dir()

# Age threshold raised 30 → 60 min so active autonomous
# blocks (often >30 min) don't promote their own in-flight pending
# obs as "from prior session". Cross-session crashes still surface via inject hook
# (which runs on next UserPromptSubmit after the crash regardless of age).
PROMOTE_AGE_MIN = 60          # pending obs older than this → eligible for promotion
SCAN_TAIL_LINES = 200         # how many obs lines to consider per promote pass
ALERT_LEN_THRESHOLD = 3       # queue length triggering PENDING_ALERT (advisory)
INJECT_TOPN_CAP = 12          # cap inject text to top N entries (newest first); overflow shows count


# The queue is read-modify-written by both
# promote and prune. Two Claude sessions on the same project running these
# concurrently can clobber one another (prune writes to a fixed `.tmp` and
# replaces, missing entries that promote just appended). Wrap both with a
# shared flock so RMW completes atomically across processes.
@contextlib.contextmanager
def _queue_lock():
    """Cross-process exclusive lock on the pending queue file.

    Best-effort: if the lock cannot be acquired (filesystem doesn't support
    flock — e.g. some NFS configs), fall through without lock so we don't
    silently drop functionality. Logs the fallback once.
    """
    lock_dir = os.path.dirname(QUEUE)
    try:
        os.makedirs(lock_dir, exist_ok=True)
    except Exception:
        pass
    lock_path = QUEUE + '.lock'
    fh = None
    try:
        fh = open(lock_path, 'a+')
        try:
            if fcntl:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            else:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        except (OSError, ValueError):
            # flock unsupported; proceed unlocked
            try:
                fh.close()
            except Exception:
                pass
            fh = None
        yield
    finally:
        if fh is not None:
            try:
                if fcntl:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                else:
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass


def _atomic_replace_queue(keep):
    """Rewrite QUEUE atomically. Caller must hold _queue_lock()."""
    pid = os.getpid()
    tmp = f'{QUEUE}.tmp.{pid}.{uuid.uuid4().hex[:8]}'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            for e in keep:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
        path_config.chmod_or_warn(tmp, 0o600)
        os.replace(tmp, QUEUE)
        path_config.chmod_or_warn(QUEUE, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso(ts):
    """Parse ISO-8601 timestamp robustly (with or without trailing Z / tz)."""
    if not ts:
        return None
    try:
        s = ts.strip().replace('Z', '+00:00')
        return datetime.fromisoformat(s)
    except Exception:
        try:
            # bare ISO no tz → assume UTC
            return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _now():
    return datetime.now(timezone.utc)


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _read_jsonl_tail(path, n_lines):
    if not os.path.exists(path):
        return []
    try:
        with open(path, errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return []
    out = []
    for line in lines[-n_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _log_error(where, exc):
    """Opt-in forensic log. Silent fallback so we never break the
    "exit 0 always" invariant, but operators get a trail when corruption happens."""
    try:
        os.makedirs(os.path.dirname(ERROR_LOG), exist_ok=True)
        with open(ERROR_LOG, 'a', encoding='utf-8') as f:
            # Error message may quote a path or value with secrets.
            f.write(json.dumps({
                'ts': _now().isoformat(),
                'where': where,
                'err': redaction.redact(str(exc)[:500]),
                'err_type': type(exc).__name__,
            }, ensure_ascii=False) + '\n')
        path_config.chmod_or_warn(ERROR_LOG, 0o600)
    except Exception:
        pass


# Module-level cache keyed on directory mtime so we re-scan only
# when memory dir actually changes. Memory dir holds 191+ files; per-Stop scan was
# costing ~2s of the 5s hook timeout budget.
_MEMORY_IDS_CACHE = {'mtime': None, 'ids': set()}


def _atomic_ids_in_memory():
    """Scan memory_dir/*.md frontmatter and return set of atomic_ids present.
    Cached by directory mtime to avoid re-scanning on every Stop event."""
    ids = set()
    if not os.path.isdir(MEMORY_DIR):
        return ids
    try:
        mtime = os.path.getmtime(MEMORY_DIR)
    except Exception as e:
        _log_error('_atomic_ids_in_memory.getmtime', e)
        mtime = None
    if mtime is not None and _MEMORY_IDS_CACHE['mtime'] == mtime:
        return _MEMORY_IDS_CACHE['ids']
    for path in glob.glob(os.path.join(MEMORY_DIR, '*.md')):
        if os.path.basename(path) == 'MEMORY.md':
            continue
        try:
            with open(path, errors='ignore') as f:
                head = f.read(2000)
            m = re.search(r'^atomic_id:\s*([a-z]+-[a-z]+-\d+)', head, re.MULTILINE)
            if m:
                ids.add(m.group(1))
        except Exception as e:
            _log_error(f'_atomic_ids_in_memory.read:{os.path.basename(path)}', e)
            continue
    if mtime is not None:
        _MEMORY_IDS_CACHE['mtime'] = mtime
        _MEMORY_IDS_CACHE['ids'] = ids
    return ids


def _is_pending_obs(o):
    det = o.get('detection') or {}
    act = o.get('action') or {}
    if not det.get('detected'):
        return False
    saved = (act.get('saved_to_memory') or '').lower()
    return saved == 'pending'


def _obs_age_min(o):
    ts = _parse_iso(o.get('timestamp'))
    if ts is None:
        return None
    return (_now() - ts).total_seconds() / 60.0


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def promote_from_observations():
    """Scan obs tail; promote eligible pending entries to queue.

    Skips obs whose proposed_atomic_id already exists as a memory file (logically
    resolved — re-adding after prune would oscillate).

    Returns dict {scanned, candidates, newly_promoted, queue_len_after,
                  skipped_already_in_memory}.
    """
    # Promote takes the queue lock for the read+append window so a
    # concurrent prune can't rewrite QUEUE between our _read_jsonl(QUEUE) and
    # the open(QUEUE, 'a') below — that's the path that loses promoted entries.
    with _queue_lock():
        return _promote_locked()


def _promote_locked():
    obs = _read_jsonl_tail(OBS_LOG, SCAN_TAIL_LINES)
    # Short-circuit before _atomic_ids_in_memory if no candidates.
    # Most Stop events have zero pending obs in the tail; this avoids the 191-file
    # glob+regex on the hot path.
    pending_count = sum(1 for o in obs if _is_pending_obs(o))
    existing = _read_jsonl(QUEUE)
    have = {e.get('source_obs_entry_id') for e in existing if e.get('source_obs_entry_id')}
    memory_atomic_ids = _atomic_ids_in_memory() if pending_count > 0 else set()

    newly_promoted = []
    candidates = 0
    skipped_already_in_memory = 0
    for o in obs:
        if not _is_pending_obs(o):
            continue
        candidates += 1
        eid = o.get('entry_id')
        if not eid or eid in have:
            continue
        age = _obs_age_min(o)
        if age is None or age < PROMOTE_AGE_MIN:
            continue
        det = o.get('detection') or {}
        act = o.get('action') or {}
        trig = o.get('trigger') or {}
        # Skip obs already finalized to memory (proposed_atomic_id matches an
        # existing memory file). This prevents oscillation: prune drops the
        # entry from the queue, but the obs still says saved=pending, so
        # without this check promote would keep re-adding it.
        proposed = act.get('proposed_atomic_id') or ''
        if proposed and proposed in memory_atomic_ids:
            skipped_already_in_memory += 1
            continue
        entry = {
            'queue_entry_id': f'{_now().isoformat()}-pq-{len(have) + len(newly_promoted) + 1:04d}',
            'promoted_at': _now().isoformat(),
            'source_obs_entry_id': eid,
            'source_session_id': o.get('session_id', ''),
            'source_obs_timestamp': o.get('timestamp'),
            'age_minutes_at_promote': round(age, 1),
            'project': o.get('project', ''),
            'cwd': o.get('cwd', ''),
            'detected_signal': {
                'signal_type': det.get('signal_type'),
                'content': det.get('content'),
                'domain': det.get('domain'),
                'scope': det.get('scope'),
                'confidence': det.get('confidence'),
            },
            'proposed_atomic_id': act.get('proposed_atomic_id'),
            'confirmation_text': act.get('confirmation_text'),
            'user_message_excerpt': trig.get('user_message_excerpt'),
        }
        # detected_signal.content / confirmation_text /
        # user_message_excerpt all quote raw user/agent text and may contain
        # API keys / SSH keys / DB URIs. Redact before persisting.
        entry = redaction.sanitize(entry)
        newly_promoted.append(entry)
        have.add(eid)

    if newly_promoted:
        try:
            os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
            with open(QUEUE, 'a', encoding='utf-8') as f:
                for e in newly_promoted:
                    f.write(json.dumps(e, ensure_ascii=False) + '\n')
            # Queue carries user-content excerpts; restrict.
            path_config.chmod_or_warn(QUEUE, 0o600)
        except Exception:
            pass

    queue_after = _read_jsonl(QUEUE)
    queue_len = len(queue_after)

    # A.4 pending alert (advisory)
    if queue_len >= ALERT_LEN_THRESHOLD:
        _write_pending_alert(queue_after)
        try:
            sys.stderr.write(
                f'\U0001f534 PENDING ALERT: {queue_len} pending memory entries unresolved '
                f'(threshold {ALERT_LEN_THRESHOLD}). See {ALERT}\n'
            )
        except Exception:
            pass
    else:
        # Clean up alert file if queue dropped below threshold
        try:
            if os.path.exists(ALERT):
                os.remove(ALERT)
        except Exception:
            pass

    return {
        'scanned': len(obs),
        'candidates': candidates,
        'skipped_already_in_memory': skipped_already_in_memory,
        'newly_promoted': len(newly_promoted),
        'queue_len_after': queue_len,
    }


def _write_pending_alert(queue):
    try:
        os.makedirs(os.path.dirname(ALERT), exist_ok=True)
        with open(ALERT, 'w', encoding='utf-8') as f:
            f.write('# Pending memory alert\n\n')
            f.write(f'**Generated**: {_now().isoformat()}\n')
            f.write(f'**Queue length**: {len(queue)}\n')
            f.write(f'**Threshold**: {ALERT_LEN_THRESHOLD}\n\n')
            f.write('Pending memory entries detected by `tellonce` skill but not '
                    'yet finalized to a memory file.  These survived prior session(s) and '
                    'must be processed before the next major work block.\n\n')
            f.write('## Unresolved entries\n\n')
            for i, e in enumerate(queue, 1):
                # Queue entries are already redacted at promote
                # time, but defense-in-depth — re-redact every string we render
                # (re-redaction also covers older entries).
                sig = e.get('detected_signal') or {}
                f.write(f'### {i}. proposed `{e.get("proposed_atomic_id") or "<unknown>"}`\n')
                f.write(f'- **promoted_at**: {e.get("promoted_at")}\n')
                f.write(f'- **source_obs_entry_id**: `{e.get("source_obs_entry_id")}`\n')
                f.write(f'- **signal_type**: {sig.get("signal_type")}\n')
                f.write(f'- **domain**: {sig.get("domain")}\n')
                f.write(f'- **content**: {redaction.redact(sig.get("content") or "")}\n')
                ume = e.get('user_message_excerpt')
                if ume:
                    f.write(f'- **user said (≤200)**: {redaction.redact(ume)}\n')
                f.write('\n')
            f.write('## Action\n\n')
            f.write('1. Review each entry above.\n')
            f.write('2. For each → apply the conflict-resolution algorithm '
                    '(NOOP / UPDATE / SUPERSEDE / NEW).\n')
            f.write(f'3. Run `python3 {_LIB_DIR}/'
                    'pending_queue_manager.py prune` to drop resolved entries from queue.\n')
        # Alert MD echoes user content; restrict.
        path_config.chmod_or_warn(ALERT, 0o600)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# inject
# ---------------------------------------------------------------------------

def inject_for_userprompt():
    """Return formatted text to inject as additionalContext at session start.
    Empty string if queue is empty.
    Caps display at INJECT_TOPN_CAP newest entries (overflow shown
    as count) so a long-stalled queue doesn't bloat every UserPromptSubmit context."""
    queue = _read_jsonl(QUEUE)
    if not queue:
        return ''
    total = len(queue)
    # Newest first (promoted_at lexicographic ISO-8601 sorts correctly)
    queue_sorted = sorted(queue, key=lambda e: e.get('promoted_at') or '', reverse=True)
    show = queue_sorted[:INJECT_TOPN_CAP]
    overflow = total - len(show)
    lines = []
    lines.append(f'⚠ {total} pending memory entr'
                 f'{"y" if total == 1 else "ies"} unfinalized from prior session(s):')
    if overflow > 0:
        lines.append(f'   (showing {len(show)} newest; {overflow} more not shown — see PENDING_ALERT.md)')
    for i, e in enumerate(show, 1):
        sig = e.get('detected_signal') or {}
        aid = e.get('proposed_atomic_id') or '<unknown>'
        content = (sig.get('content') or '').replace('\n', ' ')[:200]
        domain = sig.get('domain') or '?'
        promoted = (e.get('promoted_at') or '')[:19]
        lines.append(f'{i}. **[{aid}]** ({domain}) — {content}')
        lines.append(f'   promoted_at={promoted}, '
                     f'source_obs_entry_id={e.get("source_obs_entry_id")}')
    lines.append('')
    lines.append('Action required THIS session before substantive work: review each, '
                 'apply NOOP / UPDATE / SUPERSEDE / NEW, then run '
                 f'`python3 {_LIB_DIR}/'
                 'pending_queue_manager.py prune`. For entries whose '
                 '`proposed_atomic_id` is `<unknown>` or non-canonical (cannot be '
                 'auto-pruned), use `prune --force <queue_entry_id>` after deciding '
                 'NOOP/discard.')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------

def prune_resolved(force_ids=None):
    """Drop queue entries whose proposed_atomic_id now exists in memory dir.
    If `force_ids` is given (a set/list of queue_entry_id values),
    those entries are dropped unconditionally regardless of memory presence —
    use for entries with `<unknown>` or non-canonical proposed_atomic_id that the
    user has decided to NOOP/discard.

    Take queue lock so concurrent promote can't append
    after our _read_jsonl(QUEUE) and lose entries when we rewrite."""
    with _queue_lock():
        return _prune_locked(force_ids)


def _prune_locked(force_ids=None):
    queue = _read_jsonl(QUEUE)
    if not queue:
        return {'before': 0, 'after': 0, 'pruned': 0, 'forced': 0}
    have_ids = _atomic_ids_in_memory()
    force_set = set(force_ids or [])
    keep = []
    pruned_ids = []
    forced_ids = []
    for e in queue:
        if e.get('queue_entry_id') in force_set:
            forced_ids.append(e.get('queue_entry_id'))
            continue
        aid = e.get('proposed_atomic_id')
        if aid and aid in have_ids:
            pruned_ids.append(aid)
            continue
        keep.append(e)
    if len(keep) != len(queue):
        try:
            _atomic_replace_queue(keep)
        except Exception as ex:
            _log_error('prune_resolved.write', ex)
    # Also clean up alert file if dropped below threshold
    if len(keep) < ALERT_LEN_THRESHOLD:
        try:
            if os.path.exists(ALERT):
                os.remove(ALERT)
        except Exception as e:
            _log_error('prune_resolved.remove_alert', e)
    return {'before': len(queue), 'after': len(keep), 'pruned': len(pruned_ids),
            'pruned_ids': pruned_ids, 'forced': len(forced_ids), 'forced_ids': forced_ids}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _is_tty_stderr():
    """Robustly check if stderr is a tty. The previous
    `os.isatty(... else -1)` form raised under hooks where fileno() throws."""
    try:
        return os.isatty(sys.stderr.fileno())
    except Exception:
        return False


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'promote'
    # Child-session guard: a nested `copilot -p` (e.g. shadow judge) must not
    # promote/mutate the shared pending queue. Only the auto 'promote' Stop-hook
    # path is guarded; explicit interactive commands (inject/prune/status) still run.
    if cmd == 'promote' and path_config.is_child_session():
        sys.exit(0)
    if cmd == 'promote':
        result = promote_from_observations()
        # Stop hook is silent; only emit JSON when invoked directly with --verbose or in tty
        if '--verbose' in sys.argv or _is_tty_stderr():
            try:
                print(json.dumps(result, ensure_ascii=False))
            except Exception:
                pass
    elif cmd == 'inject':
        text = inject_for_userprompt()
        if text:
            print(text)
    elif cmd == 'prune':
        # Support `prune --force <queue_entry_id> [<queue_entry_id> ...]`
        # for entries with non-canonical proposed_atomic_id (cannot auto-prune by memory match).
        force_ids = None
        if '--force' in sys.argv:
            i = sys.argv.index('--force')
            force_ids = [a for a in sys.argv[i + 1:] if not a.startswith('-')]
            if not force_ids:
                sys.stderr.write('--force requires at least one queue_entry_id argument\n')
                sys.exit(2)
        result = prune_resolved(force_ids=force_ids)
        try:
            print(json.dumps(result, ensure_ascii=False))
        except Exception:
            pass
    elif cmd == 'status':
        queue = _read_jsonl(QUEUE)
        # Note: alert_file_exists is best-effort indicator. Source of
        # truth is `queue_len`; ALERT.md is regenerated by next promote when queue ≥ threshold.
        print(json.dumps({
            'queue_path': QUEUE,
            'queue_len': len(queue),
            'alert_threshold': ALERT_LEN_THRESHOLD,
            'alert_active': len(queue) >= ALERT_LEN_THRESHOLD,
            'alert_file_exists': os.path.exists(ALERT),
            'unprune_able_count': sum(
                1 for e in queue
                if not (e.get('proposed_atomic_id') or '').strip()
                or not re.match(r'^[a-z]+-[a-z]+-\d+$', (e.get('proposed_atomic_id') or '').strip())
            ),
        }, ensure_ascii=False, indent=2))
    else:
        sys.stderr.write(f'Unknown command: {cmd}. Use promote|inject|prune|status\n')
        sys.exit(2)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Never block hooks
        sys.exit(0)
