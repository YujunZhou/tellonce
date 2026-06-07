#!/usr/bin/env python3
"""Smoke tests for deterministic_block.py — 3 hard-block rules + whitelist + bypass.

Run: python3 test_deterministic_block.py
Expects: all 14 cases PASS (printed at end).

Mirrors test_b4_blocking.py pattern (subprocess test of main() + unit tests of helpers).
"""
import json, os, sys, tempfile, subprocess, time
from datetime import datetime, timezone

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import deterministic_block as db


# ---------------------------- Helper-level tests ----------------------------

def test_chinese_ratio_majority_chinese():
    """中文为主 response → ratio > 0.7."""
    text = "你好这是一个完整的中文句子用于测试 hello"
    r = db.chinese_ratio(text)
    assert r >= 0.7, f'expected >= 0.7, got {r}'
    return True


def test_chinese_ratio_pure_english():
    """全英文 → ratio ≈ 0."""
    text = "hello world this is a test, all in english 800 chars long....." * 10
    r = db.chinese_ratio(text)
    assert r < 0.1, f'expected < 0.1, got {r}'
    return True


def test_has_inline_english_word_whitelist_proper_noun():
    """ChromaDB/Sonnet 4-6 等 proper noun 在 whitelist → no inline english word flagged."""
    text = "我用 ChromaDB 跑 Sonnet 4-6, 数据存在向量库里"
    flagged = db.has_inline_english_word(text)
    assert not flagged, f'expected False (whitelist), got True with flagged={flagged}'
    return True


def test_has_inline_english_word_real_violation():
    """中文混 stub/drift/merge 等普通英文词 → flagged."""
    text = "好的, 这是 stub 的 fix, 我会 merge 一下"
    flagged = db.has_inline_english_word(text)
    assert flagged, f'expected flagged, got False'
    # Should detect at least 'stub' or 'fix' or 'merge'
    return True


def test_has_inline_english_word_in_code_block_skipped():
    """代码块内英文不算 inline english word."""
    text = """好的, 这是修复:

```python
def stub_function():
    return None  # placeholder
```

应该用占位代码"""
    flagged = db.has_inline_english_word(text)
    assert not flagged, f'expected False (code block), got flagged={flagged}'
    return True


def test_has_inline_english_word_cited_atomic_id_skipped():
    """atomic_id reference 例 lang-pref-001 不算 inline english."""
    text = "请按 `lang-pref-001` 改, 这个规则要求中文回复"
    flagged = db.has_inline_english_word(text)
    assert not flagged, f'expected False (atomic_id), got flagged={flagged}'
    return True


def test_has_active_code_block_with_tmp_path_in_code():
    """active bash 代码块 cd /tmp/ → True."""
    text = """这是修复:

```bash
cd /tmp/skill_library
ls *.md
```

完成"""
    flagged = db.has_active_code_block_with_tmp_path(text)
    assert flagged, f'expected True, got False'
    return True


def test_has_active_code_block_with_tmp_path_prose_skipped():
    """prose 提到 /tmp/ 不算 active write."""
    text = "上次 /tmp/skill_library wipe 了, 损失惨重. 应该改用 state/runtime/."
    flagged = db.has_active_code_block_with_tmp_path(text)
    assert not flagged, f'expected False (prose mention), got True'
    return True


def test_has_active_code_block_with_tmp_path_comment_skipped():
    """代码块内 comment 提 /tmp/ 不算 active write."""
    text = """```python
# old default was /tmp/foo, now state/runtime/foo
PATH = '/var/state/runtime/foo'
```"""
    flagged = db.has_active_code_block_with_tmp_path(text)
    assert not flagged, f'expected False (comment line), got True'
    return True


def test_last_user_prompt_explicit_english_request():
    """user 上 prompt 说 'reply in english' → True."""
    transcript_lines = [
        json.dumps({'type': 'user', 'message': {'content': '请用中文回复'}}),
        json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '好的'}]}}),
        json.dumps({'type': 'user', 'message': {'content': 'now please reply in english'}}),
    ]
    explicit = db.last_user_prompt_explicit_english_request(transcript_lines)
    assert explicit, f'expected True (explicit english request), got False'
    return True


def test_last_user_prompt_paper_context_bypass():
    """user 上 prompt 含 'paper' / 'abstract' / 'rebuttal' 等学术写作 keyword → bypass."""
    transcript_lines = [
        json.dumps({'type': 'user', 'message': {'content': 'help draft the abstract for the paper'}}),
    ]
    explicit = db.last_user_prompt_explicit_english_request(transcript_lines)
    assert explicit, f'expected True (paper context bypass), got False'
    return True


def test_last_user_prompt_no_english_request():
    """user 上 prompt 中文且无 english/paper 信号 → False."""
    transcript_lines = [
        json.dumps({'type': 'user', 'message': {'content': '帮我 debug 一下这个函数'}}),
    ]
    explicit = db.last_user_prompt_explicit_english_request(transcript_lines)
    assert not explicit, f'expected False, got True'
    return True


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
        ('chinese_ratio majority chinese', test_chinese_ratio_majority_chinese),
        ('chinese_ratio pure english', test_chinese_ratio_pure_english),
        ('has_inline_english_word whitelist (ChromaDB)', test_has_inline_english_word_whitelist_proper_noun),
        ('has_inline_english_word real violation (stub/merge)', test_has_inline_english_word_real_violation),
        ('has_inline_english_word in code block skipped', test_has_inline_english_word_in_code_block_skipped),
        ('has_inline_english_word cited atomic_id skipped', test_has_inline_english_word_cited_atomic_id_skipped),
        ('has_active_code_block_with_tmp_path in code', test_has_active_code_block_with_tmp_path_in_code),
        ('has_active_code_block_with_tmp_path prose skipped', test_has_active_code_block_with_tmp_path_prose_skipped),
        ('has_active_code_block_with_tmp_path comment skipped', test_has_active_code_block_with_tmp_path_comment_skipped),
        ('last_user_prompt explicit english', test_last_user_prompt_explicit_english_request),
        ('last_user_prompt paper context bypass', test_last_user_prompt_paper_context_bypass),
        ('last_user_prompt no english request', test_last_user_prompt_no_english_request),
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
