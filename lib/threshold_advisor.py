#!/usr/bin/env python3
"""Phase 7 完整版自适应阈值 advisor (per `wf-pref-292`).

跑历史 compliance + shadow 日志算 per-rule 触发率, 输出建议阈值改动到
`state/runtime/b5_alerts_threshold/<date>.md`, 用户在 SessionStart inject 时拍板.

绝不私自改 frontmatter — 只输出建议. 用户跑 `apply_threshold.py` 接受.

Algorithm (per HANDOFF §8 Step 3, conservative):
  - 假阳率 > 5% → 建议升阈值 0.05
  - 假阳率 = 0% AND miss 率 > 50% → 建议降阈值 0.05
  - 否则 → 保持 (no-suggestion)

Miss 率定义: shadow judge 投 violated 但 deterministic 没拦的比例.
            如果 shadow log 不存在 / 数据 < 5 condition, 跳过该 rule.
假阳率定义: deterministic 触发但同条 message 后 user 没修正 — heuristic, 默认 0
            (没显式 ground truth source 时 conservative).

Per `code-pref-287` 路径解耦; 此模块用 path_config.get_*().
Per `wf-pref-027` versioned 备份 — apply_threshold.py 写 frontmatter 前备份.
Per `tool-pit-130` state 走 .claude/preference-tracker-state/, 不 /tmp.
"""
import json
import os
import sys
import datetime
from typing import Dict, List, Tuple

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import path_config
import rule_params

MIN_DATA_POINTS = 5
MISS_RATE_THRESHOLD = 0.5
FALSE_POSITIVE_RATE_THRESHOLD = 0.05
DELTA_PER_STEP = 0.05
MIN_THRESHOLD_VALUE = 0.1
MAX_THRESHOLD_VALUE = 1.0


def _parse_iso_timestamp(ts: str):
    """Parse ISO-8601 timestamp, return None on failure."""
    try:
        if ts.endswith('Z'):
            ts = ts[:-1] + '+00:00'
        return datetime.datetime.fromisoformat(ts)
    except (ValueError, AttributeError):
        return None


def _load_jsonl_window(path: str, days: int) -> List[dict]:
    """Read .jsonl, return entries within last `days` days. Missing file → []."""
    if not os.path.exists(path):
        return []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    entries = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_iso_timestamp(entry.get('timestamp', ''))
                if ts is None:
                    continue
                if ts >= cutoff:
                    entries.append(entry)
    except OSError:
        return []
    return entries


def _load_compliance_window(days: int = 7) -> List[dict]:
    return _load_jsonl_window(path_config.get_compliance_log_path(), days)


def _load_shadow_window(days: int = 7) -> List[dict]:
    return _load_jsonl_window(path_config.get_shadow_log_path(), days)


def _empty_record():
    return {
        'triggered_n': 0,
        'fp_marked_n': 0,
        'shadow_violated_n': 0,
        'shadow_only_n': 0,
        'shadow_pass_n': 0,
    }


def per_rule_stats(compliance_entries: List[dict], shadow_entries: List[dict]) -> Dict[str, dict]:
    """Aggregate per-rule trigger counts from compliance + shadow logs."""
    stats: Dict[str, dict] = {}

    for entry in compliance_entries:
        if entry.get('check_source') == 'deterministic_block':
            for violation in entry.get('b5_check', {}).get('deterministic_violations', []):
                rule_id = violation.get('atomic_id') or violation.get('rule_id')
                if not rule_id:
                    continue
                stats.setdefault(rule_id, _empty_record())
                stats[rule_id]['triggered_n'] += 1
        else:
            for rule_id in entry.get('fp_rules_in_response', []):
                if not isinstance(rule_id, str):
                    continue
                stats.setdefault(rule_id, _empty_record())
                stats[rule_id]['fp_marked_n'] += 1

    for entry in shadow_entries:
        for vote in entry.get('rule_votes', []):
            rule_id = vote.get('atomic_id') or vote.get('rule_id')
            if not rule_id:
                continue
            stats.setdefault(rule_id, _empty_record())
            verdict = vote.get('verdict', '')
            deterministic_caught = bool(vote.get('deterministic_caught'))
            if verdict == 'violated':
                stats[rule_id]['shadow_violated_n'] += 1
                if not deterministic_caught:
                    stats[rule_id]['shadow_only_n'] += 1
            elif verdict in ('pass', 'ok', 'compliant'):
                stats[rule_id]['shadow_pass_n'] += 1

    return stats


def _is_threshold_param(key: str, value) -> bool:
    """Heuristic: ratio-style threshold (0 < float ≤ 1)."""
    if not isinstance(value, (int, float)):
        return False
    if not (0 < float(value) <= 1):
        return False
    lowered = key.lower()
    return 'threshold' in lowered or 'ratio' in lowered or 'rate' in lowered


