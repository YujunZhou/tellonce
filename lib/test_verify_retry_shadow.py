#!/usr/bin/env python3
"""Smoke tests for verify_retry_shadow.py + shadow_alert_inject.py.

Run: python3 test_verify_retry_shadow.py
Expects all tests PASS.

Uses B5_TEST_MOCK_VERDICT env to inject canned judge verdicts so no real LLM call.
"""
import json
import os
import sys
import tempfile
import subprocess
import time
from datetime import datetime, timezone, timedelta

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import verify_retry_shadow as shadow
import shadow_alert_inject as inject


# ----------------------- Helper test fixtures -----------------------

def isolated_state_dir():
    """Create a temp dir for shadow log + alert + cost, return path."""
    return tempfile.mkdtemp(prefix='b5_shadow_test_')


def make_transcript_file(messages):
    fd, path = tempfile.mkstemp(suffix='.jsonl', prefix='shadow_test_transcript_')
    os.close(fd)
    with open(path, 'w') as f:
        for m in messages:
            f.write(json.dumps(m) + '\n')
    return path


def run_shadow_main(stdin_data, env_overrides=None):
    """Invoke verify_retry_shadow.py main as subprocess."""
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        ['python3', os.path.join(LIB_DIR, 'verify_retry_shadow.py')],
        input=json.dumps(stdin_data),
        capture_output=True, text=True, env=env, timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_inject_main(stdin_data, env_overrides=None):
    """Invoke shadow_alert_inject.py main as subprocess."""
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        ['python3', os.path.join(LIB_DIR, 'shadow_alert_inject.py')],
        input=json.dumps(stdin_data),
        capture_output=True, text=True, env=env, timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ----------------------- Tests -----------------------

def test_shadow_disabled_env():
    """B5_SHADOW_DISABLED=1 → exit 0, status='disabled'."""
    state = isolated_state_dir()
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': 'help'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'response 100 chars '*5}]}},
    ])
    rc, stdout, stderr = run_shadow_main(
        {'session_id': 'test-disabled', 'transcript_path': transcript},
        env_overrides={'B5_SHADOW_DISABLED': '1', 'B5_STATE_DIR': state},
    )
    os.unlink(transcript)
    assert rc == 0, f'expected exit 0, got {rc}; stderr={stderr[:200]}'
    return True


def test_shadow_no_credit_default():
    """SDK path with ANTHROPIC_CREDIT_OK=0 → exit 0, no_credit."""
    state = isolated_state_dir()
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': 'help'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'response 100 chars '*5}]}},
    ])
    rc, stdout, stderr = run_shadow_main(
        {'session_id': 'test-no-credit', 'transcript_path': transcript},
        env_overrides={
            'B5_USE_SDK': '1',
            'ANTHROPIC_CREDIT_OK': '0',
            'B5_TEST_MOCK_VERDICT': '',
            'B5_STATE_DIR': state,
        },
    )
    os.unlink(transcript)
    assert rc == 0, f'expected exit 0, got {rc}'
    return True


def test_shadow_short_response_skip():
    """Short response (<50 chars) → skip judge, log skip_short."""
    state = isolated_state_dir()
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': 'help'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '好'}]}},
    ])
    rc, stdout, stderr = run_shadow_main(
        {'session_id': 'test-short', 'transcript_path': transcript},
        env_overrides={'ANTHROPIC_CREDIT_OK': '1', 'B5_STATE_DIR': state},
    )
    os.unlink(transcript)
    assert rc == 0, f'expected exit 0, got {rc}'
    return True


