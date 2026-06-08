#!/usr/bin/env python3
"""Shadow LLM judge (Stop hook).

An optional semantic judge that runs on Stop events to catch violations of the
user's recorded rules that the deterministic checks miss. It NEVER blocks
(always exits 0); it only logs and surfaces a rolling alert.

Per-runtime dispatch: the Claude Code runtime uses a `claude -p` subprocess
(_judge_call_cli, the default). An optional SDK path (_judge_call_sdk) is a
fallback (B5_USE_SDK=1).

Side effects:
  - Append violations to the shadow log (history)
  - Update the rolling shadow-alert file (cap N=3, 24h TTL)
  - Append a check entry to the compliance log

Privacy + gates (the judge sends the last user message + assistant reply to an LLM):
  - OFF by default for public installs — gated by path_config.shadow_enabled()
    (enable with PT_SHADOW=1 or config {"shadow": true}).
  - B5_SHADOW_DISABLED=1 hard opt-out
  - B5_DAILY_COST_CAP daily budget (auto-disable if exceeded)
  - judge_confidence threshold (lower -> log only, no alert)
  - per-rule rate limit (same rule_id within 24h -> no alert)

Verdicts are emitted as one-line JSON. On judge timeout/error the hook logs a
warning and exits 0.
"""
import json
import os
import re
import sys
import time
import traceback
import contextlib
try:
    import fcntl
except ImportError:
    fcntl = None  # Windows
from datetime import datetime, timezone, timedelta

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import path_config  # central path config
import redaction  # codex review H2 fix (2026-05-01): redact secrets pre-disk

# Module-level paths via path_config (lazy evaluated at module load, lru_cached)
MEMORY_DIR = path_config.get_memory_dir()
COMPLIANCE_LOG = path_config.get_compliance_log_path()
SHADOW_LOG = path_config.get_shadow_log_path()
SHADOW_ALERT_MD = path_config.get_shadow_alert_md_path()
COST_LOG_DIR = path_config.get_cost_log_dir()

# Env opt-out and gates
B5_SHADOW_DISABLED = os.environ.get('B5_SHADOW_DISABLED', '').lower() in ('1', 'true', 'yes')
# Defaults to True (assumes credit available); only disabled when ANTHROPIC_CREDIT_OK=0/false is set explicitly
ANTHROPIC_CREDIT_OK = os.environ.get('ANTHROPIC_CREDIT_OK', '1').lower() in ('1', 'true', 'yes')
B5_DAILY_COST_CAP = float(os.environ.get('B5_DAILY_COST_CAP', '0.50'))
B5_USE_DEEPINFRA = os.environ.get('B5_USE_DEEPINFRA', '').lower() in ('1', 'true', 'yes')
B5_USE_SDK = os.environ.get('B5_USE_SDK', '').lower() in ('1', 'true', 'yes')  # default False = use the CLI channel
B5_JUDGE_MODEL = os.environ.get('B5_JUDGE_MODEL', 'claude-haiku-4-5')  # small/cheap default model
B5_CONFIDENCE_THRESHOLD = float(os.environ.get('B5_CONFIDENCE_THRESHOLD', '0.85'))
ALERT_ROLLING_CAP = int(os.environ.get('B5_ALERT_ROLLING_CAP', '3'))
RATE_LIMIT_HOURS = float(os.environ.get('B5_RATE_LIMIT_HOURS', '24'))
TTL_HOURS = float(os.environ.get('B5_TTL_HOURS', '24'))

# Test mock — when set, skip real LLM call, return canned verdict from env
B5_TEST_MOCK_VERDICT = os.environ.get('B5_TEST_MOCK_VERDICT', '')

# Rule IDs the shadow judge evaluates. Empty by default (ship-empty): a public
# install judges no rules until the user opts specific rules into shadow judging
# via B5_SHADOW_RULE_IDS=id1,id2 (or their own config overlay).
SHADOW_RULE_IDS = [s.strip() for s in os.environ.get('B5_SHADOW_RULE_IDS', '').split(',') if s.strip()]


def _now():
    return datetime.now(timezone.utc)


def _today_str():
    return _now().strftime('%Y-%m-%d')


