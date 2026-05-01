#!/usr/bin/env python3
"""TDD tests for threshold_advisor.py + apply_threshold.py.

Coverage (12 test):
  - load window: empty / within-cutoff / corrupt / missing-file
  - per_rule_stats: deterministic violation / fp_rules / shadow miss
  - suggest_threshold: high-miss-low-fp / high-fp / low-miss / no-data / no-threshold-param
  - render markdown: empty / non-empty
  - apply_threshold: find / update / preserves-other-params / not-found / versioned-backup / snooze

Per `wf-pref-027`: tests use tempfile; never touch any user's production state.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)

import path_config
import rule_params
import threshold_advisor as advisor
import apply_threshold as appth


class _SandboxBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='threshold_test_')
        self.state_dir = os.path.join(self.tmpdir, 'state')
        self.obs_dir = os.path.join(self.tmpdir, 'obs')
        self.memory_dir = os.path.join(self.tmpdir, 'memory')
        for d in (self.state_dir, self.obs_dir, self.memory_dir,
                  os.path.join(self.state_dir, 'b5_shadow_alerts'),
                  os.path.join(self.state_dir, 'b5_alerts_threshold')):
            os.makedirs(d, exist_ok=True)
        self._prev_env = {
            key: os.environ.get(key)
            for key in ('B5_STATE_DIR', 'B5_OBS_LOG_DIR', 'B5_MEMORY_DIR')
        }
        os.environ['B5_STATE_DIR'] = self.state_dir
        os.environ['B5_OBS_LOG_DIR'] = self.obs_dir
        os.environ['B5_MEMORY_DIR'] = self.memory_dir
        path_config._clear_cache()
        rule_params._clear_cache()

    def tearDown(self):
        for key, value in self._prev_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        path_config._clear_cache()
        rule_params._clear_cache()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestLoadWindow(_SandboxBase):

    def _write_compliance(self, entries):
        with open(path_config.get_compliance_log_path(), 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')

    def test_empty_log(self):
        self._write_compliance([])
        self.assertEqual(advisor._load_compliance_window(7), [])

    def test_within_window_only(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self._write_compliance([
            {'timestamp': (now - datetime.timedelta(days=1)).isoformat(), 'event': 'Stop'},
            {'timestamp': (now - datetime.timedelta(days=10)).isoformat(), 'event': 'Stop'},
        ])
        entries = advisor._load_compliance_window(7)
        self.assertEqual(len(entries), 1)

    def test_corrupt_lines_skipped(self):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(path_config.get_compliance_log_path(), 'w', encoding='utf-8') as f:
            f.write('not json\n')
            f.write(json.dumps({'timestamp': 'BAD', 'event': 'Stop'}) + '\n')
            f.write(json.dumps({'timestamp': now, 'event': 'Stop'}) + '\n')
        entries = advisor._load_compliance_window(7)
        self.assertEqual(len(entries), 1)

    def test_missing_file_returns_empty(self):
        self.assertEqual(advisor._load_compliance_window(7), [])
        self.assertEqual(advisor._load_shadow_window(7), [])


class TestPerRuleStats(_SandboxBase):

    def test_deterministic_violation_string_list(self):
        # C2 schema: deterministic_block writes a flat string list.
        compliance = [{
            'check_source': 'deterministic_block',
            'b5_check': {
                'deterministic_status': 'block',
                'deterministic_violations': ['lang-pit-130'],
            },
        }]
        stats = advisor.per_rule_stats(compliance, [])
        self.assertEqual(stats['lang-pit-130']['triggered_n'], 1)

    def test_deterministic_violation_dict_list_backcompat(self):
        # Tolerate older dict-shaped entries.
        compliance = [{
            'check_source': 'deterministic_block',
            'b5_check': {
                'deterministic_status': 'block',
                'deterministic_violations': [{'atomic_id': 'lang-pit-130'}],
            },
        }]
        stats = advisor.per_rule_stats(compliance, [])
        self.assertEqual(stats['lang-pit-130']['triggered_n'], 1)

    def test_fp_marked_in_response(self):
        compliance = [{'fp_rules_in_response': ['lang-pref-001', 'lang-pit-130']}]
        stats = advisor.per_rule_stats(compliance, [])
        self.assertEqual(stats['lang-pref-001']['fp_marked_n'], 1)
        self.assertEqual(stats['lang-pit-130']['fp_marked_n'], 1)

    def test_shadow_flat_alerted_counted(self):
        # C3 schema: shadow log entries are flat per-rule dicts, no `rule_votes`.
        # Two alerted shadow entries, one matched by deterministic block (within 60s,
        # same session) → counts as violated_n=2 but shadow_only_n=1.
        now = datetime.datetime.now(datetime.timezone.utc)
        ts_iso = now.isoformat()
        compliance = [{
            'timestamp': ts_iso,
            'session_id': 'sid-A',
            'check_source': 'deterministic_block',
            'b5_check': {
                'deterministic_status': 'block',
                'deterministic_violations': ['lang-pit-130'],
            },
        }]
        shadow = [
            {  # alerted, deterministic also caught (sid-A) → not shadow-only
                'timestamp': ts_iso,
                'session_id': 'sid-A',
                'rule_id': 'lang-pit-130',
                'alerted': True,
            },
            {  # alerted, deterministic missed (sid-B never blocked) → shadow-only
                'timestamp': ts_iso,
                'session_id': 'sid-B',
                'rule_id': 'lang-pit-130',
                'alerted': True,
            },
            {  # not alerted, no reason_no_alert → counts as pass
                'timestamp': ts_iso,
                'session_id': 'sid-A',
                'rule_id': 'lang-pit-130',
                'alerted': False,
            },
            {  # not alerted, rate-limited → suppressed (not pass, not violated)
                'timestamp': ts_iso,
                'session_id': 'sid-A',
                'rule_id': 'lang-pit-130',
                'alerted': False,
                'reason_no_alert': 'rate_limited',
            },
        ]
        stats = advisor.per_rule_stats(compliance, shadow)
        self.assertEqual(stats['lang-pit-130']['shadow_violated_n'], 2)
        self.assertEqual(stats['lang-pit-130']['shadow_only_n'], 1)
        self.assertEqual(stats['lang-pit-130']['shadow_pass_n'], 1)


class TestSuggestThreshold(_SandboxBase):

    def test_high_miss_low_fp_lowers_threshold(self):
        params = {'chinese_ratio_threshold': 0.7}
        stats = {'lang-pit-130': {
            'triggered_n': 0, 'fp_marked_n': 0,
            'shadow_violated_n': 10, 'shadow_only_n': 8, 'shadow_pass_n': 0,
        }}
        suggestions = advisor.suggest_threshold('lang-pit-130', params, stats)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]['param'], 'chinese_ratio_threshold')
        self.assertAlmostEqual(suggestions[0]['to'], 0.65, places=2)

    def test_low_miss_no_suggestion(self):
        params = {'chinese_ratio_threshold': 0.5}
        stats = {'lang-pit-130': {
            'triggered_n': 5, 'fp_marked_n': 0,
            'shadow_violated_n': 5, 'shadow_only_n': 1, 'shadow_pass_n': 4,
        }}
        suggestions = advisor.suggest_threshold('lang-pit-130', params, stats)
        self.assertEqual(suggestions, [])

    def test_insufficient_data_no_suggestion(self):
        params = {'chinese_ratio_threshold': 0.7}
        stats = {'lang-pit-130': {
            'triggered_n': 1, 'fp_marked_n': 0,
            'shadow_violated_n': 2, 'shadow_only_n': 2, 'shadow_pass_n': 0,
        }}
        suggestions = advisor.suggest_threshold('lang-pit-130', params, stats)
        self.assertEqual(suggestions, [])

    def test_no_threshold_param_no_suggestion(self):
        params = {'min_length': 50}
        stats = {'lang-pit-130': {
            'triggered_n': 0, 'fp_marked_n': 0,
            'shadow_violated_n': 10, 'shadow_only_n': 8, 'shadow_pass_n': 0,
        }}
        suggestions = advisor.suggest_threshold('lang-pit-130', params, stats)
        self.assertEqual(suggestions, [])


class TestRenderMarkdown(_SandboxBase):

    def test_empty_renders_no_suggestions_message(self):
        content = advisor.render_suggestion_markdown({'lang-pit-130': []}, days=7)
        self.assertIn('No suggestions', content)

    def test_non_empty_includes_apply_command(self):
        suggestions = {'lang-pit-130': [{
            'param': 'chinese_ratio_threshold', 'from': 0.7, 'to': 0.65,
            'reason': 'shadow-miss rate 80% > 50%',
            'data_points': {'shadow_total': 10, 'shadow_only': 8,
                            'triggered': 0, 'false_positive': 0},
        }]}
        content = advisor.render_suggestion_markdown(suggestions, days=7)
        self.assertIn('lang-pit-130', content)
        self.assertIn('chinese_ratio_threshold', content)
        self.assertIn('apply_threshold.py', content)


class TestApplyThreshold(_SandboxBase):

    def setUp(self):
        super().setUp()
        self.rule_path = os.path.join(self.memory_dir, 'pref_test_rule.md')
        with open(self.rule_path, 'w', encoding='utf-8') as f:
            f.write(
                '---\n'
                'name: test rule\n'
                'atomic_id: test-rule-001\n'
                'params:\n'
                '  threshold: 0.7  # initial value\n'
                '  min_length: 50\n'
                '---\n'
                'Body content here.\n'
            )

    def test_find_rule_file(self):
        path = appth.find_rule_file('test-rule-001')
        self.assertEqual(path, self.rule_path)

    def test_find_rule_returns_none_when_missing(self):
        self.assertIsNone(appth.find_rule_file('nonexistent-rule-999'))

    def test_update_param_changes_value(self):
        backup = appth.update_param(self.rule_path, 'threshold', 0.55)
        self.assertTrue(os.path.exists(backup))
        rule_params._clear_cache()
        params = rule_params.read_rule_params('test-rule-001')
        self.assertAlmostEqual(params['threshold'], 0.55, places=2)

    def test_update_preserves_other_params(self):
        appth.update_param(self.rule_path, 'threshold', 0.55)
        rule_params._clear_cache()
        params = rule_params.read_rule_params('test-rule-001')
        self.assertEqual(params['min_length'], 50)

    def test_update_param_not_found_raises(self):
        with self.assertRaises(ValueError):
            appth.update_param(self.rule_path, 'nonexistent_param', 0.5)

    def test_versioned_backup_preserves_original(self):
        backup = appth.update_param(self.rule_path, 'threshold', 0.55)
        with open(backup, encoding='utf-8') as f:
            content = f.read()
        self.assertIn('threshold: 0.7', content)

    def test_snooze_writes_until_date(self):
        snooze_path = appth.write_snooze('test-rule-001', days=7)
        self.assertTrue(os.path.exists(snooze_path))
        with open(snooze_path, encoding='utf-8') as f:
            content = f.read()
        self.assertIn('test-rule-001\t', content)


class TestAdviseEndToEnd(_SandboxBase):

    def test_end_to_end_run_with_real_rule_writes_md(self):
        rule_path = os.path.join(self.memory_dir, 'pref_e2e.md')
        with open(rule_path, 'w', encoding='utf-8') as f:
            f.write(
                '---\n'
                'atomic_id: e2e-rule-001\n'
                'params:\n'
                '  threshold: 0.7\n'
                '---\n'
                'Body.\n'
            )

        now = datetime.datetime.now(datetime.timezone.utc)
        with open(path_config.get_compliance_log_path(), 'w', encoding='utf-8') as f:
            for _ in range(3):
                f.write(json.dumps({
                    'timestamp': now.isoformat(),
                    'check_source': 'deterministic_block',
                    'b5_check': {'deterministic_violations': [{'atomic_id': 'e2e-rule-001'}]},
                }) + '\n')

        with open(path_config.get_shadow_log_path(), 'w', encoding='utf-8') as f:
            for i in range(10):
                f.write(json.dumps({
                    'timestamp': now.isoformat(),
                    'session_id': f'sid-{i}',
                    'rule_id': 'e2e-rule-001',
                    'alerted': True,
                }) + '\n')

        suggestions, output_path = advisor.advise(days=7)
        self.assertTrue(os.path.exists(output_path))
        self.assertIn('e2e-rule-001', suggestions)
        self.assertEqual(len(suggestions['e2e-rule-001']), 1)
        self.assertAlmostEqual(suggestions['e2e-rule-001'][0]['to'], 0.65, places=2)


if __name__ == '__main__':
    unittest.main()
