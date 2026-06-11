"""Smoke tests for the tellonce Copilot variant.

Covers the release-critical paths that were hand-verified during the audit:
cross-runtime transcript parsing, observe-vs-enforce gating + exit-code
contract, the pt_mode switch (atomic + BOM-tolerant), config boolean parsing,
and the child-session guard. These are intentionally hermetic — they never
touch the real ~/.tellonce.config.json or any live state.

Run:  cd copilot/lib && pytest -q
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import uuid

import pytest

LIB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB)

import path_config  # noqa: E402
import transcript_adapter as ta  # noqa: E402
import deterministic_block as db  # noqa: E402


# ---------------------------------------------------------------- helpers
def _write_transcript(events):
    fd, p = tempfile.mkstemp(suffix='.jsonl')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    return p


LONG_EN = (
    'Hello, here is a deliberately long English-only answer that comfortably '
    'exceeds the two hundred character threshold so that the lang-pref-001 rule '
    'fires; it contains no Chinese characters at all and is just padding text.'
)


# ---------------------------------------------------------------- transcript_adapter
@pytest.mark.parametrize('schema', ['claude', 'copilot'])
def test_adapter_extracts_both_schemas(schema):
    if schema == 'claude':
        ev = [{'type': 'user', 'message': {'content': 'hi please help'}},
              {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': LONG_EN}]}}]
        stdin = {'transcript_path': _write_transcript(ev), 'session_id': 's'}
    else:
        ev = [{'type': 'user.message', 'data': {'content': 'hi please help'}},
              {'type': 'assistant.message', 'data': {'content': LONG_EN, 'toolRequests': []}}]
        stdin = {'transcriptPath': _write_transcript(ev), 'sessionId': 's'}
    resp, last_user, tools, _ = ta.read_transcript(stdin)
    assert resp == LONG_EN
    assert last_user == 'hi please help'
    assert ta.get_session_id(stdin) == 's'
    os.remove(stdin.get('transcript_path') or stdin['transcriptPath'])


def test_adapter_tail_caps_huge_transcript():
    ev = [{'type': 'user.message', 'data': {'content': 'noise %d' % i}} for i in range(5000)]
    ev.append({'type': 'user.message', 'data': {'content': 'final question'}})
    ev.append({'type': 'assistant.message', 'data': {'content': LONG_EN}})
    p = _write_transcript(ev)
    resp, last_user, _, raw = ta.read_transcript({'transcriptPath': p})
    assert resp == LONG_EN
    assert last_user == 'final question'
    assert len(raw) <= 2000  # tail-read cap, not the full ~7000 lines
    os.remove(p)


def test_no_builtin_rules_ship_but_mechanism_works(monkeypatch):
    # Public release ships NO built-in deterministic rules (no personal prefs).
    ev = [{'type': 'user.message', 'data': {'content': 'write a log'}},
          {'type': 'assistant.message', 'data': {'content': 'ok', 'toolRequests': [{'command': 'echo hi >> /tmp/x.log'}]}}]
    resp, last_user, tools, _ = ta.read_transcript({'transcriptPath': _write_transcript(ev)})
    monkeypatch.delenv('PT_TEST_FORCE_VIOLATION', raising=False)
    assert db.evaluate_rules(resp, last_user=last_user, tool_commands=tools) == []
    # The test hook still exercises the violation path.
    monkeypatch.setenv('PT_TEST_FORCE_VIOLATION', '1')
    vio = db.evaluate_rules(resp, last_user=last_user, tool_commands=tools)
    assert [v['rule_id'] for v in vio] == ['test-synthetic']


# ---------------------------------------------------------------- config bool parsing
def test_bool_setting_accepts_bool_str_int(monkeypatch, tmp_path):
    cfg = tmp_path / 'c.json'
    cfg.write_text(json.dumps({'enforce': 'true', 'shadow': 1}), encoding='utf-8')
    monkeypatch.setattr(path_config, 'CONFIG_PATH', str(cfg))
    monkeypatch.delenv('PT_ENFORCE', raising=False)
    monkeypatch.delenv('PT_SHADOW', raising=False)
    path_config._clear_cache()
    assert path_config.enforcement_enabled() is True
    assert path_config.shadow_enabled() is True
    monkeypatch.setenv('PT_ENFORCE', '0')  # env overrides config
    assert path_config.enforcement_enabled() is False


def test_bom_config_is_readable(monkeypatch, tmp_path):
    cfg = tmp_path / 'c.json'
    cfg.write_text('\ufeff' + json.dumps({'enforce': True}), encoding='utf-8')  # BOM prefix
    monkeypatch.setattr(path_config, 'CONFIG_PATH', str(cfg))
    monkeypatch.delenv('PT_ENFORCE', raising=False)
    path_config._clear_cache()
    assert path_config.enforcement_enabled() is True


def test_stop_block_exit_code(monkeypatch):
    monkeypatch.delenv('PT_STOP_BLOCK_EXIT', raising=False)
    assert path_config.stop_block_exit_code() == 0
    monkeypatch.setenv('PT_STOP_BLOCK_EXIT', '2')
    assert path_config.stop_block_exit_code() == 2
    monkeypatch.setenv('PT_STOP_BLOCK_EXIT', 'garbage')
    assert path_config.stop_block_exit_code() == 0


# ---------------------------------------------------------------- pt_mode switch
def test_pt_mode_roundtrip_preserves_keys_and_strips_bom(monkeypatch, tmp_path):
    import pt_mode
    cfg = tmp_path / 'c.json'
    cfg.write_text('\ufeff' + json.dumps({'retrieve_cli': 'copilot'}), encoding='utf-8')
    monkeypatch.setattr(pt_mode, 'CONFIG_PATH', str(cfg))
    monkeypatch.setattr(path_config, 'CONFIG_PATH', str(cfg))
    pt_mode.main(['enforce'])
    data = json.loads(cfg.read_text(encoding='utf-8-sig'))
    assert data['enforce'] is True and data['shadow'] is False
    assert data['retrieve_cli'] == 'copilot'  # preserved
    assert cfg.read_bytes()[:3] != b'\xef\xbb\xbf'  # BOM stripped on save
    pt_mode.main(['observe'])
    data = json.loads(cfg.read_text(encoding='utf-8-sig'))
    assert data['enforce'] is False


# ---------------------------------------------------------------- gating via subprocess
def _run_block(enforce, child=False, exit_env=None, transcript=None):
    env = os.environ.copy()
    for k in ('PT_ENFORCE', 'PT_SHADOW', 'PT_CHILD_SESSION', 'PT_STOP_BLOCK_EXIT', 'PT_TEST_FORCE_VIOLATION', 'PYTHONIOENCODING'):
        env.pop(k, None)
    # No built-in rules ship, so force a synthetic violation to exercise the
    # block / exit-code mechanism.
    env['PT_TEST_FORCE_VIOLATION'] = '1'
    if enforce:
        env['PT_ENFORCE'] = '1'
    if child:
        env['PT_CHILD_SESSION'] = '1'
    if exit_env:
        env['PT_STOP_BLOCK_EXIT'] = exit_env
    stdin = json.dumps({'sessionId': 's-' + uuid.uuid4().hex[:8], 'transcriptPath': transcript})
    p = subprocess.run([sys.executable, 'deterministic_block.py'],
                       input=stdin.encode('utf-8'), capture_output=True, env=env, cwd=LIB)
    out = p.stdout.decode('utf-8', 'replace')
    blocked = bool(out.strip()) and json.loads(out).get('decision') == 'block'
    return p.returncode, blocked


@pytest.fixture
def tmp_violation():
    # A transcript with a non-empty assistant response (content is irrelevant now;
    # the block is driven by PT_TEST_FORCE_VIOLATION in _run_block).
    ev = [{'type': 'user.message', 'data': {'content': 'do a thing'}},
          {'type': 'assistant.message', 'data': {'content': 'ok done'}}]
    p = _write_transcript(ev)
    yield p
    try:
        os.remove(p)
    except OSError:
        pass


def test_observe_never_blocks(tmp_violation):
    # Env-forced observe (PT_ENFORCE=0) must never block, even when a violation
    # is forced — observe gates out before rules are even evaluated.
    env = os.environ.copy()
    env['PT_ENFORCE'] = '0'
    env['PT_TEST_FORCE_VIOLATION'] = '1'
    env.pop('PYTHONIOENCODING', None)
    stdin = json.dumps({'sessionId': 's-' + uuid.uuid4().hex[:8], 'transcriptPath': tmp_violation})
    p = subprocess.run([sys.executable, 'deterministic_block.py'],
                       input=stdin.encode('utf-8'), capture_output=True, env=env, cwd=LIB)
    assert p.returncode == 0
    assert b'decision' not in p.stdout


def test_enforce_blocks_with_exit0_default(tmp_violation):
    rc, blocked = _run_block(enforce=True, transcript=tmp_violation)
    assert blocked and rc == 0  # PORT_DESIGN: stdout JSON + exit 0


def test_enforce_exit2_when_overridden(tmp_violation):
    rc, blocked = _run_block(enforce=True, exit_env='2', transcript=tmp_violation)
    assert blocked and rc == 2


def test_child_session_guard_suppresses_block(tmp_violation):
    rc, blocked = _run_block(enforce=True, child=True, transcript=tmp_violation)
    assert rc == 0 and not blocked  # child session must not block


# ---------------------------------------------------------------- dashboard
def test_dashboard_builds_without_crashing():
    import dashboard
    out = dashboard.build_dashboard()
    assert isinstance(out, str) and out
    # structural labels are always present regardless of install state
    for label in ('mode:', 'registered:', 'rules:', 'observations:'):
        assert label in out
    assert dashboard.main() == 0