def _read_rule_text(atomic_id):
    """Find memory file with given atomic_id, return rule_text (description + body excerpt)."""
    try:
        import glob
        for path in glob.glob(os.path.join(MEMORY_DIR, '*.md')):
            try:
                with open(path, errors='ignore') as f:
                    c = f.read()
            except Exception:
                continue
            if f'atomic_id: {atomic_id}' not in c:
                continue
            # Extract description from frontmatter
            desc_m = re.search(r'^description:\s*(.+)$', c, re.MULTILINE)
            desc = desc_m.group(1).strip() if desc_m else ''
            # Extract first 500 chars of body (after frontmatter close)
            body_m = re.split(r'^---\s*$', c, maxsplit=2, flags=re.MULTILINE)
            body = body_m[2][:500].strip() if len(body_m) >= 3 else ''
            return f'{desc}\n\n{body}'
    except Exception:
        pass
    return ''


def _read_response_and_last_user(stdin_data):
    """Extract last assistant response + last user prompt from transcript."""
    transcript_path = stdin_data.get('transcript_path')
    if not transcript_path or not os.path.exists(transcript_path):
        return '', ''
    try:
        with open(transcript_path, errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return '', ''
    response = ''
    last_user = ''
    # Find last assistant text + last user text
    for line in reversed(lines[-300:]):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not response and o.get('type') == 'assistant':
            msg = o.get('message') or {}
            content = msg.get('content', [])
            if isinstance(content, str):
                response = content
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        response = item.get('text', '')
                        break
        if not last_user and o.get('type') == 'user':
            msg = o.get('message') or {}
            content = msg.get('content', '')
            if isinstance(content, str):
                last_user = content
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        last_user = item.get('text', '')
                        break
            elif content:
                last_user = str(content)
        if response and last_user:
            break
    return response, last_user


def _read_daily_cost():
    """Read today's accumulated judge cost (USD)."""
    path = os.path.join(COST_LOG_DIR, f'{_today_str()}.json')
    if not os.path.exists(path):
        return 0.0
    try:
        with open(path) as f:
            d = json.load(f)
        return float(d.get('total_usd', 0.0))
    except Exception:
        return 0.0


@contextlib.contextmanager
def _cost_lock():
    """Cross-process exclusive lock for the daily-cost RMW (M4).

    Best-effort: if flock is unsupported, proceed unlocked rather than break.
    """
    try:
        os.makedirs(COST_LOG_DIR, exist_ok=True)
    except Exception:
        pass
    lock_path = os.path.join(COST_LOG_DIR, '.daily_cost.lock')
    fh = None
    try:
        try:
            fh = open(lock_path, 'a+')
            if fcntl:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            else:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        except (OSError, ValueError):
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
            fh = None
        yield
    finally:
        if fh is not None:
            try:
                if fcntl:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                else:
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass


def _bump_daily_cost(extra_usd):
    """Add extra_usd to today's cost log.

    M4 fix: the read-modify-write is wrapped in a cross-process lock so two
    concurrent shadow-judge calls can't each read the same prior total and
    overwrite each other (which would let the daily cost cap be exceeded).
    Best-effort lock — falls through unlocked if the filesystem doesn't support
    flock.
    """
    os.makedirs(COST_LOG_DIR, exist_ok=True)
    path = os.path.join(COST_LOG_DIR, f'{_today_str()}.json')
    with _cost_lock():
        cur = _read_daily_cost()
        new = cur + extra_usd
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'date': _today_str(), 'total_usd': new}, f)
        except Exception:
            pass
    return new


def _read_shadow_log_recent(hours=24):
    """Read shadow log entries within last `hours`. Returns list of dicts."""
    if not os.path.exists(SHADOW_LOG):
        return []
    cutoff = _now() - timedelta(hours=hours)
    out = []
    try:
        with open(SHADOW_LOG, errors='ignore') as f:
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
    except Exception:
        pass
    return out


def _is_rate_limited(rule_id, hours=24):
    """Check if same rule_id had alert written in last `hours`. Returns True if limited."""
    recent = _read_shadow_log_recent(hours=hours)
    return any(o.get('rule_id') == rule_id and o.get('alerted', False) for o in recent)