def test_shadow_violation_with_mock():
    """Mock judge returns violation → exit 0, alert written."""
    # Use isolated state
    state = isolated_state_dir()
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': '帮我修复一下'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '好的我来修复这个 stub 函数的问题,我们需要把它 merge 进主分支然后处理一下相关的依赖关系并通知所有团队成员之后再做进一步的代码 review 和测试'}]}},
    ])
    mock = json.dumps([
        {'rule_id': 'lang-pit-130', 'applicable': True, 'compliant': False,
         'judge_confidence': 0.9, 'evidence': '中文混 stub/merge', 'feedback': '改占位代码/合并'},
    ])
    # Override SHADOW_LOG/ALERT path via monkeypatch with env (not built-in for this script).
    # Workaround: we'll directly call evaluate() in unit mode rather than subprocess.
    # Module-level env vars are evaluated at import; patch attribute directly
    shadow.B5_TEST_MOCK_VERDICT = mock  # _judge_call reads this at call time
    orig_credit = shadow.ANTHROPIC_CREDIT_OK
    shadow.ANTHROPIC_CREDIT_OK = True
    orig_log = shadow.SHADOW_LOG
    orig_alert = shadow.SHADOW_ALERT_MD
    orig_cost = shadow.COST_LOG_DIR
    orig_compliance = shadow.COMPLIANCE_LOG  # I1 isolation: prevent real production logs from interfering with _just_blocked_by_deterministic
    shadow.SHADOW_LOG = os.path.join(state, 'b5_shadow_log.jsonl')
    shadow.SHADOW_ALERT_MD = os.path.join(state, 'B5_SHADOW_ALERT.md')
    shadow.COST_LOG_DIR = os.path.join(state, 'cost')
    shadow.COMPLIANCE_LOG = os.path.join(state, 'compliance_log.jsonl')
    try:
        status, log_entry = shadow.evaluate({'session_id': 'test-violation', 'transcript_path': transcript})
    finally:
        shadow.SHADOW_LOG = orig_log
        shadow.SHADOW_ALERT_MD = orig_alert
        shadow.COST_LOG_DIR = orig_cost
        shadow.COMPLIANCE_LOG = orig_compliance
        shadow.ANTHROPIC_CREDIT_OK = orig_credit
        shadow.B5_TEST_MOCK_VERDICT = ''
    os.unlink(transcript)

    assert status == 'violation', f'expected violation, got {status}'
    assert log_entry['b5_check']['shadow_violation_count'] == 1, \
        f'expected 1 violation, got {log_entry["b5_check"]}'
    assert log_entry['b5_check']['shadow_alerted_count'] == 1, \
        f'expected 1 alerted, got {log_entry["b5_check"]}'
    return True


def test_shadow_low_confidence_no_alert():
    """Mock judge returns violation but confidence < 0.85 → log only, no alert."""
    state = isolated_state_dir()
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': '帮我'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '好的我来修复这个 stub 函数的问题,我们需要把它 merge 进主分支处理一下相关的依赖关系然后再做最终的代码 review 和测试以及部署到生产环境'}]}},
    ])
    mock = json.dumps([
        {'rule_id': 'lang-pit-130', 'applicable': True, 'compliant': False,
         'judge_confidence': 0.5, 'evidence': 'maybe', 'feedback': '?'},
    ])
    shadow.B5_TEST_MOCK_VERDICT = mock
    orig_credit = shadow.ANTHROPIC_CREDIT_OK
    shadow.ANTHROPIC_CREDIT_OK = True
    orig_log = shadow.SHADOW_LOG
    orig_alert = shadow.SHADOW_ALERT_MD
    orig_cost = shadow.COST_LOG_DIR
    orig_compliance = shadow.COMPLIANCE_LOG  # I1 isolation: prevent real production logs from interfering with _just_blocked_by_deterministic
    shadow.SHADOW_LOG = os.path.join(state, 'b5_shadow_log.jsonl')
    shadow.SHADOW_ALERT_MD = os.path.join(state, 'B5_SHADOW_ALERT.md')
    shadow.COST_LOG_DIR = os.path.join(state, 'cost')
    shadow.COMPLIANCE_LOG = os.path.join(state, 'compliance_log.jsonl')
    try:
        status, log_entry = shadow.evaluate({'session_id': 'test-low-conf', 'transcript_path': transcript})
    finally:
        shadow.SHADOW_LOG = orig_log
        shadow.SHADOW_ALERT_MD = orig_alert
        shadow.COST_LOG_DIR = orig_cost
        shadow.COMPLIANCE_LOG = orig_compliance
        shadow.ANTHROPIC_CREDIT_OK = orig_credit
        shadow.B5_TEST_MOCK_VERDICT = ''
    os.unlink(transcript)

    assert status == 'violation', f'expected violation status, got {status}'
    assert log_entry['b5_check']['shadow_alerted_count'] == 0, \
        f'expected 0 alerted (low conf), got {log_entry["b5_check"]}'
    return True