def suggest_threshold(rule_id: str, current_params: dict, stats: Dict[str, dict]) -> List[dict]:
    """Per-rule advice. Empty list = keep as-is."""
    record = stats.get(rule_id, _empty_record())
    suggestions: List[dict] = []

    shadow_total = record['shadow_violated_n']
    triggered_total = record['triggered_n']
    fp_count = record.get('false_positive_n', 0)

    if shadow_total < MIN_DATA_POINTS and triggered_total < MIN_DATA_POINTS:
        return []

    miss_rate = (record['shadow_only_n'] / shadow_total) if shadow_total > 0 else 0.0
    false_positive_rate = (fp_count / triggered_total) if triggered_total > 0 else 0.0

    threshold_keys = [k for k, v in current_params.items() if _is_threshold_param(k, v)]
    if not threshold_keys:
        return []

    for param in threshold_keys:
        current_val = float(current_params[param])
        new_val = None
        reason = None

        if false_positive_rate > FALSE_POSITIVE_RATE_THRESHOLD:
            candidate = round(min(MAX_THRESHOLD_VALUE, current_val + DELTA_PER_STEP), 2)
            if candidate != current_val:
                new_val = candidate
                reason = (
                    f'FP rate {false_positive_rate:.0%} > {FALSE_POSITIVE_RATE_THRESHOLD:.0%}'
                    f' (n={triggered_total}); raise threshold to reduce false catches'
                )
        elif false_positive_rate <= 0.0 and miss_rate > MISS_RATE_THRESHOLD and shadow_total >= MIN_DATA_POINTS:
            candidate = round(max(MIN_THRESHOLD_VALUE, current_val - DELTA_PER_STEP), 2)
            if candidate != current_val:
                new_val = candidate
                reason = (
                    f'shadow-miss rate {miss_rate:.0%} > {MISS_RATE_THRESHOLD:.0%}'
                    f' (n={shadow_total}), FP=0%; lower threshold to recover misses'
                )

        if new_val is not None and reason is not None:
            suggestions.append({
                'param': param,
                'from': current_val,
                'to': new_val,
                'reason': reason,
                'data_points': {
                    'shadow_total': shadow_total,
                    'shadow_only': record['shadow_only_n'],
                    'triggered': triggered_total,
                    'false_positive': fp_count,
                },
            })
    return suggestions


def render_suggestion_markdown(suggestions_per_rule: Dict[str, list], days: int = 7) -> str:
    """Build the .md content for state/runtime/b5_alerts_threshold/<date>.md."""
    today = datetime.date.today().isoformat()
    lines = [
        f'# Threshold Suggestions ({today})',
        '',
        f'Window: last {days} days. Generated by `threshold_advisor.py`.',
        '',
        '> Apply: `python3 ~/.claude/skills/preference-tracker/lib/apply_threshold.py --rule X --param Y --value Z`',
        '> Snooze: `python3 ~/.claude/skills/preference-tracker/lib/apply_threshold.py --snooze X --days 7`',
        '> No action: just ignore — suggestions never auto-apply (per `wf-pref-292`).',
        '',
    ]
    has_any = any(suggestions_per_rule.values())
    if not has_any:
        lines.append('No suggestions — current thresholds are stable, or insufficient data.')
        lines.append('')
        return '\n'.join(lines)

    for rule_id in sorted(suggestions_per_rule):
        suggestions = suggestions_per_rule[rule_id]
        if not suggestions:
            continue
        lines.append(f'## `{rule_id}`')
        lines.append('')
        for sugg in suggestions:
            lines.append(f"- `{sugg['param']}`: **{sugg['from']} → {sugg['to']}**")
            lines.append(f"  - Reason: {sugg['reason']}")
            data = sugg['data_points']
            lines.append(
                f"  - Data: shadow_violated={data['shadow_total']},"
                f" shadow_only_miss={data['shadow_only']},"
                f" deterministic_triggered={data['triggered']},"
                f" false_positive={data['false_positive']}"
            )
            lines.append(
                f"  - Apply: `python3 apply_threshold.py --rule {rule_id}"
                f" --param {sugg['param']} --value {sugg['to']}`"
            )
        lines.append('')
    return '\n'.join(lines)


def write_suggestion_file(content: str) -> str:
    """Write .md to state/runtime/b5_alerts_threshold/<date>.md, return abs path."""
    target_dir = path_config.get_b5_alerts_threshold_dir()
    os.makedirs(target_dir, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = os.path.join(target_dir, f'{today}.md')
    with open(path, 'w') as f:
        f.write(content)
    return path


def latest_suggestion_path() -> str:
    """Return path to newest .md in b5_alerts_threshold/, or '' if none."""
    target_dir = path_config.get_b5_alerts_threshold_dir()
    if not os.path.isdir(target_dir):
        return ''
    candidates = [f for f in os.listdir(target_dir) if f.endswith('.md')]
    if not candidates:
        return ''
    candidates.sort(reverse=True)
    return os.path.join(target_dir, candidates[0])


def advise(days: int = 7) -> Tuple[Dict[str, list], str]:
    """Top-level: load, analyze, write. Returns (suggestions_dict, output_path)."""
    compliance = _load_compliance_window(days)
    shadow = _load_shadow_window(days)
    stats = per_rule_stats(compliance, shadow)

    suggestions_per_rule: Dict[str, list] = {}
    for rule_id in stats:
        rule_params._clear_cache()
        current = rule_params.read_rule_params(rule_id)
        if not current:
            continue
        suggestions_per_rule[rule_id] = suggest_threshold(rule_id, current, stats)

    content = render_suggestion_markdown(suggestions_per_rule, days)
    path = write_suggestion_file(content)
    return suggestions_per_rule, path


if __name__ == '__main__':
    suggestions, output_path = advise()
    nonzero = sum(1 for sug in suggestions.values() if sug)
    total = sum(len(sug) for sug in suggestions.values())
    print(f'Wrote {output_path}')
    print(f'{total} suggestion(s) across {nonzero} rule(s); analyzed {len(suggestions)} rule(s) total')
