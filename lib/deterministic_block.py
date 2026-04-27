#!/usr/bin/env python3
"""Phase B5 Tier A item 1 — Deterministic regex hard-block (Stop hook).

Mirrors verify_compliance.py B3-lite + B4 pattern, but ENFORCES (exit 2 + decision='block')
on 3 deterministic violation classes:

  1. lang-pit-130: chinese_ratio>=0.7 AND has_inline_english_word (with whitelist)
  2. oth-pref-001: extract_paths inside active code blocks contains /tmp/
  3. lang-pref-001 relaxed: chinese_ratio<0.1 AND length>200 AND last user prompt
     does NOT explicitly ask for English / paper context

Design v2.5 §3.1, post 5-round brainstorm battle convergence (round5_critic_final.md GREEN LIGHT).

Defenses:
  - whitelist file `lib/deterministic_block_whitelist.txt` (proper noun bypass)
  - code-block regex scope (prose mention NOT flagged)
  - explicit-English / paper-context bypass for lang-pref-001
  - B5_DETERMINISTIC_DISABLED=1 env opt-out

Per `code-pref-101` (JSON for reward-hack-resistant surfaces) — verdict output is JSON.
Per `wf-pref-027` (versioned 备份) — additive new file, not in-place edit existing.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)
import path_config  # Phase 4.1 解耦中央

B5_DETERMINISTIC_DISABLED = os.environ.get('B5_DETERMINISTIC_DISABLED', '').lower() in ('1', 'true', 'yes')

# 思路 D 安全阀: 同 atomic_id 在同 session 连续触发 >= STREAK_BYPASS 次后, 该 atomic_id
# 在剩余 session 自动放行 (log 警告但不阻断). 防级联 transcript 灾难.
STREAK_BYPASS = int(os.environ.get('B5_STREAK_BYPASS', '3'))

# 代码默认值. Phase 7 简易自适应: 规则 frontmatter `params:` 块若设了则覆盖.
# 用 lambda 让 evaluate_rules 每次 fire 时拿最新值 (用户改 frontmatter 不需重启).
import rule_params  # noqa: E402 — 解耦阈值

def _LANG_PIT_130_MIN_LENGTH():
    return rule_params.get_param('lang-pit-130', 'min_length', 50)

def _LANG_PREF_001_MIN_LENGTH():
    return rule_params.get_param('lang-pref-001', 'min_length', 200)

def _CHINESE_RATIO_PIT_130():
    return rule_params.get_param('lang-pit-130', 'chinese_ratio_threshold', 0.7)

def _CHINESE_RATIO_PREF_001():
    return rule_params.get_param('lang-pref-001', 'chinese_ratio_threshold', 0.1)

# Backward-compat 常量 (旧 import 不破)
LANG_PIT_130_MIN_LENGTH = 50
LANG_PREF_001_MIN_LENGTH = 200
CHINESE_RATIO_PIT_130 = 0.7
CHINESE_RATIO_PREF_001 = 0.1

# Cache whitelist on import for perf
_WHITELIST_CACHE = None


def _load_whitelist():
    """Load whitelist from [全局基础, per-user 增量] 两文件合并. Skip blank + comment lines.

    Returns set of lowercase entries.
    """
    global _WHITELIST_CACHE
    if _WHITELIST_CACHE is not None:
        return _WHITELIST_CACHE
    out = set()
    for path in path_config.get_whitelist_paths():
        try:
            with open(path, errors='ignore') as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith('#'):
                        continue
                    out.add(s.lower())
        except FileNotFoundError:
            pass
    _WHITELIST_CACHE = out
    return out


def chinese_ratio(text):
    """Compute fraction of CJK ideographs vs english letters in text.

    Returns float in [0.0, 1.0]. If neither chinese nor english, returns 0.0.
    """
    if not text:
        return 0.0
    chinese = sum(1 for c in text if '一' <= c <= '鿿')
    english_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    total = chinese + english_letters
    if total == 0:
        return 0.0
    return chinese / total


def _strip_code_blocks(text):
    """Remove fenced code blocks ``` ... ``` (any fence backtick count >= 3) from text.

    Returns prose-only version. Used by has_inline_english_word to scope search to prose.
    """
    if not text:
        return ''
    # Match fences of varying backtick count: 3+ backticks open and close
    # Greedy: assume ``` always closed before next ```
    pattern = re.compile(r'(`{3,})[a-zA-Z0-9_\-]*\n.*?\1', re.DOTALL)
    return pattern.sub('', text)


def _strip_inline_code(text):
    """Remove inline `...` and \\code{...} sequences from text."""
    if not text:
        return ''
    text = re.sub(r'`[^`\n]+`', '', text)
    text = re.sub(r'\\code\{[^}]*\}', '', text)
    return text


def _strip_atomic_ids(text):
    """Remove atomic_id-style refs like 'lang-pref-001' / 'wf-pit-016'."""
    if not text:
        return ''
    return re.sub(r'\b[a-z]+-[a-z]+-\d+\b', '', text)


def has_inline_english_word(text, whitelist=None):
    """Detect inline English word in mostly-Chinese reply (lang-pit-130 helper).

    Excludes:
      - Words inside fenced code blocks ``` ... ```
      - Words inside backtick inline code `word`
      - Words inside \\code{...}
      - URLs / href
      - Cited atomic_ids (e.g., lang-pref-001)
      - Whitelisted proper nouns (case-insensitive)

    Returns True if any non-whitelisted English word (>= 3 chars) found in prose.
    """
    if whitelist is None:
        whitelist = _load_whitelist()

    # Strip non-prose content
    prose = _strip_code_blocks(text)
    prose = _strip_inline_code(prose)
    prose = _strip_atomic_ids(prose)
    # Strip URLs (http://... https://... www....)
    prose = re.sub(r'https?://\S+', '', prose)
    prose = re.sub(r'www\.\S+', '', prose)
    # Strip \href{...}
    prose = re.sub(r'\\href\{[^}]*\}', '', prose)

    # Find all English word sequences (3+ chars), word-boundary
    matches = re.findall(r'\b[a-zA-Z]{3,}\b', prose)
    for m in matches:
        if m.lower() in whitelist:
            continue
        return True
    return False


# Path detection regex used by has_active_code_block_with_tmp_path.
# Matches /tmp/<something> on a code line that is NOT a comment.
_TMP_IN_CODE = re.compile(r'^[^#\n]*?/tmp/', re.MULTILINE)


def has_active_code_block_with_tmp_path(text):
    """Detect active /tmp/ usage in fenced code blocks (oth-pref-001 helper).

    Only flags lines inside ```python|bash|sh|json|yaml|toml|sql|js|ts|md|...```
    or unlabeled ```. Comment lines (# or // prefix) are skipped.
    Prose mentions of /tmp/ are NOT flagged.

    Returns True if any active code block has non-comment line containing /tmp/.
    """
    if not text or '/tmp/' not in text:
        return False
    # Find all fenced blocks (fence count >= 3, lang-tagged or not)
    pattern = re.compile(r'(`{3,})([a-zA-Z0-9_\-]*)\n(.*?)\1', re.DOTALL)
    for m in pattern.finditer(text):
        block = m.group(3)
        # Search for non-comment lines containing /tmp/
        for line in block.split('\n'):
            stripped = line.lstrip()
            if not stripped:
                continue
            # Skip comment lines (Python/bash style #, JS/Java style //)
            if stripped.startswith('#') or stripped.startswith('//'):
                continue
            if '/tmp/' in line:
                return True
    return False