def test_shadow_compliant_no_alert():
    """Mock judge returns all compliant → no alert."""
    state = isolated_state_dir()
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': '帮我'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '好的我来修复这个占位代码的问题,我们需要把它合并进主分支然后处理一下相关的依赖关系再通知所有相关团队成员之后再做最终的代码审查和部署到生产环境'}]}},
    ])
    mock = json.dumps([
        {'rule_id': 'lang-pit-130', 'applicable': True, 'compliant': True,
         'judge_confidence': 0.95, 'evidence': '', 'feedback': ''},
    ])
    shadow.B5_TEST_MOCK_VERDICT = mock
    orig_credit = shadow.ANTHROPIC_CREDIT_OK
    shadow.ANTHROPIC_CREDIT_OK = True
    orig_log = shadow.SHADOW_LOG
    orig_alert = shadow.SHADOW_ALERT_MD
    orig_cost = shadow.COST_LOG_DIR
    orig_compliance = shadow.COMPLIANCE_LOG  # I1 isolation: prevent real production logs from interfering with _just_blocked_by_deterministic
    shadow.SHADOW_LOG = os.path.join(state, 'b5_shadow_log.jsonl')
    shadow.SHADOW_ALERT_MD = os.path.join(state, 'B5_SHADOW_ALERT.md')
    shadow.COST_LOG_DIR = os.path.join(state, 'cost')
    shadow.COMPLIANCE_LOG = os.path.join(state, 'compliance_log.jsonl')
    try:
        status, log_entry = shadow.evaluate({'session_id': 'test-compliant', 'transcript_path': transcript})
    finally:
        shadow.SHADOW_LOG = orig_log
        shadow.SHADOW_ALERT_MD = orig_alert
        shadow.COST_LOG_DIR = orig_cost
        shadow.COMPLIANCE_LOG = orig_compliance
        shadow.ANTHROPIC_CREDIT_OK = orig_credit
        shadow.B5_TEST_MOCK_VERDICT = ''
    os.unlink(transcript)

    assert status == 'compliant', f'expected compliant, got {status}'
    assert log_entry['b5_check']['shadow_violation_count'] == 0
    return True


def test_shadow_lang_pref_mixed_chinese_suppressed():
    """lang-pref-001 judge false-positive on Chinese-majority reply is suppressed."""
    state = isolated_state_dir()
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': '现在进展怎么样了'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': (
            '目前实验已经完成，PROGRESS.md 已更新，summary_metrics.json 都存在，'
            'runtime_error 是 0。Codex 和 Claude 的 all-ClawArena 主结果都已经落盘，'
            '下一步是按 ID/OOD 分开报告 objective task pass 和 overlay violation。'
        )}]}},
    ])
    mock = json.dumps([
        {'rule_id': 'lang-pref-001', 'applicable': True, 'compliant': False,
         'judge_confidence': 0.95, 'evidence': '含英文术语', 'feedback': '改用中文回复'},
    ])
    shadow.B5_TEST_MOCK_VERDICT = mock
    orig_credit = shadow.ANTHROPIC_CREDIT_OK
    shadow.ANTHROPIC_CREDIT_OK = True
    orig_log = shadow.SHADOW_LOG
    orig_alert = shadow.SHADOW_ALERT_MD
    orig_cost = shadow.COST_LOG_DIR
    orig_compliance = shadow.COMPLIANCE_LOG
    shadow.SHADOW_LOG = os.path.join(state, 'b5_shadow_log.jsonl')
    shadow.SHADOW_ALERT_MD = os.path.join(state, 'B5_SHADOW_ALERT.md')
    shadow.COST_LOG_DIR = os.path.join(state, 'cost')
    shadow.COMPLIANCE_LOG = os.path.join(state, 'compliance_log.jsonl')
    try:
        status, log_entry = shadow.evaluate({'session_id': 'test-lang-pref-suppressed', 'transcript_path': transcript})
    finally:
        shadow.SHADOW_LOG = orig_log
        shadow.SHADOW_ALERT_MD = orig_alert
        shadow.COST_LOG_DIR = orig_cost
        shadow.COMPLIANCE_LOG = orig_compliance
        shadow.ANTHROPIC_CREDIT_OK = orig_credit
        shadow.B5_TEST_MOCK_VERDICT = ''
    os.unlink(transcript)

    assert status == 'compliant', f'expected compliant after suppression, got {status}'
    b5 = log_entry['b5_check']
    assert b5['shadow_violation_count'] == 0, f'expected 0 unsuppressed violations, got {b5}'
    assert b5['shadow_alerted_count'] == 0, f'expected 0 alerts, got {b5}'
    assert b5['shadow_suppressed_rule_ids'] == ['lang-pref-001'], f'expected suppressed lang-pref-001, got {b5}'
    return True


