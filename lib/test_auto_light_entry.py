#!/usr/bin/env python3
"""Tests for verify_compliance.generate_auto_light_entry — Sprint v23 day-1 hook fallback.

Run: python3 test_auto_light_entry.py
Expects: 7/7 PASS (kickoff §Step 1 asked for 5+; we cover the 5 listed cases plus
2 extra schema/edge tests).

Tests cover:
  1. Write the first entry to an empty file (empty obs_log creation + single entry)
  2. Append after a normal write (existing entries preserved + new entry at the end)
  3. Atomic interruption retry (.tmp residue cleanup; re-run does not corrupt)
  4. Correct age computation (age_sec / threshold_sec fully written into the entry)
  5. session_id propagate (top-level session_id + entry_id contains sid prefix)
  6. schema satisfies hook validation (detection.detected boolean, trigger excerpt
     non-empty, self_observations.uncertainty_notes non-empty — CHECK 2 is key)
  7. Does not break when existing file has no trailing newline (robustness)
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
    """Write the first entry to an empty obs_log; file created + 1 line of valid JSON."""
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
        # parent dir mkdir -p must work too (before sub/ exists)
        assert os.path.isdir(os.path.dirname(obs))
    print('  ✓ test_empty_file_first_write')


def test_append_to_existing():
    """Append when entries exist, without losing old entries."""
    with tempfile.TemporaryDirectory() as d:
        obs = os.path.join(d, 'observations.jsonl')
        # seed 2 old entries
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
    """Simulate .tmp residue left by a previous crash; a new call should not import the residue + not corrupt obs."""
    with tempfile.TemporaryDirectory() as d:
        obs = os.path.join(d, 'observations.jsonl')
        with open(obs, 'w') as f:
            f.write(json.dumps({'entry_id': 'real-1'}) + '\n')
        # residue .tmp contains garbage (previous process interrupted)
        residue = obs + '.tmp.99999.99999999999999'
        with open(residue, 'w') as f:
            f.write('CORRUPT-RESIDUE-DO-NOT-MERGE\n')
        # new call
        vc.generate_auto_light_entry(
            session_id='sid-tmp-003',
            age_sec=1900,
            threshold_sec=1800,
            obs_log_path=obs,
        )
        entries = _read_jsonl(obs)
        # old entry preserved + new entry added, residue does not enter obs
        contents = open(obs).read()
        assert 'CORRUPT-RESIDUE-DO-NOT-MERGE' not in contents, \
            'residue tmp file leaked into obs_log'
        assert len(entries) == 2, f'expected 2 entries, got {len(entries)}'
        assert entries[0]['entry_id'] == 'real-1'
        assert entries[1]['auto_generated'] is True
        # residue tmp file may still exist (the function does not proactively clean unrelated tmp in the same dir); only test obs integrity
    print('  ✓ test_tmp_residue_does_not_corrupt')


def test_age_and_threshold_propagate():
    """age_sec and threshold_sec are written into the entry in full, with correct int cast."""
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
        # uncertainty_notes must contain the age + threshold numbers (hook validation requires non-empty)
        notes = e['self_observations']['uncertainty_notes']
        assert '2456' in notes, f'age missing from notes: {notes}'
        assert '1800' in notes, f'threshold missing from notes: {notes}'
    print('  ✓ test_age_and_threshold_propagate')


def test_session_id_propagate_and_entry_id_format():
    """session_id top-level field correct + entry_id slug contains sid prefix (8 char)."""
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
        # entry_id looks like "<iso>-auto-fallback-<sid_slug>"
        assert 'auto-fallback' in e['entry_id'], f'entry_id missing slug: {e["entry_id"]}'
        # sid_slug is the first 8 chars (re.sub does not change 'claude-s'), entry_id should end with it
        assert e['entry_id'].endswith('-claude-s'), f'entry_id should end with sid prefix: {e["entry_id"]}'
        # unknown session_id does not crash
        vc.generate_auto_light_entry(
            session_id=None,
            age_sec=2000, threshold_sec=1800,
            obs_log_path=obs,
        )
        entries = _read_jsonl(obs)
        assert entries[1]['session_id'] == 'unknown'
    print('  ✓ test_session_id_propagate_and_entry_id_format')


def test_schema_satisfies_hook_validation():
    """New entries must pass check-observation-log.sh's 4-check structured validation:
    detection.detected boolean / trigger.user_message_excerpt non-empty /
    self_observations.uncertainty_notes non-empty / when detected=false skip the strict sub-check."""
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
        # 4. detected=false → skip signal_type/content/conf_text check (depends on hook code)
        # but the schema should normalize it to None
        assert e['detection']['signal_type'] is None
        # auto_* markers for hook trace + later analysis
        assert e['auto_generated'] is True
        assert e['auto_reason'] == 'hook_fallback_due_to_age_exceeded'
    print('  ✓ test_schema_satisfies_hook_validation')


def test_existing_no_trailing_newline():
    """When the old obs_log has no trailing newline (abnormal corrupt state), the new entry should auto-add a newline to fix it."""
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
        # file contents should be 2 lines, both ending with \n
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