# Explicit english request keywords (case insensitive)
_EXPLICIT_ENGLISH_KW = re.compile(
    r'\b(in english|reply in english|english only|translate to english|please .{0,20}english|use english|write in english|英文|please.{0,20}english|英语|in en\b)',
    re.IGNORECASE,
)
# Paper context bypass keywords
_PAPER_CTX_KW = re.compile(
    r'\b(paper|appendix|rebuttal|camera-?ready|NeurIPS|ICLR|ACL|AAAI|reviewer|abstract|section [3-9]|sec [3-9]|figure caption|table caption)\b',
    re.IGNORECASE,
)


def last_user_prompt_explicit_english_request(transcript_lines):
    """Read transcript reverse, find last user-text content. Check english/paper bypass.

    Args:
      transcript_lines: iterable of JSONL lines (str), each one a transcript entry.

    Returns True if last user prompt explicitly asks English OR is paper context.
    """
    last_user_text = None
    # transcript_lines may be list or generator; iterate in reverse
    lines = list(transcript_lines) if not isinstance(transcript_lines, list) else transcript_lines
    for line in reversed(lines):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get('type') != 'user':
            continue
        msg = o.get('message') or {}
        content = msg.get('content')
        if isinstance(content, str):
            last_user_text = content
            break
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'text':
                    last_user_text = item.get('text', '')
                    break
            if last_user_text is not None:
                break
        elif content is not None:
            last_user_text = str(content)
            break

    if not last_user_text:
        return False

    if _EXPLICIT_ENGLISH_KW.search(last_user_text):
        return True
    if _PAPER_CTX_KW.search(last_user_text):
        return True
    return False


