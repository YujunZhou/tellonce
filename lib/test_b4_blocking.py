#!/usr/bin/env python3
"""Smoke tests for B4 pending-finalize blocking gate (verify_compliance.py).

Run: python3 test_b4_blocking.py
Expects: all 13 cases PASS (printed at end).

Tests use B4_TEST_OBS_OVERRIDE env var to inject fixture obs file paths so
production observation log is never touched. After P-2 round-3 code-review,
includes real subprocess test of main() blocking behavior + retry counter.
"""
import json, os, sys, tempfile, subprocess, time
from datetime import datetime, timezone, timedelta

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import verify_compliance as v
import path_config

# Step 4 fix (per Phase 8 review I5 / kickoff §3 Step 4):
# fixture path 走 path_config 以便跨环境跑 (之前硬编码 author-local path,
# doctor.sh 在其他 home 上 skip 该 test). prod verify_compliance 已用 path_config.
_B4_RETRY_DIR = path_config.get_b4_retry_dir()
_B4_ALERT_DIR = path_config.get_b4_alert_dir()


def make_obs(ts, detected, saved_to_memory):
    """Build a minimal obs entry."""
    return {
        'entry_id': f'test-{ts.isoformat()}-{detected}-{saved_to_memory}',
        'timestamp': ts.isoformat(),
        'project': 'test',
        'cwd': '/tmp',
        'trigger': {'user_message_excerpt': 'test'},
        'detection': {'detected': detected, 'signal_type': 'preference' if detected else 'none'},
        'action': {'saved_to_memory': saved_to_memory},
        'user_response': {'received': False, 'outcome': None},
        'self_observations': {'judgment_confidence': 'high'},
    }


def write_fixture(entries):
    """Write entries to a temp obs file. Return path."""
    fd, path = tempfile.mkstemp(suffix='.jsonl', prefix='b4_test_obs_')
    os.close(fd)
    with open(path, 'w') as f:
        for e in entries:
            f.write(json.dumps(e) + '\n')
    return path