def _append_shadow_log(entry):
    """Append entry dict to b5_shadow_log.jsonl. Restrict to user-only readable
    (H10 fix: shadow log contains evidence/feedback excerpts of user content).

    H2 fix: sanitize entry before serialize — judge evidence/feedback can quote
    user content that contains API keys / SSH keys / DB URIs.
    """
    os.makedirs(os.path.dirname(SHADOW_LOG), exist_ok=True)
    try:
        with open(SHADOW_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(redaction.sanitize(entry), ensure_ascii=False) + '\n')
        path_config.chmod_or_warn(SHADOW_LOG, 0o600)
    except Exception:
        pass


def _update_alert_md(violation_alerts):
    """Refresh B5_SHADOW_ALERT.md with rolling cap N=3 latest violations.

    Reads recent shadow log (within TTL_HOURS), keeps top-N by timestamp desc,
    writes to alert md. Older alerts are pruned (TTL).
    """
    recent = _read_shadow_log_recent(hours=TTL_HOURS)
    # filter to alerted=True (not all log entries are alert-worthy)
    alerts = [o for o in recent if o.get('alerted', False)]
    # most recent first, cap N
    alerts.sort(key=lambda o: o.get('timestamp', ''), reverse=True)
    alerts = alerts[:ALERT_ROLLING_CAP]

    if not alerts:
        # No alerts in TTL window: write empty placeholder
        body = f'# B5 shadow violation alert (last {int(TTL_HOURS)}h)\n\n*(no alerts in window)*\n'
    else:
        sections = [f'# B5 shadow violation alert (last {int(TTL_HOURS)}h)\n',
                    f'**Rolling cap**: {ALERT_ROLLING_CAP} latest violations across all turns (older auto-archived to b5_shadow_log.jsonl).\n']
        for i, a in enumerate(alerts, 1):
            sections.append(f'## Violation {i}')
            sections.append(f'- **Rule**: {a.get("rule_id", "<unknown>")} ({redaction.redact(a.get("rule_desc", ""))})')
            sections.append(f'- **Last triggered**: {a.get("timestamp", "")}')
            sections.append(f'- **Confidence**: {a.get("judge_confidence", "?")}')
            sections.append(f'- **Evidence**: {redaction.redact(a.get("evidence", ""))[:200]}')
            sections.append(f'- **Feedback**: {redaction.redact(a.get("feedback", ""))[:200]}')
            sections.append('')
        body = '\n'.join(sections) + '\n'
    os.makedirs(os.path.dirname(SHADOW_ALERT_MD), exist_ok=True)
    try:
        with open(SHADOW_ALERT_MD, 'w', encoding='utf-8') as f:
            f.write(body)
        # H10 fix: contains violation evidence; restrict to user-only.
        path_config.chmod_or_warn(SHADOW_ALERT_MD, 0o600)
    except Exception:
        pass


def _judge_call_cli(rules, last_user, response):
    """Invoke claude -p CLI subprocess judge. 0 API charge (uses the CLI subscription).

    Default: prefer the CLI when both CLI subscription and SDK are available.
    Default model: claude-haiku-4-5 (a small/cheap model suffices for the shadow judge).
    Returns (verdicts_list, cost_usd=0.0, latency_ms, error_str_or_None).
    """
    import subprocess
    t0 = time.time()
    # Keep the judge prompt small.
    rules_text = '\n'.join(
        f'- {r["rule_id"]}: {r["rule_text"][:200]}'
        for r in rules
    )
    prompt = f"""You are a strict compliance judge. Decide whether the response below violates any rule. Output exactly one line of JSON, no preamble.

Last user prompt (truncated):
{(last_user or '')[:400]}

Response (truncated):
{response[:4000]}

Rules:
{rules_text}

Output strict one-line JSON in this format:
{{"verdicts":[{{"rule_id":"<id>","applicable":true|false,"compliant":true|false,"judge_confidence":0.0-1.0,"evidence":"<10-30 chars>","feedback":"<fix instruction if not compliant>"}}]}}"""
    try:
        proc = subprocess.run(
            ['claude', '-p', prompt, '--model', B5_JUDGE_MODEL,
             '--output-format', 'text'],
            capture_output=True, text=True, timeout=60,
        )
        latency_ms = (time.time() - t0) * 1000
        if proc.returncode != 0:
            return [], 0.0, latency_ms, f'CLI exit {proc.returncode}: {proc.stderr[:200]}'
        # Find JSON in stdout (model may add prose despite instruction)
        m = re.search(r'\{[^{}]*"verdicts"[^{}]*\[.*?\]\s*\}', proc.stdout, re.DOTALL)
        if not m:
            # Fallback: try last {} block
            m = re.search(r'\{.*\}', proc.stdout, re.DOTALL)
        if not m:
            return [], 0.0, latency_ms, f'no JSON in output: {proc.stdout[:200]}'
        try:
            obj = json.loads(m.group())
        except json.JSONDecodeError as e:
            return [], 0.0, latency_ms, f'JSON parse error: {e}'
        verdicts_raw = obj.get('verdicts', [])
        verdicts = []
        for v_dict in verdicts_raw:
            if not isinstance(v_dict, dict):
                continue
            verdicts.append({
                'rule_id': v_dict.get('rule_id'),
                'applicable': v_dict.get('applicable', True),
                'compliant': v_dict.get('compliant', True),
                'judge_confidence': float(v_dict.get('judge_confidence', 0.85)),
                'evidence': v_dict.get('evidence', '')[:300],
                'feedback': v_dict.get('feedback', '')[:300],
            })
        # 0 cost — CLI uses the subscription
        return verdicts, 0.0, latency_ms, None
    except subprocess.TimeoutExpired:
        return [], 0.0, 60000.0, 'CLI timeout 60s'
    except FileNotFoundError:
        return [], 0.0, (time.time() - t0) * 1000, 'claude CLI not in PATH'
    except Exception as e:
        return [], 0.0, (time.time() - t0) * 1000, f'CLI exception: {type(e).__name__}: {str(e)[:200]}'