def _extract_response_and_transcript_lines(stdin_data):
    """From Stop hook stdin JSON {session_id, transcript_path, ...},
    extract (response_text, transcript_lines).

    response_text = last assistant message text.
    transcript_lines = full JSONL lines (for last_user_prompt detection).
    Returns (response_text, transcript_lines) or ('', []).
    """
    transcript_path = stdin_data.get('transcript_path')
    if not transcript_path or not os.path.exists(transcript_path):
        return '', []
    try:
        with open(transcript_path, errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return '', []
    # Find last assistant text
    response_text = ''
    for line in reversed(lines[-200:]):
        try:
            o = json.loads(line)
            if o.get('type') == 'assistant':
                msg = o.get('message') or {}
                content = msg.get('content', [])
                if isinstance(content, str):
                    response_text = content
                    break
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'text':
                            response_text = item.get('text', '')
                            break
                if response_text:
                    break
        except Exception:
            continue
    return response_text, lines


def evaluate_rules(response, transcript_lines):
    """Evaluate 3 deterministic rules. Returns list of violation dicts.

    Each violation dict: {rule_id, reason, evidence_excerpt}
    """
    violations = []
    if not response:
        return violations

    cr = chinese_ratio(response)
    n = len(response)

    # Rule 1: lang-pit-130 (chinese majority + inline english word) — Phase 7 阈值从 frontmatter 读
    if cr >= _CHINESE_RATIO_PIT_130() and n > _LANG_PIT_130_MIN_LENGTH():
        if has_inline_english_word(response):
            # Find first 3 non-whitelisted english words for evidence
            wl = _load_whitelist()
            prose = _strip_code_blocks(response)
            prose = _strip_inline_code(prose)
            prose = _strip_atomic_ids(prose)
            words = re.findall(r'\b[a-zA-Z]{3,}\b', prose)
            offenders = [w for w in words if w.lower() not in wl][:3]
            violations.append({
                'rule_id': 'lang-pit-130',
                'reason': '中文 reply 混入 inline 普通英文词',
                'evidence_excerpt': f'chinese_ratio={cr:.2f}, found english words: {offenders}',
            })

    # Rule 2: oth-pref-001 (path /tmp/ in active code block)
    if has_active_code_block_with_tmp_path(response):
        # Snip first /tmp/ occurrence in code block for evidence
        m = re.search(r'```[a-zA-Z0-9_\-]*\n.{0,500}?/tmp/[^\n]*', response, re.DOTALL)
        excerpt = m.group(0)[-200:] if m else '/tmp/...'
        violations.append({
            'rule_id': 'oth-pref-001',
            'reason': 'active code block 含 /tmp/ path (per `tool-pit-130` 不持久)',
            'evidence_excerpt': excerpt,
        })

    # Rule 3: lang-pref-001 relaxed (全英文长 reply + 用户没明显要 english) — Phase 7 阈值从 frontmatter 读
    if n > _LANG_PREF_001_MIN_LENGTH() and cr < _CHINESE_RATIO_PREF_001():
        if not last_user_prompt_explicit_english_request(transcript_lines):
            violations.append({
                'rule_id': 'lang-pref-001',
                'reason': 'response 几乎全英文但 user 上 prompt 没明 explicit ask English / paper context',
                'evidence_excerpt': f'chinese_ratio={cr:.3f}, length={n}',
            })

    return violations


def build_block_reason(violations):
    """Build block reason text. v2 (post 续写重复 brainstorm 思路 A+C+F):
    显式禁令(不道歉/不重述/不铺垫) + 只列触发的 rule + 强制 `[修正]` 前缀.
    """
    if not violations:
        return ''
    # 思路 C: 只列触发的 rule, 不列全部 3 条
    fix_hints = {
        'lang-pit-130': '把英文借词换中文 (stub→占位代码 / merge→合并 / drift→漂移 / ship→上线)',
        'oth-pref-001': '改 /tmp/ → 项目内 state/runtime/ 或 .claude/preference-tracker-state/',
        'lang-pref-001': '改中文回复. 若给外部 reviewer, 在下条 prompt 明示 in english 触发 bypass',
    }
    triggered_lines = []
    for v in violations:
        rid = v['rule_id']
        triggered_lines.append(
            f"  • [{rid}] {v['evidence_excerpt'][:120]}\n"
            f"    → {fix_hints.get(rid, v['reason'])}"
        )
    triggered = '\n'.join(triggered_lines)

    # 思路 A+F: 禁令清单 + 强制前缀
    reason = (
        f"⛔ {', '.join(v['rule_id'] for v in violations)} 触发\n\n"
        f"{triggered}\n\n"
        f"🔧 续写规则 (严格遵守, 减少 transcript 回声):\n"
        f"  ❌ 不要道歉 (\"抱歉/对不起/刚才\")\n"
        f"  ❌ 不要重述上文 (用户已看到原回复)\n"
        f"  ❌ 不要解释规则 (用户已看到本提示)\n"
        f"  ❌ 不要写 \"补充修正/我注意到/上文我违反了\" 类铺垫\n"
        f"  ✅ 直接以 `[修正]` 开头, 后接修正后的内容片段, 越短越好\n\n"
        f"Override: env `B5_DETERMINISTIC_DISABLED=1` 全关; `B5_STREAK_BYPASS=N` 调连续违规放行阈值 (默认 3)."
    )
    return reason


def _streak_path(session_id):
    """Per-session streak counter file path."""
    sid = re.sub(r'[^a-zA-Z0-9_-]', '_', session_id or 'unknown')[:64]
    return os.path.join(path_config.get_streak_dir(), f'{sid}.json')


def _load_streak(session_id):
    """Load {atomic_id: count} for current session."""
    path = _streak_path(session_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _bump_streak(session_id, rule_ids):
    """Increment streak counter for given rule_ids. Returns updated dict."""
    os.makedirs(path_config.get_streak_dir(), exist_ok=True)
    streak = _load_streak(session_id)
    for rid in rule_ids:
        streak[rid] = streak.get(rid, 0) + 1
    try:
        with open(_streak_path(session_id), 'w') as f:
            json.dump(streak, f)
    except Exception:
        pass
    return streak


def _filter_bypass_streaked(violations, streak):
    """思路 D: 同 atomic_id streak >= STREAK_BYPASS → 该规则放行 (drop from violations).
    Returns (filtered_violations, bypassed_rule_ids).
    """
    filtered = []
    bypassed = []
    for v in violations:
        rid = v['rule_id']
        if streak.get(rid, 0) >= STREAK_BYPASS:
            bypassed.append(rid)
        else:
            filtered.append(v)
    return filtered, bypassed


def log_check(session_id, status, violations, latency_ms):
    """Append b5_check entry to compliance_log.jsonl."""
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'session_id': session_id,
        'event': 'Stop',
        'check_source': 'deterministic_block',
        'b5_check': {
            'deterministic_status': status,  # 'pass' | 'block' | 'disabled'
            'deterministic_violations': [v['rule_id'] for v in violations],
            'deterministic_latency_ms': round(latency_ms, 2),
        },
    }
    try:
        log_path = path_config.get_compliance_log_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


def main():
    """Stop hook entrypoint. Read stdin JSON, evaluate rules, exit 2 + JSON if block."""
    import time
    t0 = time.time()
    try:
        data = json.load(sys.stdin)
    except Exception:
        # Malformed stdin: don't block
        sys.exit(0)

    session_id = data.get('session_id', '')

    if B5_DETERMINISTIC_DISABLED:
        log_check(session_id, 'disabled', [], (time.time() - t0) * 1000)
        sys.exit(0)

    response, transcript_lines = _extract_response_and_transcript_lines(data)
    if not response:
        log_check(session_id, 'pass', [], (time.time() - t0) * 1000)
        sys.exit(0)

    violations = evaluate_rules(response, transcript_lines)

    # 思路 D: 安全阀 — 同 rule 连续 STREAK_BYPASS 次后该 rule 放行
    if violations:
        streak = _load_streak(session_id)
        violations, bypassed = _filter_bypass_streaked(violations, streak)
        if bypassed:
            # log 这些放行的 rule (不阻断, 但记录灾难 escape 事件)
            log_check(session_id, 'streak_bypass', [{'rule_id': r, 'reason': 'streak >= bypass threshold', 'evidence_excerpt': f'streak={streak.get(r, 0)}'} for r in bypassed], (time.time() - t0) * 1000)

    latency_ms = (time.time() - t0) * 1000

    if violations:
        # bump streak only for rules that actually fired this turn (not bypassed)
        _bump_streak(session_id, [v['rule_id'] for v in violations])
        log_check(session_id, 'block', violations, latency_ms)
        decision = {
            'decision': 'block',
            'reason': build_block_reason(violations),
        }
        print(json.dumps(decision, ensure_ascii=False))
        sys.exit(2)

    log_check(session_id, 'pass', [], latency_ms)
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Defensive: never block on internal errors
        sys.exit(0)
