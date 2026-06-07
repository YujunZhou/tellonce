#!/usr/bin/env python3
"""Simple adaptive thresholds — tests for rule_params reading the frontmatter `params:` block.

Run: python3 test_rule_params.py
Expects: 6/6 PASS.

Tests cover:
  T1 a real rule lang-pit-130 reads params
  T2 a rule with no params block returns empty dict (oth-pref-001)
  T3 get_param default fallback
  T4 _parse_params_block handles comments + quotes
  T5 missing file does not crash
  T6 _clear_cache re-reads after frontmatter change
"""
import os
import sys
import tempfile
import shutil

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import path_config
import rule_params


def test_T1_real_rule_reads_params():
    """A rule whose frontmatter has a `params:` block parses to a dict.

    Self-contained: creates its own rule file in a temp memory dir (the public
    release ships no built-in rules, so this no longer relies on a seeded one)."""
    import tempfile, shutil
    td = tempfile.mkdtemp(prefix='pt_rp_')
    old_env = os.environ.get('B5_MEMORY_DIR')
    try:
        os.environ['B5_MEMORY_DIR'] = td
        with open(os.path.join(td, 'lang-pit-130.md'), 'w', encoding='utf-8') as f:
            f.write("---\natomic_id: lang-pit-130\nparams:\n"
                    "  chinese_ratio_threshold: 0.7\n  min_length: 50\n---\nbody\n")
        if hasattr(path_config, '_clear_cache'):
            path_config._clear_cache()
        rule_params._clear_cache()
        p = rule_params.read_rule_params('lang-pit-130')
        assert isinstance(p, dict), 'expected dict, got ' + str(type(p))
        assert p.get('chinese_ratio_threshold') == 0.7, \
            'chinese_ratio_threshold should be 0.7, got ' + str(p)
        assert p.get('min_length') == 50, 'min_length should be 50, got ' + str(p)
    finally:
        if old_env is None:
            os.environ.pop('B5_MEMORY_DIR', None)
        else:
            os.environ['B5_MEMORY_DIR'] = old_env
        if hasattr(path_config, '_clear_cache'):
            path_config._clear_cache()
        rule_params._clear_cache()
        shutil.rmtree(td, ignore_errors=True)
    return True


def test_T2_rule_without_params_returns_empty():
    """A rule with no params block returns an empty dict."""
    rule_params._clear_cache()
    p = rule_params.read_rule_params('oth-pref-001')
    assert p == {}, 'oth-pref-001 没 params 应返 {} got ' + str(p)
    return True


def test_T3_get_param_fallback_default():
    """get_param returns the default when the key is not found."""
    rule_params._clear_cache()
    v = rule_params.get_param('lang-pit-130', 'nonexistent_key', 999)
    assert v == 999, '没设 key 应返 default 999 got ' + str(v)
    # a set key returns the frontmatter value, not the default
    v2 = rule_params.get_param('lang-pit-130', 'min_length', 50)
    assert v2 == 50, 'min_length 应返 frontmatter 50 got ' + str(v2)
    return True


def test_T4_parser_handles_comments_and_quotes():
    """_parse_params_block parses comments / quotes / different types."""
    content = """---
name: Test
atomic_id: test-rule
params:
  threshold: 0.55   # inline comment
  min_length: 80
  mode: "strict"
  label: 'pilot'
  count: 42
other_field: ignored
---
body
"""
    p = rule_params._parse_params_block(content)
    assert p['threshold'] == 0.55, 'threshold 应 0.55 got ' + str(p)
    assert p['min_length'] == 80, 'min_length 应 80'
    assert p['mode'] == 'strict', 'mode 应 strict (去引号)'
    assert p['label'] == 'pilot', 'label 应 pilot (去单引号)'
    assert p['count'] == 42, 'count 应 42 (int)'
    assert 'other_field' not in p, 'top-level 字段不该混入'
    return True


def test_T5_missing_file_no_crash():
    """memory_dir missing or rule atomic_id not found → return empty dict without crashing."""
    rule_params._clear_cache()
    p = rule_params.read_rule_params('nonexistent-rule-id-9999')
    assert p == {}, '找不到规则应返 {} got ' + str(p)
    return True


def test_T6_clear_cache_rereads_frontmatter():
    """After changing frontmatter, _clear_cache makes the next read get the new value."""
    rule_params._clear_cache()
    p1 = rule_params.read_rule_params('lang-pit-130')
    # second call with the same atomic_id gets the cached value
    p2 = rule_params.read_rule_params('lang-pit-130')
    assert p1 == p2, 'cache hit 应 deterministic'
    # after _clear_cache, reading again still works (fresh disk read)
    rule_params._clear_cache()
    p3 = rule_params.read_rule_params('lang-pit-130')
    assert p3 == p1, '_clear_cache 后重读应一致'
    return True


def main():
    tests = [
        ('T1 lang-pit-130 真规则读 params', test_T1_real_rule_reads_params),
        ('T2 没 params 块返空', test_T2_rule_without_params_returns_empty),
        ('T3 get_param fallback default', test_T3_get_param_fallback_default),
        ('T4 解析注释 / 引号 / 类型', test_T4_parser_handles_comments_and_quotes),
        ('T5 缺规则不 crash', test_T5_missing_file_no_crash),
        ('T6 _clear_cache 重读', test_T6_clear_cache_rereads_frontmatter),
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            ok = fn()
            if ok:
                print('  PASS  ' + name)
                passed += 1
            else:
                print('  FAIL  ' + name + ' (returned False)')
                failed.append(name)
        except AssertionError as e:
            print('  FAIL  ' + name + ': ' + str(e))
            failed.append(name)
        except Exception as e:
            print('  ERR   ' + name + ': ' + type(e).__name__ + ': ' + str(e))
            failed.append(name)
    print('\n' + str(passed) + '/' + str(len(tests)) + ' PASS, ' + str(len(failed)) + ' FAIL')
    if failed:
        print('Failed: ' + str(failed))
        sys.exit(1)


if __name__ == '__main__':
    main()