def _judge_call_sdk(rules, last_user, response):
    """Optional: Anthropic SDK call (only when B5_USE_SDK=1 + B5_SDK_MODULE set).

    Charges API credit per call. Default install path (CLI subscription) is 0
    cost; SDK only matters for paper-replication / SDK-only environments.

    To enable: set both env vars
      B5_USE_SDK=1
      B5_SDK_MODULE=/path/to/your/sdk_wrapper_module   # exposes verify_all_global()

    The wrapper module must define `verify_all_global(rules, last_user, response) ->
    {"verdicts": [{rule_id, applicable, compliant, judge_confidence, evidence, feedback}, ...]}`.

    Without B5_SDK_MODULE the call returns a friendly error so the caller can fall
    back to CLI; no hardcoded paths.
    """
    t0 = time.time()
    sdk_module_path = os.environ.get('B5_SDK_MODULE', '').strip()
    if not sdk_module_path:
        return [], 0.0, (time.time() - t0) * 1000, (
            'B5_USE_SDK=1 but B5_SDK_MODULE is unset. '
            'Either unset B5_USE_SDK to use CLI (default), or set B5_SDK_MODULE '
            'to a directory exposing verify_all_global().'
        )
    if not os.path.isdir(sdk_module_path):
        return [], 0.0, (time.time() - t0) * 1000, (
            f'B5_SDK_MODULE={sdk_module_path!r} is not a directory.'
        )
    try:
        sys.path.insert(0, sdk_module_path)
        import verify as v  # noqa: E402 — dynamic loader
    except Exception as e:
        return [], 0.0, (time.time() - t0) * 1000, f'verify import failed: {e}'
    try:
        loaded_rules = [{'rule_id': r['rule_id'], 'rule_text': r['rule_text']} for r in rules]
        result = v.verify_all_global(loaded_rules, last_user, response)
        verdicts_raw = result.get('verdicts', [])
        # Cost estimate (Haiku 4-5: $1/M input, $5/M output). Used only when SDK
        # path doesn't surface real usage; consumers may overwrite if they have
        # actual token counts.
        n_rules = len(loaded_rules)
        in_tokens = 500 * n_rules + len(response) // 3
        out_tokens = 300 * n_rules
        cost_usd = (in_tokens * 1 / 1_000_000) + (out_tokens * 5 / 1_000_000)
        verdicts = []
        for v_dict in verdicts_raw:
            if not isinstance(v_dict, dict):
                continue
            verdicts.append({
                'rule_id': v_dict.get('rule_id'),
                'applicable': v_dict.get('applicable', True),
                'compliant': v_dict.get('compliant', True),
                'judge_confidence': v_dict.get('judge_confidence', 0.85),
                'evidence': v_dict.get('evidence', ''),
                'feedback': v_dict.get('feedback', ''),
            })
        return verdicts, cost_usd, (time.time() - t0) * 1000, None
    except Exception as e:
        return [], 0.0, (time.time() - t0) * 1000, f'SDK call failed: {type(e).__name__}: {str(e)[:200]}'


