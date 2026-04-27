#!/usr/bin/env python3
"""Tests for verify_compliance.generate_auto_light_entry — Sprint v23 day-1 hook fallback.

Run: python3 test_auto_light_entry.py
Expects: 7/7 PASS (kickoff §Step 1 asked for 5+; we cover the 5 listed cases plus
2 extra schema/edge tests).

测试覆盖:
  1. 空文件写第一条 (empty obs_log 创建 + 单 entry)
  2. 正常写后追加 (existing entries 保留 + 新 entry 末尾)
  3. atomic 中断重试 (.tmp 残留 cleanup; 重跑不 corrupt)
  4. age 计算正确 (age_sec / threshold_sec 进 entry 完整)
  5. session_id propagate (顶层 session_id + entry_id 含 sid prefix)
  6. schema 满足 hook validation (detection.detected boolean, trigger excerpt
     non-empty, self_observations.uncertainty_notes non-empty — CHECK 2 关键)
  7. existing 没结尾 newline 时不破坏 (健壮性)
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import verify_compliance as vc


def _read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------- Tests ----------------------------

def test_empty_file_first_write():
    """空 obs_log 写第一条 entry, 文件创建 + 1 行 valid JSON."""
    with tempfile.TemporaryDirectory() as d:
        obs = os.path.join(d, 'sub', 'observations.jsonl')
        path = vc.generate_auto_light_entry(
            session_id='sid-empty-001',
            age_sec=2000,
            threshold_sec=1800,
            obs_log_path=obs,
        )
        assert path == os.path.abspath(obs), f'returned path mismatch: {path}'
        assert os.path.exists(obs), 'obs file not created'
        entries = _read_jsonl(obs)
        assert len(entries) == 1, f'expected 1 entry, got {len(entries)}'
        e = entries[0]
        assert e['auto_generated'] is True
        assert e['session_id'] == 'sid-empty-001'
        # 父目录 mkdir -p 也要 work (sub/ 不存在前)
        assert os.path.isdir(os.path.dirname(obs))
    print('  ✓ test_empty_file_first_write')


def test_append_to_existing():
    """已有 entry 时追加, 不丢老 entry."""
    with tempfile.TemporaryDirectory() as d:
        obs = os.path.join(d, 'observations.jsonl')
        # seed 2 老 entry
        with open(obs, 'w') as f:
            f.write(json.dumps({'entry_id': 'old-1', 'detection': {'detected': True}}) + '\n')
            f.write(json.dumps({'entry_id': 'old-2', 'detection': {'detected': False}}) + '\n')
        vc.generate_auto_light_entry(
            session_id='sid-append-002',
            age_sec=2000,
            threshold_sec=1800,
            obs_log_path=obs,
        )
        entries = _read_jsonl(obs)
        assert len(entries) == 3, f'expected 3 entries (2 old + 1 new), got {len(entries)}'
        assert entries[0]['entry_id'] == 'old-1'
        assert entries[1]['entry_id'] == 'old-2'
        assert entries[2]['auto_generated'] is True
        assert entries[2]['session_id'] == 'sid-append-002'
    print('  ✓ test_append_to_existing')


def test_tmp_residue_does_not_corrupt():
    """模拟前次 crash 留下 .tmp 残留, 新 call 不应 import 残留 + 不 corrupt obs."""
    with tempfile.TemporaryDirectory() as d:
        obs = os.path.join(d, 'observations.jsonl')
        with open(obs, 'w') as f:
            f.write(json.dumps({'entry_id': 'real-1'}) + '\n')
        # 残留 .tmp 含垃圾 (前次 process 中断)
        residue = obs + '.tmp.99999.99999999999999'
        with open(residue, 'w') as f:
            f.write('CORRUPT-RESIDUE-DO-NOT-MERGE\n')
        # 新 call
        vc.generate_auto_light_entry(
            session_id='sid-tmp-003',
            age_sec=1900,
            threshold_sec=1800,
            obs_log_path=obs,
        )
        entries = _read_jsonl(obs)
        # 老 entry 保留 + 新 entry 加, 残留不进 obs
        contents = open(obs).read()
        assert 'CORRUPT-RESIDUE-DO-NOT-MERGE' not in contents, \
            'residue tmp file leaked into obs_log'
        assert len(entries) == 2, f'expected 2 entries, got {len(entries)}'
        assert entries[0]['entry_id'] == 'real-1'
        assert entries[1]['auto_generated'] is True
        # 残留 tmp 文件可以仍存在 (function 不主动清同 dir 的 unrelated tmp); 仅测 obs 完整
    print('  ✓ test_tmp_residue_does_not_corrupt')


def test_age_and_threshold_propagate():
    """age_sec 和 threshold_sec 完整入 entry, int cast 正确."""
    with tempfile.TemporaryDirectory() as d:
        obs = os.path.join(d, 'observations.jsonl')
        vc.generate_auto_light_entry(
            session_id='sid-age-004',
            age_sec=2456.789,         # float input
            threshold_sec=1800,        # int input
            obs_log_path=obs,
        )
        entries = _read_jsonl(obs)
        e = entries[0]
        assert e['auto_age_sec'] == 2456, f'age_sec int cast wrong: {e["auto_age_sec"]}'
        assert e['auto_threshold_sec'] == 1800
        # uncertainty_notes 必须包含 age + threshold 数字 (hook validation 要 non-empty)
        notes = e['self_observations']['uncertainty_notes']
        assert '2456' in notes, f'age missing from notes: {notes}'
        assert '1800' in notes, f'threshold missing from notes: {notes}'
    print('  ✓ test_age_and_threshold_propagate')


def test_session_id_propagate_and_entry_id_format():
    """session_id 顶层字段正确 + entry_id slug 含 sid prefix (8 char)."""
    with tempfile.TemporaryDirectory() as d:
        obs = os.path.join(d, 'observations.jsonl')
        vc.generate_auto_light_entry(
            session_id='claude-session-abcdef0123456789',
            age_sec=2000,
            threshold_sec=1800,
            obs_log_path=obs,
        )
        entries = _read_jsonl(obs)
        e = entries[0]
        assert e['session_id'] == 'claude-session-abcdef0123456789'
        # entry_id 形如 "<iso>-auto-fallback-<sid_slug>"
        assert 'auto-fallback' in e['entry_id'], f'entry_id missing slug: {e["entry_id"]}'
        # sid_slug 是前 8 char (re.sub 不改 'claude-s'), entry_id 末尾应含
        assert e['entry_id'].endswith('-claude-s'), f'entry_id should end with sid prefix: {e["entry_id"]}'
        # unknown session_id 不 crash
        vc.generate_auto_light_entry(
            session_id=None,
            age_sec=2000, threshold_sec=1800,
            obs_log_path=obs,
        )
        entries = _read_jsonl(obs)
        assert entries[1]['session_id'] == 'unknown'
    print('  ✓ test_session_id_propagate_and_entry_id_format')


def test_schema_satisfies_hook_validation():
    """新 entry 必须过 check-observation-log.sh 的 4-check structured validation:
    detection.detected boolean / trigger.user_message_excerpt non-empty /
    self_observations.uncertainty_notes non-empty / detected=false 时 skip 严格 sub-check."""
    with tempfile.TemporaryDirectory() as d:
        obs = os.path.join(d, 'observations.jsonl')
        vc.generate_auto_light_entry(
            session_id='sid-schema-006',
            age_sec=2000, threshold_sec=1800,
            obs_log_path=obs,
        )
        e = _read_jsonl(obs)[0]
        # 1. detected ∈ {True, False}
        assert e['detection']['detected'] is False
        # 2. trigger.user_message_excerpt non-empty
        excerpt = e['trigger']['user_message_excerpt']
        assert excerpt and len(excerpt) > 0, 'user_message_excerpt empty'
        # 3. self_observations.uncertainty_notes non-empty
        notes = e['self_observations']['uncertainty_notes']
        assert notes and len(notes) > 0, 'uncertainty_notes empty'
        # 4. detected=false → skip signal_type/content/conf_text check (依赖 hook code)
        # 但 schema 规整应该 None
        assert e['detection']['signal_type'] is None
        # auto_* 标记 hook trace + 后续 analysis 用
        assert e['auto_generated'] is True
        assert e['auto_reason'] == 'hook_fallback_due_to_age_exceeded'
    print('  ✓ test_schema_satisfies_hook_validation')


def test_existing_no_trailing_newline():
    """老 obs_log 末尾没 newline (异常 corrupt 状态), 新 entry 应自动加 newline 修."""
    with tempfile.TemporaryDirectory() as d:
        obs = os.path.join(d, 'observations.jsonl')
        with open(obs, 'w') as f:
            f.write(json.dumps({'entry_id': 'no-newline'}))  # NO trailing \n
        vc.generate_auto_light_entry(
            session_id='sid-newline-007',
            age_sec=2000, threshold_sec=1800,
            obs_log_path=obs,
        )
        entries = _read_jsonl(obs)
        assert len(entries) == 2, f'expected 2 entries after newline-fix, got {len(entries)}'
        assert entries[0]['entry_id'] == 'no-newline'
        assert entries[1]['auto_generated'] is True
        # 文件内容应该 2 行, 都带 \n 结尾
        contents = open(obs).read()
        assert contents.endswith('\n'), 'file should end with newline after fix'
    print('  ✓ test_existing_no_trailing_newline')


# ---------------------------- Runner ----------------------------

def main():
    tests = [
        test_empty_file_first_write,
        test_append_to_existing,
        test_tmp_residue_does_not_corrupt,
        test_age_and_threshold_propagate,
        test_session_id_propagate_and_entry_id_format,
        test_schema_satisfies_hook_validation,
        test_existing_no_trailing_newline,
    ]
    print(f'Running {len(tests)} tests for generate_auto_light_entry...')
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f'  ✗ {t.__name__}: {e}')
            failed += 1
        except Exception as e:
            print(f'  ✗ {t.__name__}: {type(e).__name__}: {e}')
            failed += 1
    if failed == 0:
        print(f'\n✅ {len(tests)}/{len(tests)} PASS')
        sys.exit(0)
    else:
        print(f'\n❌ {failed}/{len(tests)} FAIL')
        sys.exit(1)


if __name__ == '__main__':
    main()