def test_shadow_lang_pref_mostly_english_still_alerts():
    """A genuinely English user-facing response still triggers lang-pref-001."""
    state = isolated_state_dir()
    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': '现在进展怎么样了'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': (
            'The experiments are complete and the progress file has been updated. '
            'All summaries are available and there are no runtime errors in the final records. '
            'The next step is to report ID and OOD metrics separately.'
        )}]}},
    ])
    mock = json.dumps([
        {'rule_id': 'lang-pref-001', 'applicable': True, 'compliant': False,
         'judge_confidence': 0.95, 'evidence': '全英文用户回复', 'feedback': '改用中文回复'},
    ])
    shadow.B5_TEST_MOCK_VERDICT = mock
    orig_credit = shadow.ANTHROPIC_CREDIT_OK
    shadow.ANTHROPIC_CREDIT_OK = True
    orig_log = shadow.SHADOW_LOG
    orig_alert = shadow.SHADOW_ALERT_MD
    orig_cost = shadow.COST_LOG_DIR
    orig_compliance = shadow.COMPLIANCE_LOG
    shadow.SHADOW_LOG = os.path.join(state, 'b5_shadow_log.jsonl')
    shadow.SHADOW_ALERT_MD = os.path.join(state, 'B5_SHADOW_ALERT.md')
    shadow.COST_LOG_DIR = os.path.join(state, 'cost')
    shadow.COMPLIANCE_LOG = os.path.join(state, 'compliance_log.jsonl')
    try:
        status, log_entry = shadow.evaluate({'session_id': 'test-lang-pref-alert', 'transcript_path': transcript})
    finally:
        shadow.SHADOW_LOG = orig_log
        shadow.SHADOW_ALERT_MD = orig_alert
        shadow.COST_LOG_DIR = orig_cost
        shadow.COMPLIANCE_LOG = orig_compliance
        shadow.ANTHROPIC_CREDIT_OK = orig_credit
        shadow.B5_TEST_MOCK_VERDICT = ''
    os.unlink(transcript)

    assert status == 'violation', f'expected violation, got {status}'
    b5 = log_entry['b5_check']
    assert b5['shadow_violation_count'] == 1, f'expected 1 violation, got {b5}'
    assert b5['shadow_alerted_count'] == 1, f'expected 1 alert, got {b5}'
    assert b5['shadow_suppressed_rule_ids'] == [], f'expected no suppression, got {b5}'
    return True


def test_shadow_cost_capped():
    """Pre-populate today's cost > cap → exit 0, status='cost_capped'."""
    state = isolated_state_dir()
    cost_dir = os.path.join(state, 'cost')
    os.makedirs(cost_dir, exist_ok=True)
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with open(os.path.join(cost_dir, f'{today}.json'), 'w') as f:
        json.dump({'date': today, 'total_usd': 1.0}, f)  # over $0.50 cap

    transcript = make_transcript_file([
        {'type': 'user', 'message': {'content': '帮我'}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '响应内容100字符长度长一点凑足'*5}]}},
    ])
    orig_credit = shadow.ANTHROPIC_CREDIT_OK
    shadow.ANTHROPIC_CREDIT_OK = True
    orig_cost = shadow.COST_LOG_DIR
    shadow.COST_LOG_DIR = cost_dir
    orig_cap = shadow.B5_DAILY_COST_CAP
    shadow.B5_DAILY_COST_CAP = 0.50
    orig_compliance = shadow.COMPLIANCE_LOG  # I1 isolation
    shadow.COMPLIANCE_LOG = os.path.join(state, 'compliance_log.jsonl')
    try:
        status, log_entry = shadow.evaluate({'session_id': 'test-cost-cap', 'transcript_path': transcript})
    finally:
        shadow.COST_LOG_DIR = orig_cost
        shadow.B5_DAILY_COST_CAP = orig_cap
        shadow.ANTHROPIC_CREDIT_OK = orig_credit
        shadow.COMPLIANCE_LOG = orig_compliance
    os.unlink(transcript)

    assert status == 'cost_capped', f'expected cost_capped, got {status}'
    return True


def test_inject_disabled_env():
    """B5_INJECT_DISABLED=1 → exit 0, no stdout."""
    state = isolated_state_dir()
    rc, stdout, stderr = run_inject_main(
        {'prompt': 'test prompt'},
        env_overrides={'B5_INJECT_DISABLED': '1', 'B5_STATE_DIR': state},
    )
    assert rc == 0, f'expected exit 0, got {rc}'
    assert stdout.strip() == '', f'expected empty stdout, got {stdout[:200]}'
    return True


def test_inject_empty_no_alert_file():
    """No alert file → exit 0, no stdout."""
    state = isolated_state_dir()
    rc, stdout, stderr = run_inject_main(
        {'prompt': 'test'},
        env_overrides={'B5_TTL_HOURS': '0.001', 'B5_STATE_DIR': state},
    )
    assert rc == 0, f'expected exit 0, got {rc}'
    return True