def _judge_call(rules, last_user, response):
    """Dispatcher: B5_TEST_MOCK_VERDICT first, then CLI default, SDK fallback if env set.

    Returns (verdicts_list, cost_usd, latency_ms, error_str_or_None).
    """
    t0 = time.time()
    if B5_TEST_MOCK_VERDICT:
        try:
            verdicts = json.loads(B5_TEST_MOCK_VERDICT)
            return verdicts, 0.0, (time.time() - t0) * 1000, None
        except Exception as e:
            return [], 0.0, (time.time() - t0) * 1000, f'mock parse error: {e}'
    if B5_USE_SDK:
        return _judge_call_sdk(rules, last_user, response)
    return _judge_call_cli(rules, last_user, response)


def _just_blocked_by_deterministic(within_sec=5):
    """I1 fix: check if deterministic_block just fired within `within_sec` seconds.

    If it was just deterministically blocked, the shadow judge skips — already hard-blocked, no need to log violation again
    (saves ~1 CLI call per blocked Stop).
    """
    if not os.path.exists(COMPLIANCE_LOG):
        return False
    try:
        with open(COMPLIANCE_LOG, errors='ignore') as f:
            tail = f.readlines()[-5:]
        cutoff = (_now() - timedelta(seconds=within_sec)).isoformat()
        for line in reversed(tail):
            try:
                o = json.loads(line)
            except Exception:
                continue
            if (o.get('check_source') == 'deterministic_block'
                and o.get('b5_check', {}).get('deterministic_status') == 'block'
                and o.get('timestamp', '') >= cutoff):
                return True
    except Exception:
        pass
    return False