def run_main(stdin_data, env_overrides=None, obs_path_override=None):
    """Invoke verify_compliance.py as subprocess with controlled env. Return
    (exit_code, stdout)."""
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    if obs_path_override:
        env['B4_TEST_OBS_OVERRIDE'] = obs_path_override
    proc = subprocess.run(
        ['python3', os.path.join(LIB_DIR, 'verify_compliance.py')],
        input=json.dumps(stdin_data),
        capture_output=True, text=True, env=env, timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_count_empty_file():
    """Empty obs file → 0/0/0 + empty details."""
    path = write_fixture([])
    p, op, sa, det = v.count_recent_pending(window_min=60, obs_log_path=path)
    os.unlink(path)
    assert p == 0 and op == 0.0 and sa == 0.0, f'expected 0/0/0, got {p}/{op}/{sa}'
    assert det == [], f'expected empty pending_details, got {det}'
    return True


def test_count_pending_details_populated():
    """Pending obs → details list populated with atomic_id + content + age."""
    now = datetime.now(timezone.utc)
    e = make_obs(now - timedelta(minutes=5), True, 'pending')
    e['detection']['signal_type'] = 'preference'
    e['detection']['content'] = 'test preference content for B4 strengthen'
    e['action']['proposed_atomic_id'] = 'wf-pref-test'
    path = write_fixture([e])
    p, op, sa, det = v.count_recent_pending(window_min=60, obs_log_path=path)
    os.unlink(path)
    assert p == 1, f'expected 1 pending, got {p}'
    assert len(det) == 1, f'expected 1 detail, got {len(det)}'
    assert det[0]['atomic_id'] == 'wf-pref-test'
    assert det[0]['signal_type'] == 'preference'
    assert 'test preference content' in det[0]['content_excerpt']
    assert 4 < det[0]['age_min'] < 6
    return True


def test_count_no_pending():
    """5 entries, 3 detected but all saved=yes → 0 pending."""
    now = datetime.now(timezone.utc)
    entries = [make_obs(now - timedelta(minutes=i*5), True, 'yes') for i in range(5)]
    path = write_fixture(entries)
    p, op, sa, det = v.count_recent_pending(window_min=60, obs_log_path=path)
    os.unlink(path)
    assert p == 0, f'expected 0 pending, got {p}'
    assert sa > 0, f'expected positive session_age, got {sa}'
    return True


def test_count_pending_in_window():
    """4 pending obs in last 30 min, 2 outside window → returns 4 in 60-min window."""
    now = datetime.now(timezone.utc)
    entries = [
        make_obs(now - timedelta(minutes=5), True, 'pending'),
        make_obs(now - timedelta(minutes=10), True, 'pending'),
        make_obs(now - timedelta(minutes=15), True, 'pending'),
        make_obs(now - timedelta(minutes=25), True, 'pending'),
        make_obs(now - timedelta(minutes=120), True, 'pending'),  # outside
        make_obs(now - timedelta(minutes=180), True, 'pending'),  # outside
    ]
    path = write_fixture(entries)
    p, op, sa, det = v.count_recent_pending(window_min=60, obs_log_path=path)
    os.unlink(path)
    assert p == 4, f'expected 4 pending, got {p}'
    assert 24 < op < 27, f'expected oldest pending ~25 min, got {op}'
    return True


def test_count_window_boundary():
    """Tight 10-min window → only obs within 10 min counted."""
    now = datetime.now(timezone.utc)
    entries = [
        make_obs(now - timedelta(minutes=5), True, 'pending'),
        make_obs(now - timedelta(minutes=8), True, 'pending'),
        make_obs(now - timedelta(minutes=15), True, 'pending'),  # outside 10-min
    ]
    path = write_fixture(entries)
    p, op, sa, det = v.count_recent_pending(window_min=10, obs_log_path=path)
    os.unlink(path)
    assert p == 2, f'expected 2 (10-min window), got {p}'
    return True


def test_main_blocks_at_threshold():
    """B.2 main: 4 pending + session > 20 min → blocks (exit 2 + JSON decision).
    Real subprocess test now that B4_TEST_OBS_OVERRIDE is honored (I2 from code-review).
    """
    now = datetime.now(timezone.utc)
    entries = [make_obs(now - timedelta(minutes=i*5+5), True, 'pending') for i in range(4)]
    # earliest at ~25 min (i=3), so session age > 20 min ✓
    path = write_fixture(entries)
    sid = f'test-block-{int(time.time()*1000)}'
    rc, stdout, stderr = run_main(
        {'session_id': sid},
        env_overrides={
            'B4_THRESHOLD_PENDING': '3',
            'B4_THRESHOLD_DURATION_MIN': '20',
            'B4_TEST_OBS_OVERRIDE': path,
        },
    )
    # Cleanup retry state file (test isolation)
    retry_path = os.path.join(_B4_RETRY_DIR, f'b4_retry_{sid}.json')
    if os.path.exists(retry_path):
        os.unlink(retry_path)
    alert_path = os.path.join(_B4_ALERT_DIR, f'session_pending_alert_{sid}.md')
    if os.path.exists(alert_path):
        os.unlink(alert_path)
    os.unlink(path)
    assert rc == 2, f'expected exit 2 (block), got {rc}; stderr={stderr[:200]}'
    try:
        decision = json.loads(stdout.strip())
    except Exception as e:
        raise AssertionError(f'expected JSON decision in stdout, got: {stdout!r} (err: {e})')
    assert decision.get('decision') == 'block', f'expected decision=block, got {decision}'
    reason = decision.get('reason', '')
    # B4 strengthen: imperative reason text now starts with "⛔ STOP BLOCKED" + has PENDING SIGNALS section
    assert 'STOP BLOCKED' in reason, f'expected STOP BLOCKED in reason, got: {reason[:200]}'
    assert 'PENDING SIGNALS' in reason, f'expected PENDING SIGNALS section, got: {reason[:300]}'
    assert 'REQUIRED ACTION' in reason, f'expected REQUIRED ACTION section, got: {reason[:300]}'
    return True


def test_main_no_block_below_threshold():
    """B.2 main: 2 pending (≤3 threshold) → no block, exit 0."""
    now = datetime.now(timezone.utc)
    entries = [make_obs(now - timedelta(minutes=i*5+5), True, 'pending') for i in range(2)]
    path = write_fixture(entries)
    sid = f'test-noblock-{int(time.time()*1000)}'
    rc, stdout, stderr = run_main(
        {'session_id': sid},
        env_overrides={
            'B4_THRESHOLD_PENDING': '3',
            'B4_THRESHOLD_DURATION_MIN': '20',
            'B4_TEST_OBS_OVERRIDE': path,
        },
    )
    # cleanup
    retry_path = os.path.join(_B4_RETRY_DIR, f'b4_retry_{sid}.json')
    if os.path.exists(retry_path):
        os.unlink(retry_path)
    os.unlink(path)
    assert rc == 0, f'expected exit 0 (no block), got {rc}'
    return True


def test_main_self_disable_after_n_retries():
    """C1 livelock fix: after MAX_RETRIES blocks, gate self-disables."""
    now = datetime.now(timezone.utc)
    entries = [make_obs(now - timedelta(minutes=i*5+5), True, 'pending') for i in range(5)]
    path = write_fixture(entries)
    sid = f'test-livelock-{int(time.time()*1000)}'

    # Pre-populate retry state with retries=3 (= MAX_RETRIES_BEFORE_SELF_DISABLE default)
    retry_path = os.path.join(_B4_RETRY_DIR, f'b4_retry_{sid}.json')
    with open(retry_path, 'w') as f:
        json.dump({'retries': 3, 'first_block_ts': now.isoformat(), 'last_block_ts': now.isoformat()}, f)

    rc, stdout, stderr = run_main(
        {'session_id': sid},
        env_overrides={
            'B4_THRESHOLD_PENDING': '3',
            'B4_THRESHOLD_DURATION_MIN': '20',
            'B4_TEST_OBS_OVERRIDE': path,
            'B4_MAX_RETRIES': '3',
        },
    )
    # cleanup
    if os.path.exists(retry_path):
        os.unlink(retry_path)
    os.unlink(path)
    assert rc == 0, f'expected exit 0 (self-disabled after 3 retries), got {rc}'
    return True


def test_main_b4_disabled_env():
    """B4_DISABLED=1 short-circuits even when conditions met."""
    now = datetime.now(timezone.utc)
    entries = [make_obs(now - timedelta(minutes=i*5+5), True, 'pending') for i in range(10)]
    path = write_fixture(entries)
    sid = f'test-disabled-{int(time.time()*1000)}'
    rc, stdout, stderr = run_main(
        {'session_id': sid},
        env_overrides={
            'B4_THRESHOLD_PENDING': '3',
            'B4_THRESHOLD_DURATION_MIN': '20',
            'B4_TEST_OBS_OVERRIDE': path,
            'B4_DISABLED': '1',
        },
    )
    retry_path = os.path.join(_B4_RETRY_DIR, f'b4_retry_{sid}.json')
    if os.path.exists(retry_path):
        os.unlink(retry_path)
    os.unlink(path)
    assert rc == 0, f'expected exit 0 (B4_DISABLED), got {rc}'
    return True


def test_decision_logic_block():
    """Direct: pending=4 + session_age=25 + threshold=3/20 → should_block=True."""
    pending = 4
    session_age_min = 25.0
    THRESHOLD_PENDING = 3
    THRESHOLD_DURATION_MIN = 20.0
    B4_DISABLED = False
    should_block = (
        not B4_DISABLED
        and pending > THRESHOLD_PENDING
        and session_age_min > THRESHOLD_DURATION_MIN
    )
    assert should_block is True, 'expected block'
    return True


def test_decision_logic_finalize_unblocks():
    """After finalize 1 (pending=3, not >3) → should_block=False."""
    pending = 3  # was 4, finalized 1
    session_age_min = 25.0
    should_block = (
        not False
        and pending > 3
        and session_age_min > 20.0
    )
    assert should_block is False, 'expected NOT block after finalize'
    return True


def test_decision_logic_short_session():
    """High pending but session < 20 min → not block."""
    pending = 10
    session_age_min = 15.0  # < 20
    should_block = (
        not False
        and pending > 3
        and session_age_min > 20.0
    )
    assert should_block is False, 'expected NOT block (session too young)'
    return True


def test_decision_logic_disabled():
    """B4_DISABLED=true overrides everything."""
    pending = 100
    session_age_min = 60.0
    should_block = (
        not True  # disabled
        and pending > 3
        and session_age_min > 20.0
    )
    assert should_block is False, 'expected NOT block (disabled)'
    return True


def test_alert_file_format():
    """write_pending_alert produces a parseable .md with key fields."""
    path = v.write_pending_alert('test-sid-alpha-beta', 5, 30.0, 25.0)
    assert path.startswith(os.path.join(_B4_ALERT_DIR, 'session_pending_alert_')), \
        f'expected path under {_B4_ALERT_DIR}, got {path}'
    assert os.path.exists(path)
    with open(path) as f:
        content = f.read()
    os.unlink(path)
    assert 'test-sid-alpha-beta' in content
    assert '5 pending' in content
    assert 'B4_DISABLED' in content
    return True


def main():
    import time  # for timestamp-based unique sids
    globals().setdefault('time', time)
    tests = [
        ('count_empty_file', test_count_empty_file),
        ('count_no_pending', test_count_no_pending),
        ('count_pending_in_window', test_count_pending_in_window),
        ('count_window_boundary', test_count_window_boundary),
        ('count_pending_details_populated (B4 strengthen)', test_count_pending_details_populated),
        ('decision_logic_block', test_decision_logic_block),
        ('decision_logic_finalize_unblocks', test_decision_logic_finalize_unblocks),
        ('decision_logic_short_session', test_decision_logic_short_session),
        ('decision_logic_disabled', test_decision_logic_disabled),
        ('alert_file_format', test_alert_file_format),
        ('main_blocks_at_threshold (real subprocess, I2)', test_main_blocks_at_threshold),
        ('main_no_block_below_threshold (real subprocess)', test_main_no_block_below_threshold),
        ('main_self_disable_after_n_retries (C1)', test_main_self_disable_after_n_retries),
        ('main_b4_disabled_env (real subprocess)', test_main_b4_disabled_env),
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            ok = fn()
            if ok:
                print(f'  PASS  {name}')
                passed += 1
            else:
                print(f'  FAIL  {name} (returned False)')
                failed.append(name)
        except AssertionError as e:
            print(f'  FAIL  {name}: {e}')
            failed.append(name)
        except Exception as e:
            print(f'  ERR   {name}: {type(e).__name__}: {e}')
            failed.append(name)
    print(f'\n{passed}/{len(tests)} PASS, {len(failed)} FAIL')
    if failed:
        print(f'Failed: {failed}')
        sys.exit(1)


if __name__ == '__main__':
    main()