def test_inject_with_alert():
    """Inject_module test (in-process): manually populate shadow log, build context."""
    state = isolated_state_dir()
    log_path = os.path.join(state, 'b5_shadow_log.jsonl')
    now_iso = datetime.now(timezone.utc).isoformat()
    with open(log_path, 'w') as f:
        f.write(json.dumps({
            'timestamp': now_iso,
            'rule_id': 'lang-pit-130',
            'rule_desc': '中文回复禁混普通英文词',
            'judge_confidence': 0.9,
            'evidence': '中文混 stub/merge',
            'feedback': '改占位代码',
            'alerted': True,
        }) + '\n')
    orig = inject.SHADOW_LOG
    inject.SHADOW_LOG = log_path
    try:
        alerts = inject._read_recent_alerted_violations(hours=24)
        assert len(alerts) == 1, f'expected 1 alert, got {len(alerts)}'
        ctx = inject.build_inject_context(alerts)
        assert ctx is not None
        assert 'lang-pit-130' in ctx
        assert '中文回复禁混普通英文词' in ctx
    finally:
        inject.SHADOW_LOG = orig
    return True


def test_inject_dedupe_by_rule():
    """Multiple alerts same rule_id → inject only latest."""
    state = isolated_state_dir()
    log_path = os.path.join(state, 'b5_shadow_log.jsonl')
    now = datetime.now(timezone.utc)
    with open(log_path, 'w') as f:
        for i in range(5):
            f.write(json.dumps({
                'timestamp': (now - timedelta(minutes=i*10)).isoformat(),
                'rule_id': 'lang-pit-130',
                'rule_desc': 'test',
                'judge_confidence': 0.9,
                'evidence': f'evidence {i}',
                'feedback': f'feedback {i}',
                'alerted': True,
            }) + '\n')
    orig = inject.SHADOW_LOG
    inject.SHADOW_LOG = log_path
    try:
        alerts = inject._read_recent_alerted_violations(hours=24)
        assert len(alerts) == 1, f'expected 1 (dedupe), got {len(alerts)}'
        # latest should have evidence 0 (smallest minutes ago = newest)
        assert 'evidence 0' in alerts[0]['evidence'], f'expected newest, got {alerts[0]}'
    finally:
        inject.SHADOW_LOG = orig
    return True


def test_inject_ttl_skip_old():
    """Alerts older than TTL → not injected."""
    state = isolated_state_dir()
    log_path = os.path.join(state, 'b5_shadow_log.jsonl')
    now = datetime.now(timezone.utc)
    with open(log_path, 'w') as f:
        # 1 fresh + 1 old
        f.write(json.dumps({
            'timestamp': now.isoformat(),
            'rule_id': 'lang-pit-130',
            'rule_desc': 'fresh',
            'judge_confidence': 0.9,
            'alerted': True,
        }) + '\n')
        f.write(json.dumps({
            'timestamp': (now - timedelta(hours=48)).isoformat(),
            'rule_id': 'oth-pref-001',
            'rule_desc': 'old',
            'judge_confidence': 0.9,
            'alerted': True,
        }) + '\n')
    orig = inject.SHADOW_LOG
    inject.SHADOW_LOG = log_path
    try:
        alerts = inject._read_recent_alerted_violations(hours=24)
        assert len(alerts) == 1, f'expected 1 (old skipped), got {len(alerts)}'
        assert alerts[0]['rule_id'] == 'lang-pit-130', f'expected fresh, got {alerts[0]}'
    finally:
        inject.SHADOW_LOG = orig
    return True


def main():
    tests = [
        ('shadow disabled env', test_shadow_disabled_env),
        ('shadow no credit default', test_shadow_no_credit_default),
        ('shadow short response skip', test_shadow_short_response_skip),
        ('shadow violation with mock judge', test_shadow_violation_with_mock),
        ('shadow low confidence no alert', test_shadow_low_confidence_no_alert),
        ('shadow compliant no alert', test_shadow_compliant_no_alert),
        ('shadow lang-pref mixed Chinese suppressed', test_shadow_lang_pref_mixed_chinese_suppressed),
        ('shadow lang-pref mostly English still alerts', test_shadow_lang_pref_mostly_english_still_alerts),
        ('shadow cost capped', test_shadow_cost_capped),
        ('inject disabled env', test_inject_disabled_env),
        ('inject empty no alert file', test_inject_empty_no_alert_file),
        ('inject with alert', test_inject_with_alert),
        ('inject dedupe by rule', test_inject_dedupe_by_rule),
        ('inject TTL skip old', test_inject_ttl_skip_old),
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
            import traceback as tb
            tb.print_exc()
            failed.append(name)
    print(f'\n{passed}/{len(tests)} PASS, {len(failed)} FAIL')
    if failed:
        print(f'Failed: {failed}')
        sys.exit(1)


if __name__ == '__main__':
    main()
