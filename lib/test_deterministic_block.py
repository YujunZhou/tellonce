#!/usr/bin/env python3
"""Smoke tests for deterministic_block.py — block mechanism (main()) only.

Run: python3 test_deterministic_block.py
Expects: all 2 cases PASS (printed at end).

Mirrors test_b4_blocking.py pattern (subprocess test of main()).
"""
import json, os, sys, tempfile, subprocess, time
from datetime import datetime, timezone

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import deterministic_block as db


# ---------------------------- Subprocess tests of main() ----------------------------

def make_transcript_file(messages):
    """Write a fixture transcript file in JSONL with given messages."""
    fd, path = tempfile.mkstemp(suffix='.jsonl', prefix='det_test_transcript_')
    os.close(fd)
    with open(path, 'w') as f:
        for m in messages:
            f.write(json.dumps(m) + '\n')
    return path


def run_main(stdin_data, env_overrides=None):
    """Invoke deterministic_block.py main() as subprocess."""
    env = dict(os.environ)
    env.setdefault('PYTHONIOENCODING', 'utf-8')  # UTF-8 stdout so the block reason prints on any host
    env.setdefault('PT_ENFORCE', '1')  # tests exercise the block mechanism; enforcement is opt-in by default
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, os.path.join(LIB_DIR, 'deterministic_block.py')],
        input=json.dumps(stdin_data),
        capture_output=True, text=True, encoding='utf-8', env=env, timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_main_blocks_on_forced_violation():
    """main: with a (test-forced) violation → exit 2 + JSON block decision.
    The public release ships no built-in rules, so PT_TEST_FORCE_VIOLATION drives
    the block / exit-code path."""
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': 'help'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'some response'}]}},
    ])
    import time as _time
    unique_sid = f'test-forced-{int(_time.time() * 1000000)}'
    rc, stdout, stderr = run_main({'session_id': unique_sid, 'transcript_path': transcript},
                                  env_overrides={'PT_TEST_FORCE_VIOLATION': '1'})
    os.unlink(transcript)
    assert rc == 2, f'expected exit 2 (block), got {rc}; stderr={stderr[:300]}'
    try:
        decision = json.loads(stdout.strip())
    except Exception:
        raise AssertionError(f'expected JSON decision in stdout, got: {stdout!r}')
    assert decision.get('decision') == 'block', f'expected block, got {decision}'
    assert 'test-synthetic' in decision.get('reason', ''), 'expected test-synthetic in reason'
    return True


def test_main_no_block_disabled_env():
    """B5_DETERMINISTIC_DISABLED=1 → exit 0 even with a (forced) violation."""
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': 'help'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'some response'}]}},
    ])
    rc, stdout, stderr = run_main(
        {'session_id': f'test-disabled-{int(__import__("time").time() * 1000000)}', 'transcript_path': transcript},
        env_overrides={'B5_DETERMINISTIC_DISABLED': '1', 'PT_TEST_FORCE_VIOLATION': '1'},
    )
    os.unlink(transcript)
    assert rc == 0, f'expected exit 0 (disabled), got {rc}'
    return True


# ---------------------------- Test runner ----------------------------

def main():
    tests = [
        ('main blocks on forced violation (subprocess)', test_main_blocks_on_forced_violation),
        ('main no block when DETERMINISTIC_DISABLED env', test_main_no_block_disabled_env),
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
