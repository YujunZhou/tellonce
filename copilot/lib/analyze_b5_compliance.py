#!/usr/bin/env python3
"""Tier B item 6 — daily summary of B5 compliance log.

Reads compliance_log.jsonl entries within last 24h, computes:
  - deterministic block counts per rule
  - shadow judge violation rate, latency, cost
  - soft injection alert generation/dedup rates
  - threshold alerts (judge fail rate > 5%, daily cost > 80% cap, rule blocked > 10x)

Outputs:
  - state/runtime/b5_daily_summary/<YYYY-MM-DD>.md (human readable summary)
  - state/runtime/b5_alerts_threshold/<YYYY-MM-DD>.md (only if any threshold tripped)

Usage:
  python3 analyze_b5_compliance.py [--days N]  (default: last 1 day)
"""
import os
import sys
import json
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta

import sys as _sys
import os as _os
_LIB_DIR = _os.path.dirname(_os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config

COMPLIANCE_LOG = path_config.get_compliance_log_path()
SUMMARY_DIR = path_config.get_b5_summary_dir()
ALERT_DIR = path_config.get_b5_alerts_threshold_dir()

# Threshold defaults
JUDGE_FAIL_RATE_THRESHOLD = 0.05  # 5%
COST_RATIO_THRESHOLD = 0.80  # 80% of B5_DAILY_COST_CAP
B5_DAILY_COST_CAP = 0.50
RULE_BLOCK_COUNT_THRESHOLD = 10  # >10 blocks/day suspect false-positive


def parse_log(days_back=1):
    """Read compliance_log.jsonl, return list of entries within last N days."""
    if not os.path.exists(COMPLIANCE_LOG):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    out = []
    with open(COMPLIANCE_LOG, errors='ignore') as f:
        for line in f:
            try:
                o = json.loads(line.strip())
            except Exception:
                continue
            ts_str = o.get('timestamp')
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            except Exception:
                continue
            if ts >= cutoff:
                out.append(o)
    return out


def compute_metrics(entries):
    """Compute summary metrics from log entries."""
    total = len(entries)
    deterministic_blocks = Counter()
    deterministic_pass = 0
    deterministic_disabled = 0
    shadow_violations = 0
    shadow_compliant = 0
    shadow_disabled = 0
    shadow_no_credit = 0
    shadow_cost_capped = 0
    shadow_judge_error = 0
    shadow_skip_short = 0
    shadow_alerted_count = 0
    shadow_violation_rules = Counter()
    judge_latencies = []
    judge_costs = []

    for e in entries:
        b5 = e.get('b5_check') or {}
        # Deterministic
        det_status = b5.get('deterministic_status')
        if det_status == 'block':
            for rid in b5.get('deterministic_violations', []):
                deterministic_blocks[rid] += 1
        elif det_status == 'pass':
            deterministic_pass += 1
        elif det_status == 'disabled':
            deterministic_disabled += 1
        # Shadow (M2 fix per Phase 8 review): bucket per-rule using shadow_violation_rule_ids
        shadow_status = b5.get('shadow_judge_status')
        if shadow_status == 'violation':
            shadow_violations += 1
            for rid in b5.get('shadow_violation_rule_ids', []):
                shadow_violation_rules[rid] += 1
        elif shadow_status == 'compliant':
            shadow_compliant += 1
        elif shadow_status == 'disabled':
            shadow_disabled += 1
        elif shadow_status == 'no_credit':
            shadow_no_credit += 1
        elif shadow_status == 'cost_capped':
            shadow_cost_capped += 1
        elif shadow_status == 'judge_error':
            shadow_judge_error += 1
        elif shadow_status == 'skip_short':
            shadow_skip_short += 1
        if 'shadow_alerted_count' in b5:
            shadow_alerted_count += b5['shadow_alerted_count']
        if 'shadow_judge_latency_ms' in b5:
            judge_latencies.append(b5['shadow_judge_latency_ms'])
        if 'shadow_judge_cost_usd' in b5:
            judge_costs.append(b5['shadow_judge_cost_usd'])

    judge_total = (shadow_violations + shadow_compliant + shadow_judge_error)
    judge_fail_rate = (shadow_judge_error / judge_total) if judge_total > 0 else 0.0

    return {
        'total_entries': total,
        'deterministic_blocks': dict(deterministic_blocks),
        'deterministic_pass': deterministic_pass,
        'deterministic_disabled': deterministic_disabled,
        'shadow_violations': shadow_violations,
        'shadow_compliant': shadow_compliant,
        'shadow_disabled': shadow_disabled,
        'shadow_no_credit': shadow_no_credit,
        'shadow_cost_capped': shadow_cost_capped,
        'shadow_judge_error': shadow_judge_error,
        'shadow_skip_short': shadow_skip_short,
        'shadow_alerted_count': shadow_alerted_count,
        'judge_total': judge_total,
        'judge_fail_rate': judge_fail_rate,
        'avg_latency_ms': sum(judge_latencies) / len(judge_latencies) if judge_latencies else 0,
        'p95_latency_ms': sorted(judge_latencies)[int(len(judge_latencies) * 0.95)] if judge_latencies else 0,
        'total_cost_usd': sum(judge_costs),
    }


def write_summary(metrics, date_str):
    """Write daily summary markdown."""
    os.makedirs(SUMMARY_DIR, exist_ok=True)
    path = os.path.join(SUMMARY_DIR, f'{date_str}.md')
    det_lines = '\n'.join(
        f'  - {rid}: blocked {n} time(s)'
        for rid, n in sorted(metrics['deterministic_blocks'].items(), key=lambda x: -x[1])
    ) or '  (none)'

    cost_pct = (metrics['total_cost_usd'] / B5_DAILY_COST_CAP * 100) if B5_DAILY_COST_CAP > 0 else 0

    body = f"""# B5 daily summary {date_str}

**Total log entries (last 24h)**: {metrics['total_entries']}

## Deterministic (Tier A item 1)
- Total pass: {metrics['deterministic_pass']}
- Total disabled (B5_DETERMINISTIC_DISABLED env): {metrics['deterministic_disabled']}
- Blocks by rule:
{det_lines}

## Shadow LLM judge (Tier A item 2)
- Total turns judged: {metrics['judge_total']} (violations: {metrics['shadow_violations']}, compliant: {metrics['shadow_compliant']}, errors: {metrics['shadow_judge_error']})
- Skipped: {metrics['shadow_skip_short']} (short response), {metrics['shadow_no_credit']} (no credit), {metrics['shadow_disabled']} (disabled), {metrics['shadow_cost_capped']} (cost cap)
- Judge fail rate: {metrics['judge_fail_rate']:.2%}
- Avg latency: {metrics['avg_latency_ms']:.0f}ms; p95: {metrics['p95_latency_ms']:.0f}ms
- Total cost: ${metrics['total_cost_usd']:.4f} ({cost_pct:.1f}% of ${B5_DAILY_COST_CAP:.2f}/day cap)

## Soft injection (Tier A item 3)
- Alerts written: {metrics['shadow_alerted_count']}
"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(body)
    return path


def check_thresholds(metrics, date_str):
    """Write threshold alert MD if any threshold tripped. Returns alert path or None."""
    alerts = []
    if metrics['judge_total'] >= 10 and metrics['judge_fail_rate'] > JUDGE_FAIL_RATE_THRESHOLD:
        alerts.append(f"⚠ Judge fail rate {metrics['judge_fail_rate']:.2%} > threshold {JUDGE_FAIL_RATE_THRESHOLD:.2%} ({metrics['shadow_judge_error']}/{metrics['judge_total']})")
    cost_ratio = metrics['total_cost_usd'] / B5_DAILY_COST_CAP if B5_DAILY_COST_CAP > 0 else 0
    if cost_ratio > COST_RATIO_THRESHOLD:
        alerts.append(f"⚠ Daily cost ${metrics['total_cost_usd']:.4f} > {COST_RATIO_THRESHOLD*100:.0f}% of cap ${B5_DAILY_COST_CAP:.2f}")
    for rid, n in metrics['deterministic_blocks'].items():
        if n > RULE_BLOCK_COUNT_THRESHOLD:
            alerts.append(f"⚠ Rule {rid} blocked {n} times > threshold {RULE_BLOCK_COUNT_THRESHOLD} (suspect false-positive — review whitelist)")
    if not alerts:
        return None
    os.makedirs(ALERT_DIR, exist_ok=True)
    path = os.path.join(ALERT_DIR, f'{date_str}.md')
    body = f"""# B5 threshold alerts {date_str}

The following thresholds were tripped today:

""" + '\n'.join(f'- {a}' for a in alerts) + '\n\nReview deterministic whitelist, judge accuracy, or cost cap as appropriate.\n'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(body)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=1)
    # H12 fix (2026-05-01): dashboard.sh has been passing --json since
    # introduction (M5 fix), but argparse never declared it → silent failure
    # on `dashboard.sh --json`. Now machine-parseable via stdout JSON.
    parser.add_argument('--json', action='store_true',
                        help='Emit a single JSON object on stdout (no markdown).')
    args = parser.parse_args()

    entries = parse_log(days_back=args.days)
    metrics = compute_metrics(entries)

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    if args.json:
        # JSON mode: machine-readable output. Don't write summary / alert files
        # (those are markdown for humans). Caller can post-process via jq.
        json_out = {
            'date': date_str,
            'days_back': args.days,
            'metrics': metrics,
            'cost_cap_usd': B5_DAILY_COST_CAP,
        }
        print(json.dumps(json_out, ensure_ascii=False, indent=2, default=str))
        return

    summary_path = write_summary(metrics, date_str)
    alert_path = check_thresholds(metrics, date_str)

    print(f'Summary written to: {summary_path}')
    if alert_path:
        print(f'Threshold alerts written to: {alert_path}')
    else:
        print('No threshold alerts.')

    # Print key numbers
    print(f"\nKey metrics:")
    print(f"  Total entries: {metrics['total_entries']}")
    print(f"  Deterministic blocks: {sum(metrics['deterministic_blocks'].values())}")
    print(f"  Shadow violations: {metrics['shadow_violations']}")
    print(f"  Shadow alerted: {metrics['shadow_alerted_count']}")
    print(f"  Judge total: {metrics['judge_total']} (fail rate {metrics['judge_fail_rate']:.2%})")
    print(f"  Cost: ${metrics['total_cost_usd']:.4f} / cap ${B5_DAILY_COST_CAP:.2f}")


if __name__ == '__main__':
    main()