def evaluate(stdin_data):
    """Main shadow judge logic. Returns (status_str, log_entry_dict).

    Status one of: 'disabled' / 'no_credit' / 'cost_capped' / 'skip_short' /
                   'compliant' / 'violation' / 'no_rules' / 'judge_error'
    """
    session_id = stdin_data.get('session_id', '')
    log_entry = {
        'timestamp': _now().isoformat(),
        'session_id': session_id,
        'event': 'Stop',
        'check_source': 'verify_retry_shadow',
    }

    if B5_SHADOW_DISABLED or not path_config.shadow_enabled():
        log_entry['b5_check'] = {'shadow_judge_status': 'disabled'}
        return 'disabled', log_entry

    # I1 fix: skip shadow judge if deterministic_block just fired this turn
    # (within 5s) — already hard-blocked, no need to invoke judge again
    if _just_blocked_by_deterministic(within_sec=5):
        log_entry['b5_check'] = {'shadow_judge_status': 'skipped_post_deterministic_block'}
        return 'skipped_post_deterministic_block', log_entry

    # Credit gate only matters for SDK path; CLI uses subscription, no credit needed
    if B5_USE_SDK and not ANTHROPIC_CREDIT_OK and not B5_TEST_MOCK_VERDICT:
        log_entry['b5_check'] = {'shadow_judge_status': 'no_credit'}
        return 'no_credit', log_entry

    daily_cost = _read_daily_cost()
    if daily_cost >= B5_DAILY_COST_CAP:
        log_entry['b5_check'] = {
            'shadow_judge_status': 'cost_capped',
            'daily_cost_usd': round(daily_cost, 4),
        }
        return 'cost_capped', log_entry

    response, last_user = _read_response_and_last_user(stdin_data)
    if not response or len(response) < 50:
        log_entry['b5_check'] = {'shadow_judge_status': 'skip_short'}
        return 'skip_short', log_entry

    # Load rule texts
    rules = []
    for atomic_id in SHADOW_RULE_IDS:
        rule_text = _read_rule_text(atomic_id)
        if rule_text:
            rules.append({'rule_id': atomic_id, 'rule_text': rule_text})
    if not rules:
        # Distinct from judge_error: there was nothing to judge (no shadow rules
        # loaded for this project), NOT a judge failure. Common with the empty-seed
        # default — the shadow judge has no rules until you add some.
        log_entry['b5_check'] = {'shadow_judge_status': 'no_rules'}
        return 'no_rules', log_entry

    # Run judge
    verdicts, cost_usd, latency_ms, err = _judge_call(rules, last_user, response)
    if err:
        log_entry['b5_check'] = {
            'shadow_judge_status': 'judge_error',
            'error': err,
            'shadow_judge_latency_ms': round(latency_ms, 2),
        }
        return 'judge_error', log_entry

    if cost_usd > 0:
        _bump_daily_cost(cost_usd)

    # Pre-collect violation rule_ids for the main compliance log:
    # the main entry writes a shadow_violation_rule_ids list so analyze_b5_compliance.py can
    # bucket per-rule directly, without reading b5_shadow_log.jsonl as a second source.
    raw_violations = [v for v in verdicts
                      if v.get('applicable', True) and not v.get('compliant', True)]
    violations = list(raw_violations)
    # Shadow suppression (the maintainer's language false-positive guard) was
    # removed; all raw violations now count. `suppressed` stays empty so the
    # log_entry's shadow_suppressed_rule_ids / shadow_suppression_reasons fields
    # still populate without error.
    suppressed = []

    log_entry['b5_check'] = {
        'shadow_judge_status': 'violation' if violations else 'compliant',
        'shadow_violation_count': len(violations),
        'shadow_violation_rule_ids': [v.get('rule_id') for v in violations if v.get('rule_id')],
        'shadow_suppressed_rule_ids': [s.get('rule_id') for s in suppressed if s.get('rule_id')],
        'shadow_suppression_reasons': suppressed,
        'shadow_judge_latency_ms': round(latency_ms, 2),
        'shadow_judge_cost_usd': round(cost_usd, 6),
        'shadow_judge_model': ('mock' if B5_TEST_MOCK_VERDICT
                               else (B5_JUDGE_MODEL + '/sdk' if B5_USE_SDK
                                     else B5_JUDGE_MODEL + '/cli')),
    }

    # Process violations: confidence threshold + rate limit + alert
    alerted_count = 0
    for v in violations:
        rid = v.get('rule_id', '')
        confidence = v.get('judge_confidence', 0.0)
        if confidence < B5_CONFIDENCE_THRESHOLD:
            # log only, no alert
            _append_shadow_log({
                **log_entry,
                'rule_id': rid,
                'judge_confidence': confidence,
                'evidence': v.get('evidence', ''),
                'feedback': v.get('feedback', ''),
                'alerted': False,
                'reason_no_alert': 'confidence_below_threshold',
            })
            continue
        if _is_rate_limited(rid, hours=RATE_LIMIT_HOURS):
            _append_shadow_log({
                **log_entry,
                'rule_id': rid,
                'judge_confidence': confidence,
                'evidence': v.get('evidence', ''),
                'feedback': v.get('feedback', ''),
                'alerted': False,
                'reason_no_alert': 'rate_limited',
            })
            continue
        # Alert-worthy violation
        rule_desc_m = re.search(r'^description:\s*(.+)$', _read_rule_text(rid) or '', re.MULTILINE)
        rule_desc = rule_desc_m.group(1).strip() if rule_desc_m else ''
        _append_shadow_log({
            **log_entry,
            'rule_id': rid,
            'rule_desc': rule_desc,
            'judge_confidence': confidence,
            'evidence': v.get('evidence', ''),
            'feedback': v.get('feedback', ''),
            'alerted': True,
        })
        alerted_count += 1

    # Refresh alert md (rolling cap)
    if alerted_count > 0:
        _update_alert_md(violations)

    log_entry['b5_check']['shadow_alerted_count'] = alerted_count
    return 'violation' if violations else 'compliant', log_entry


def main():
    """Stop hook entrypoint. ALWAYS exits 0 (shadow mode)."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    status, log_entry = evaluate(data)

    # Append to compliance log; H10 fix chmod 0600 (contains response excerpts)
    try:
        os.makedirs(os.path.dirname(COMPLIANCE_LOG), exist_ok=True)
        with open(COMPLIANCE_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(redaction.sanitize(log_entry), ensure_ascii=False) + '\n')
        path_config.chmod_or_warn(COMPLIANCE_LOG, 0o600)
    except Exception:
        pass

    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Defensive: never block on internal errors
        sys.exit(0)
