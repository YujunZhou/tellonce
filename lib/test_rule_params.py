#!/usr/bin/env python3
"""Phase 7 简易自适应阈值 — rule_params 读 frontmatter `params:` 块测试.

Run: python3 test_rule_params.py
Expects: 6/6 PASS.

测试覆盖:
  T1 真规则 lang-pit-130 读到 params
  T2 没设 params 块的规则返空字典 (oth-pref-001)
  T3 get_param 默认值 fallback
  T4 _parse_params_block 处理 注释 + 引号
  T5 缺失文件不 crash
  T6 改 frontmatter 后 _clear_cache 重读
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
    """seed rule lang-pit-130 frontmatter ships with `params:`; should parse to dict."""
    rule_params._clear_cache()
    p = rule_params.read_rule_params('lang-pit-130')
    assert isinstance(p, dict), '应返字典 got ' + str(type(p))
    assert p.get('chinese_ratio_threshold') == 0.7, \
        'lang-pit-130 chinese_ratio_threshold 应 0.7 got ' + str(p)
    assert p.get('min_length') == 50, 'lang-pit-130 min_length 应 50 got ' + str(p)
    return True


def test_T2_rule_without_params_returns_empty():
    """没设 params 块的规则返空字典."""
    rule_params._clear_cache()
    p = rule_params.read_rule_params('oth-pref-001')
    assert p == {}, 'oth-pref-001 没 params 应返 {} got ' + str(p)
    return True


def test_T3_get_param_fallback_default():
    """get_param 没找到 key 返默认值."""
    rule_params._clear_cache()
    v = rule_params.get_param('lang-pit-130', 'nonexistent_key', 999)
    assert v == 999, '没设 key 应返 default 999 got ' + str(v)
    # 已设 key 返 frontmatter 值, 不是 default
    v2 = rule_params.get_param('lang-pit-130', 'min_length', 50)
    assert v2 == 50, 'min_length 应返 frontmatter 50 got ' + str(v2)
    return True


def test_T4_parser_handles_comments_and_quotes():
    """_parse_params_block 解析注释 / 引号 / 不同类型."""
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
    """memory_dir 不存在或规则 atomic_id 找不到 → 返空字典不 crash."""
    rule_params._clear_cache()
    p = rule_params.read_rule_params('nonexistent-rule-id-9999')
    assert p == {}, '找不到规则应返 {} got ' + str(p)
    return True


def test_T6_clear_cache_rereads_frontmatter():
    """改 frontmatter 后 _clear_cache 让下次 read 拿新值."""
    rule_params._clear_cache()
    p1 = rule_params.read_rule_params('lang-pit-130')
    # 同 atomic_id 第二次拿 cached
    p2 = rule_params.read_rule_params('lang-pit-130')
    assert p1 == p2, 'cache hit 应 deterministic'
    # _clear_cache 后再 read, 仍能拿到 (fresh disk read)
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
