#!/usr/bin/env python3
"""Tellonce memory retrieve + inject.

Reads UserPromptSubmit JSON from stdin, picks relevant atomic_ids based on
B5_RETRIEVE_BACKEND env var:
  - 'progressive' (default): progressive-disclosure index. Scans the memory
    dir and injects a one-line index of the saved rules every turn, letting
    the main model judge which apply. When the library exceeds PROGRESSIVE_MAX
    (default 50, configurable via B5_PROGRESSIVE_MAX / config
    `progressive_max`, 0 = no cap), tier-1 rules are pinned when they fit
    under the cap (if tier-1 alone overflows it, the whole library rotates)
    and the remainder rotates in across turns via a persisted cursor; the
    truncation is disclosed in the injected block. Zero LLM calls, zero keyword matching,
    zero CLI cold-start latency. Also fixes Copilot's SessionStart "0 rules"
    gap, since it does not depend on fingerprints.yaml priority tags.
  - 'cli': semantic match via a small model (claude haiku for CC,
    gpt-5.4-mini for codex). Costs 1-2s latency per UserPromptSubmit but
    uses the same subscription auth path as the runtime, no API key needed
    and no trigger-keyword maintenance. B5_RETRIEVE_BACKEND=cli reproduces
    the pre-progressive default.
  - 'keyword': legacy fingerprints.yaml literal/regex matcher. Cheap and
    instant but only as good as the trigger lists in fingerprints.yaml.
  - 'api': OpenAI-compatible HTTP endpoint (see backend notes below).

CLI dispatch is governed by B5_RETRIEVE_CLI (default per platform via
pt_platform.RETRIEVE_CLI_DEFAULT):
  - 'copilot': `copilot -p` (no --model; copilot picks its own model)
  - 'claude':  `claude -p --model <model>`
  - 'codex':   `codex exec --ephemeral ... -m <model>`

Default model is derived from B5_RETRIEVE_CLI when B5_RETRIEVE_MODEL is unset:
  claude → claude-haiku-4-5, codex → gpt-5.4-mini, copilot → (none / omitted).

Recursion guard: when retrieve_inject invokes the CLI, it sets
B5_RETRIEVE_RECURSION_GUARD=1 in the child env. The hook scripts in
<PLUGIN_ROOT>/hooks/memory-retrieve-inject.sh and
~/.codex/skills/tellonce/hooks/userpromptsubmit-retrieve-inject.sh
honor this flag and exit 0 immediately, so a nested CLI session doesn't
re-trigger the retrieve hook and loop.

Emits JSON with hookSpecificOutput.additionalContext naming the matched atomic_ids.
Non-destructive: any failure → exit 0 silently (no block).
"""
from __future__ import annotations

import hashlib, json, sys, re, os, glob, subprocess, tempfile, time

import sys as _sys
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_sys.path.insert(0, _LIB_DIR)
import path_config
import pt_platform  # platform-specific values (this variant)
import memory_store
import memory_upsert
import transcript_adapter

FP_YAML = os.path.join(_LIB_DIR, 'fingerprints.yaml')
# Private overlay: the shipped fingerprints.yaml is empty by default. Users keep
# their own rules in fingerprints.user.yaml (gitignored), merged at load.
FP_USER_YAML = os.path.join(_LIB_DIR, 'fingerprints.user.yaml')
MEMORY_DIR = path_config.get_memory_dir()
MAX_SHOW = 10
# progressive backend: default cap on rules injected per turn. The effective
# value (PROGRESSIVE_MAX below, after _USER_CONFIG loads) is configurable via
# B5_PROGRESSIVE_MAX / config `progressive_max`; 0 disables the cap. When the
# library exceeds the cap, tier-1 rules are always included and the remainder
# rotates in across turns (persisted cursor) so no rule is permanently starved.
_PROGRESSIVE_MAX_DEFAULT = 50
PROMPT_TRUNCATE = 4000


def _load_fingerprints():
    """Return the merged `fingerprints` dict: shipped fingerprints.yaml plus the
    optional private fingerprints.user.yaml overlay (user entries win). Requires
    PyYAML; returns {} if unavailable or both files missing/unreadable."""
    try:
        import yaml
    except ImportError:
        return {}
    merged = {}
    for path in (FP_YAML, FP_USER_YAML):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            fps = (data or {}).get('fingerprints', {}) or {}
            if isinstance(fps, dict):
                merged.update(fps)
        except Exception:
            continue
    return merged


# Persist retrieve defaults in
# ~/.tellonce.config.json so the user doesn't have to remember
# `export B5_RETRIEVE_*` for every shell. Precedence per setting:
#   1. process env (B5_RETRIEVE_*)        — highest
#   2. ~/.tellonce.config.json   — persistent default
#   3. built-in default                    — lowest
# Plus: if config has retrieve_env_file, we read that .env file (KEY=VALUE)
# and inject DEEPINFRA_API_KEY / OPENROUTER_API_KEY / etc. into os.environ
# so the API backend can find them without `source .env` in every shell.
_USER_CONFIG_PATH = os.path.expanduser('~/.tellonce.config.json')


def _load_user_config() -> dict:
    if not os.path.exists(_USER_CONFIG_PATH):
        return {}
    try:
        with open(_USER_CONFIG_PATH, encoding='utf-8-sig') as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _config_setting(env_key: str, config_key: str, cfg: dict, default: str = '') -> str:
    if env_key.startswith('B5_'):
        val = path_config.pt_env(env_key[3:])
    else:
        val = os.environ.get(env_key)
    if val:
        return val
    cv = cfg.get(config_key)
    if isinstance(cv, str) and cv:
        return cv
    return default


def _autoload_env_file_from_config(cfg: dict) -> None:
    """If config sets retrieve_env_file, parse simple KEY=VALUE lines from
    that file and inject ANY *_API_KEY entries into os.environ — but never
    overwrite an env var the user already has set. Idempotent + best-effort.
    """
    env_file = cfg.get('retrieve_env_file')
    if not isinstance(env_file, str) or not os.path.isfile(env_file):
        return
    try:
        with open(env_file, encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if not k or not v:
                    continue
                # Enforce the documented contract: only *_API_KEY entries are
                # loaded. The api backend is the sole consumer of this file;
                # slurping EVERY key would drag unrelated secrets (DB URLs,
                # tokens) from a shared .env into the hook environment.
                if not k.upper().endswith('_API_KEY'):
                    continue
                if k in os.environ:
                    continue  # respect existing env
                os.environ[k] = v
    except OSError:
        pass


_USER_CONFIG = _load_user_config()
_autoload_env_file_from_config(_USER_CONFIG)

# Backend default is 'progressive': inject a one-line index of the saved
# rules each turn (tier-1 first, remainder rotating under PROGRESSIVE_MAX)
# and let the main model self-select. Zero LLM, zero keyword,
# zero CLI cold-start. 'cli' (small-model semantic match) is the legacy
# default — set B5_RETRIEVE_BACKEND=cli to reproduce it. 'api' backend targets
# OpenAI-compatible HTTP endpoints (DeepInfra / OpenRouter / etc.). Settings can
# come from ~/.tellonce.config.json so the user doesn't have to
# `export B5_RETRIEVE_*` in every shell.
RETRIEVE_BACKEND = _config_setting('B5_RETRIEVE_BACKEND', 'retrieve_backend', _USER_CONFIG, 'progressive').lower()
# Effective per-turn cap for the progressive backend (see the note at
# _PROGRESSIVE_MAX_DEFAULT). Config JSON may hold it as an int or a string;
# _config_setting only passes strings through, so check the raw config value
# too. Falls back to the default on any non-integer value.
def _resolve_progressive_max() -> int:
    raw = _config_setting('B5_PROGRESSIVE_MAX', 'progressive_max', _USER_CONFIG, '')
    if not raw and isinstance(_USER_CONFIG.get('progressive_max'), (int, float)):
        raw = str(_USER_CONFIG.get('progressive_max'))
    try:
        return int(str(raw).strip()) if raw else _PROGRESSIVE_MAX_DEFAULT
    except (TypeError, ValueError):
        return _PROGRESSIVE_MAX_DEFAULT


PROGRESSIVE_MAX = _resolve_progressive_max()
# Backwards compat: 'haiku' alias 'cli'.
if RETRIEVE_BACKEND == 'haiku':
    RETRIEVE_BACKEND = 'cli'
RETRIEVE_CLI = _config_setting('B5_RETRIEVE_CLI', 'retrieve_cli', _USER_CONFIG, pt_platform.RETRIEVE_CLI_DEFAULT).lower()
_DEFAULT_MODEL_BY_CLI = {
    'copilot': '',
    'claude': 'claude-haiku-4-5',
    'codex': 'gpt-5.4-mini',
}
# API backend selectors
RETRIEVE_API_PROVIDER = _config_setting('B5_RETRIEVE_API_PROVIDER', 'retrieve_api_provider', _USER_CONFIG, 'openrouter').lower()
_DEFAULT_MODEL_BY_API = {
    'openrouter': 'deepseek/deepseek-v4-flash',
    'deepinfra': 'deepseek-ai/DeepSeek-V4-Flash',
}
if RETRIEVE_BACKEND == 'api':
    _backend_default_model = _DEFAULT_MODEL_BY_API.get(RETRIEVE_API_PROVIDER, '')
else:
    _backend_default_model = _DEFAULT_MODEL_BY_CLI.get(RETRIEVE_CLI, 'claude-haiku-4-5')
RETRIEVE_MODEL = _config_setting('B5_RETRIEVE_MODEL', 'retrieve_model', _USER_CONFIG, _backend_default_model)
try:
    RETRIEVE_TIMEOUT_S = int(str(_config_setting('B5_RETRIEVE_TIMEOUT', 'retrieve_timeout_s', _USER_CONFIG, '12')).strip())
except (TypeError, ValueError):
    RETRIEVE_TIMEOUT_S = 12  # malformed env/config value → default, never crash at import
RETRIEVE_HAIKU_PROMPT_BUDGET = 2000  # truncate user prompt
RETRIEVE_HAIKU_RULE_LIMIT = 40       # max rules to show backend

# Recursion guard: when retrieve_inject calls the CLI, it sets this env.
# Hook scripts check it and exit 0 immediately so a nested session doesn't
# re-fire retrieve and loop.
RETRIEVE_RECURSION_GUARD = os.environ.get('B5_RETRIEVE_RECURSION_GUARD') == '1'


_RULE_INDEX = None
_RULE_DESC_INDEX = None
_RULE_META_INDEX = None

# Leading '---' fenced YAML block. DOTALL so the block may span lines; the
# closing fence must start a line.
_FRONTMATTER_RE = re.compile(r'\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)', re.DOTALL)
_ACTIVE_INDEX_FILENAME = '.tellonce-active.json'


def _frontmatter_of(content: str) -> str:
    """Return the YAML frontmatter block (text between the leading `---`
    fences), or the whole content when there is no frontmatter (legacy
    tolerance: earlier versions scanned the full file, and promote paths
    always write a proper frontmatter anyway).

    Scoping the field regexes to this block matters twice over:
      - a rule whose *body* happens to mention `atomic_id: foo` must not be
        indexed under foo;
      - Claude Code's memory normalizer rewrites agent-written rule files
        with the tellonce fields nested (indented) under `metadata:`, so the
        field regexes tolerate leading whitespace instead of anchoring to
        column 0 — a column-0 anchor silently mis-tiers those rules.
    """
    m = _FRONTMATTER_RE.match(content)
    return m.group(1) if m else content


def _frontmatter_scalar(frontmatter: str, field: str) -> str:
    """Read one scalar without allowing YAML whitespace to cross a newline."""
    match = re.search(
        rf'^[ \t]*{re.escape(field)}:[ \t]*(.*?)[ \t]*$',
        frontmatter,
        re.MULTILINE,
    )
    if not match:
        return ''
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1].strip()
    return value


def _load_active_projection():
    """Return the authoritative active-rule projection, or None if unavailable."""
    db_path = os.path.join(MEMORY_DIR, memory_store.DB_FILENAME)
    if not os.path.isfile(db_path):
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rule_columns = {
                row['name']
                for row in conn.execute('PRAGMA table_info(rules)').fetchall()
            }
            anchor_select = (
                'r.scope_anchor'
                if 'scope_anchor' in rule_columns
                else "'' AS scope_anchor"
            )
            db_active = [
                dict(item)
                for item in conn.execute(
                    f"""
                    SELECT r.atomic_id, r.type, r.domain, r.scope,
                           {anchor_select},
                           v.description, v.rule_text, v.condition,
                           v.confidence, v.applies_when,
                           v.does_not_apply_when, v.content_hash
                    FROM rules r
                    JOIN rule_versions v
                      ON v.atomic_id=r.atomic_id
                     AND v.revision=r.current_revision
                    WHERE r.status='active' AND r.superseded_by IS NULL
                    ORDER BY r.domain, r.type, r.atomic_id
                    """
                ).fetchall()
            ]
        finally:
            conn.close()
        # SQLite is the sole authority. Reading the canonical rows directly
        # prevents a project-authored projection from replacing rule content
        # while reusing valid IDs.
        return db_active
    except Exception:
        # A present-but-unreadable canonical database is not a legacy store.
        # Fail closed instead of trusting mutable Markdown projections.
        return []


def _memory_scan_dirs():
    """Use canonical projections first; fall back to pre-unification stores."""
    dirs = [MEMORY_DIR]
    if _load_active_projection() is None:
        try:
            dirs.extend(path_config.get_legacy_memory_dirs())
        except Exception:
            pass
    seen = set()
    result = []
    for directory in dirs:
        normalized = os.path.normcase(os.path.abspath(directory))
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(directory)
    return result


def _build_index():
    """One-pass scan of memory dir → {atomic_id: (applies_when, condition)}.
    Also caches description into _RULE_DESC_INDEX so render can fall back
    to the .md frontmatter when fingerprints.yaml has no `desc` for the
    rule (UX: rules that exist only in memory dir used to
    render with empty desc in the additionalContext block)."""
    global _RULE_INDEX, _RULE_DESC_INDEX, _RULE_META_INDEX
    if _RULE_INDEX is not None:
        return _RULE_INDEX
    idx = {}
    desc_idx: dict[str, str] = {}
    meta_idx: dict[str, dict[str, str]] = {}
    projected = _load_active_projection()
    if projected is not None:
        for item in projected:
            atomic_id = str(item.get('atomic_id', '')).strip()
            if not atomic_id:
                continue
            idx[atomic_id] = (
                str(item.get('applies_when', '') or '').strip(),
                str(item.get('condition', '') or '').strip(),
            )
            desc_idx[atomic_id] = str(
                item.get('description') or item.get('rule_text') or ''
            ).strip()
            meta_idx[atomic_id] = {
                'scope': str(item.get('scope') or '').strip(),
                'scope_anchor': str(item.get('scope_anchor') or '').strip(),
                'does_not_apply_when': str(
                    item.get('does_not_apply_when') or ''
                ).strip(),
            }
        _RULE_INDEX = idx
        _RULE_DESC_INDEX = desc_idx
        _RULE_META_INDEX = meta_idx
        return idx
    try:
        paths = []
        for directory in _memory_scan_dirs():
            paths.extend(glob.glob(os.path.join(directory, '*.md')))
        for path in paths:
            basename = os.path.basename(path)
            if basename == 'MEMORY.md' or basename.startswith('_archived_'):
                continue
            try:
                # utf-8-sig: a BOM would defeat the \A--- frontmatter fence.
                with open(path, encoding='utf-8-sig', errors='ignore') as f:
                    c = f.read()
            except Exception:
                continue
            fm = _frontmatter_of(c)
            atomic_id = _frontmatter_scalar(fm, 'atomic_id')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', atomic_id):
                continue
            status = _frontmatter_scalar(fm, 'status')
            superseded_by = _frontmatter_scalar(fm, 'superseded_by')
            if status and status.lower() != 'active':
                continue
            if superseded_by.lower() not in {'', 'null', 'none', '[]', '[ ]'}:
                continue
            if atomic_id in idx:
                continue
            idx[atomic_id] = (
                _frontmatter_scalar(fm, 'applies_when'),
                _frontmatter_scalar(fm, 'condition'),
            )
            description = _frontmatter_scalar(fm, 'description')
            if description:
                desc_idx[atomic_id] = description
            meta_idx[atomic_id] = {
                'scope': _frontmatter_scalar(fm, 'scope'),
                'scope_anchor': _frontmatter_scalar(fm, 'scope_anchor'),
                'does_not_apply_when': _frontmatter_scalar(
                    fm,
                    'does_not_apply_when',
                ),
            }
    except Exception:
        pass
    _RULE_INDEX = idx
    _RULE_DESC_INDEX = desc_idx
    _RULE_META_INDEX = meta_idx
    return idx


def read_rule_applicability(atomic_id):
    return _build_index().get(atomic_id, ('', ''))


def read_rule_description(atomic_id: str) -> str:
    """Return the .md frontmatter `description:` for `atomic_id`, or '' if
    the rule isn't in memory dir or has no description field."""
    if _RULE_DESC_INDEX is None:
        _build_index()
    return (_RULE_DESC_INDEX or {}).get(atomic_id, '')


def read_rule_metadata(atomic_id: str) -> dict[str, str]:
    if _RULE_META_INDEX is None:
        _build_index()
    return (_RULE_META_INDEX or {}).get(
        atomic_id,
        {'scope': '', 'scope_anchor': '', 'does_not_apply_when': ''},
    )


# When a rule has no explicit priority_tier, derive an ordering tier from its
# confidence field so higher-confidence rules sort first. The real frontmatter
# schema (SKILL.md) uses `confidence:`, not `priority_tier:`.
_CONFIDENCE_TIER = {'high': 1, 'medium': 2, 'low': 3}


def _collect_all_rules():
    """Scan the memory dir and return EVERY rule as a dict for the progressive
    backend: {id, tier, desc, when, scope, except, path}.

    Pure regex over the .md frontmatter — no PyYAML, no LLM call, no keyword
    matching. The progressive backend injects a one-line index of these every
    turn (tier-1 always; when the library exceeds PROGRESSIVE_MAX the rest
    rotate in across turns — see _select_progressive_rules) and lets the model
    judge which apply, instead of pre-filtering by prompt. Returns [] on any
    failure so the caller emits nothing (never blocks).

    Files without an `atomic_id:` in their frontmatter are skipped on purpose:
    those are the host runtime's own memory notes (e.g. Claude Code loads them
    each session via its MEMORY.md index), so re-injecting them here would
    duplicate context. tellonce indexes only its own atomic rules.

    Schema-tolerant across the rule sources that actually ship, which do NOT use
    a single uniform frontmatter:
      - CC / Copilot (SKILL.md schema): description + condition + confidence
      - Codex promote.py (codex-memory-v1): rule_text + condition + applies_when
        + confidence  (no `description`, no `priority_tier`)
      - richer seed rules: description + applies_when + priority_tier
    So the one-liner falls back description->rule_text, the applicability gate
    falls back applies_when->condition, and ordering falls back
    priority_tier->confidence. Tuning only to the seed schema would render real
    promoted rules with an empty description and an inert tier sort."""
    rules = []
    projected = _load_active_projection()
    if projected is not None:
        for item in projected:
            confidence = str(item.get('confidence', '')).lower()
            rules.append({
                'id': str(item.get('atomic_id', '')),
                'tier': _CONFIDENCE_TIER.get(confidence, 3),
                'desc': str(item.get('description') or item.get('rule_text') or '').strip(),
                'when': str(item.get('applies_when') or item.get('condition') or '').strip(),
                'scope': str(item.get('scope') or '').strip(),
                'scope_anchor': str(item.get('scope_anchor') or '').strip(),
                'except': str(item.get('does_not_apply_when') or '').strip(),
                'path': os.path.join(MEMORY_DIR, f"{item.get('atomic_id', '')}.md"),
            })
        rules.sort(key=lambda r: (r['tier'], r['id']))
        return rules
    try:
        paths = []
        for directory in _memory_scan_dirs():
            paths.extend(glob.glob(os.path.join(directory, '*.md')))
    except Exception:
        return rules
    seen_rule_ids = {}
    for path in paths:
        basename = os.path.basename(path)
        if basename == 'MEMORY.md' or basename.startswith('_archived_'):
            continue
        try:
            # utf-8-sig: a BOM would defeat the \A--- frontmatter fence.
            with open(path, encoding='utf-8-sig', errors='ignore') as f:
                c = f.read()
        except Exception:
            continue
        # Field regexes are scoped to the frontmatter block and tolerate
        # leading whitespace (nested-under-`metadata:` schema) — see
        # _frontmatter_of. Capture the whole id slug (promote.py allows
        # [A-Za-z0-9_-]); a `\d+` tail would truncate ids like
        # `wf-pref-3sync` to `wf-pref-3`.
        fm = _frontmatter_of(c)
        atomic_id = _frontmatter_scalar(fm, 'atomic_id')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', atomic_id):
            continue
        status = _frontmatter_scalar(fm, 'status')
        superseded_by = _frontmatter_scalar(fm, 'superseded_by')
        if status and status.lower() != 'active':
            continue
        if superseded_by.lower() not in {'', 'null', 'none', '[]', '[ ]'}:
            continue
        description = (
            _frontmatter_scalar(fm, 'description')
            or _frontmatter_scalar(fm, 'rule_text')
        )
        applies_when = (
            _frontmatter_scalar(fm, 'applies_when')
            or _frontmatter_scalar(fm, 'condition')
        )
        scope = _frontmatter_scalar(fm, 'scope')
        scope_anchor = _frontmatter_scalar(fm, 'scope_anchor')
        exception = _frontmatter_scalar(fm, 'does_not_apply_when')
        tier_text = _frontmatter_scalar(fm, 'priority_tier')
        if tier_text.isdigit():
            tier = int(tier_text)
        else:
            confidence = _frontmatter_scalar(fm, 'confidence').lower()
            tier = _CONFIDENCE_TIER.get(confidence, 3)
        signature = hashlib.sha256(
            json.dumps(
                {
                    'description': description,
                    'when': applies_when,
                    'scope': scope,
                    'scope_anchor': scope_anchor,
                    'except': exception,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode('utf-8')
        ).hexdigest()
        if atomic_id in seen_rule_ids:
            if seen_rule_ids[atomic_id] == signature:
                continue
            base_id = f'{atomic_id}-legacy-{signature[:8]}'
            atomic_id = base_id
            suffix = 2
            while atomic_id in seen_rule_ids:
                atomic_id = f'{base_id}-{suffix}'
                suffix += 1
        seen_rule_ids[atomic_id] = signature
        rules.append({
            'id': atomic_id,
            'tier': tier,
            'desc': description,
            'when': applies_when,
            'scope': scope,
            'scope_anchor': scope_anchor,
            'except': exception,
            'path': path,
        })
    # Stable order: tier ascending (1 = highest), then id.
    rules.sort(key=lambda r: (r['tier'], r['id']))
    return rules


def _progressive_cursor_path() -> str:
    return os.path.join(path_config.get_state_dir(), 'progressive_cursor.json')


def _load_progressive_cursor() -> int:
    """Best-effort read of the rotation cursor; any failure → 0 (rotation
    degrades to a stable window, injection itself never blocks)."""
    try:
        with open(_progressive_cursor_path(), encoding='utf-8') as f:
            cur = json.load(f).get('cursor', 0)
        return cur if isinstance(cur, int) and cur >= 0 else 0
    except Exception:
        return 0


def _save_progressive_cursor(cursor: int) -> None:
    """Atomic write (tmp + os.replace): concurrent sessions share this file,
    and a reader hitting a half-written JSON would snap the rotation window
    back to 0. A lost-update race between two sessions is accepted — it only
    repeats a window, never corrupts."""
    try:
        path = _progressive_cursor_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.progressive_cursor.', dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'cursor': cursor}, f)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        path_config.chmod_or_warn(path, 0o600)
    except Exception:
        pass


def _select_progressive_rules(rules):
    """Pick which rules go into this turn's index when the library exceeds
    PROGRESSIVE_MAX (0 or negative = no cap → all rules).
    Returns (selected_rules, tier1_pinned: bool).

    Tier-1 rules are always included when they fit under the cap (they are
    the user's hardest preferences). The remaining slots are filled from the
    non-tier-1 rules by a cursor persisted in the state dir, so over
    successive turns every rule cycles into view — a plain `rules[:cap]`
    would permanently starve everything sorted after the cap. If tier-1
    alone overflows the cap, the cursor rotates over the whole library
    instead (tier1_pinned=False), so nothing is starved there either; the
    render layer words the disclosure per branch."""
    if PROGRESSIVE_MAX <= 0 or len(rules) <= PROGRESSIVE_MAX:
        return rules, False
    tier1 = [r for r in rules if r['tier'] == 1]
    tier1_pinned = len(tier1) < PROGRESSIVE_MAX
    if tier1_pinned:
        pinned = tier1
        pool = [r for r in rules if r['tier'] != 1]
    else:
        pinned = []
        pool = rules
    slots = min(PROGRESSIVE_MAX - len(pinned), len(pool))
    cur = _load_progressive_cursor() % len(pool)
    picked = [pool[(cur + i) % len(pool)] for i in range(slots)]
    _save_progressive_cursor((cur + slots) % len(pool))
    selected = pinned + picked
    selected.sort(key=lambda r: (r['tier'], r['id']))
    return selected, tier1_pinned


def _render_progressive_lines(rules):
    """Render the progressive rule index: one compact line per saved rule, so a
    simple rule is fully usable from its line alone; the model reads the full
    .md only if it needs the Why / How-to-apply detail. When the library
    exceeds PROGRESSIVE_MAX the truncation is disclosed explicitly — a silent
    cap reads as "these are all my rules" when it isn't."""
    shown, tier1_pinned = _select_progressive_rules(rules)
    lines = ['### Your saved preferences — check each against this turn and apply the ones that fit:']
    for r in shown:
        desc = r['desc'] or r['id']
        line = f"- [{r['id']}] (tier{r['tier']}) {desc}"
        if r['when']:
            line += f" | when: {r['when'][:200]}"
        if r['scope']:
            line += f" | scope: {r['scope'][:120]}"
        if r.get('scope_anchor'):
            line += f" | anchor: {r['scope_anchor'][:120]}"
        if r['except'] and r['except'].lower() not in {'(none)', 'none'}:
            line += f" | except: {r['except'][:800]}"
        lines.append(line)
    if len(shown) < len(rules):
        # 'turns' on Claude Code / Codex (per-prompt injection); 'sessions' on
        # Copilot, whose only injection point is SessionStart — telling that
        # model "later turns" would promise rules that never arrive.
        cadence = getattr(pt_platform, 'INJECT_CADENCE', 'turns')
        if tier1_pinned:
            how = f'tier-1 rules are always included, the rest rotate in on later {cadence}'
        else:
            how = f'the tier-1 rules alone fill the cap, so all rules rotate in over {cadence}'
        lines.append(
            f"(Showing {len(shown)} of {len(rules)} saved rules — {how}. "
            "Set progressive_max in ~/.tellonce.config.json "
            "or PT_PROGRESSIVE_MAX to widen the window; 0 = show all.)"
        )
    lines.append(
        '(Evaluate every rule independently using explicit evidence from the '
        'current task. Topic similarity or possible future usefulness is not '
        'enough. Apply the minimum sufficient set, continue scanning after a '
        'match, and skip rules whose scope, when, or exception excludes them.)'
    )
    return '\n'.join(lines)


def _render_pending_clarifications(presentation_key: str = '') -> str:
    """Render unresolved semantic questions without reviving the old gate."""
    try:
        enabled = os.environ.get('PT_MEMORY_UPSERT_ENABLED')
        if enabled is None:
            enabled = os.environ.get('B5_MEMORY_UPSERT_ENABLED')
        if enabled is None:
            enabled = _USER_CONFIG.get('memory_upsert_enabled', False)
        if str(enabled).strip().lower() not in {'1', 'true', 'yes', 'on'}:
            return ''
        memory_dir = path_config.get_memory_dir()
        if not os.path.isfile(os.path.join(memory_dir, memory_store.DB_FILENAME)):
            return ''
        store = memory_store.MemoryStore(memory_dir)
        pending = store.needs_user_turns(limit=3)
    except Exception:
        return ''
    if not pending:
        return ''
    memory_upsert.record_clarification_presentation(
        memory_dir,
        presentation_key,
        [item['turn_key'] for item in pending],
    )
    lines = [
        '### Tellonce needs one lightweight clarification before saving memory:',
        (
            'Use the current user message and conversation context first. If the '
            'current message clearly answers an item, proceed without asking it '
            'again; otherwise ask the user one concise question. Do not treat the '
            'quoted source below as new authorization by itself. The user can '
            'dismiss an obsolete item with `memory_upsert.py dismiss --turn-key <id>`.'
        ),
    ]
    for item in pending:
        source = re.sub(r'\s+', ' ', str(item.get('source_text', ''))).strip()[:500]
        reason = str(item.get('reason', '')).strip()[:500]
        lines.append(f"- turn_key={item['turn_key']}: {reason or 'semantic scope remains ambiguous'}")
        if source:
            lines.append(f"  original turn excerpt: {source}")
    return '\n'.join(lines)


def _emit_context(event_name: str, parts: list[str]) -> None:
    context = '\n\n---\n\n'.join(part for part in parts if part)
    if not context:
        return
    out = {
        'hookSpecificOutput': {
            'hookEventName': event_name,
            'additionalContext': context,
        }
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False))


def _emit_progressive(event_name: str, presentation_key: str = '') -> None:
    """Emit the progressive full-index injection as hookSpecificOutput JSON.
    Silent (no output) when there are no rules yet."""
    rules = _collect_all_rules()
    parts = []
    if rules:
        parts.append(_render_progressive_lines(rules))
    clarification = _render_pending_clarifications(presentation_key)
    if clarification:
        parts.append(clarification)
    _emit_context(event_name, parts)


def _write_debug(record):
    """Append a retrieve debug entry to <state>/runtime/retrieve_debug.jsonl.
    Caller controls via B5_RETRIEVE_DEBUG=1. Silent on any IO failure."""
    try:
        state_dir = path_config.get_state_dir()
        log_path = os.path.join(state_dir, 'retrieve_debug.jsonl')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        path_config.chmod_or_warn(log_path, 0o600)
    except Exception:
        pass


def _build_cli_invocation(
    prompt: str,
) -> tuple[list[str], str | None, str | None, bool]:
    """Build command, output path, prompt path, and stdin mode.

    Dispatches by B5_RETRIEVE_CLI:
      - 'claude': prompt via stdin, output on stdout
      - 'codex':  prompt via stdin, output written to --output-last-message file
    Returns an empty command on unknown CLI.
    """
    if RETRIEVE_CLI in ('copilot', 'claude'):
        cli_cmd = 'copilot' if RETRIEVE_CLI == 'copilot' else 'claude'
        prompt_file = None
        prompt_on_stdin = RETRIEVE_CLI == 'claude'
        if prompt_on_stdin:
            cmd = [cli_cmd, '-p']
        elif os.name == 'nt' and len(prompt) > 16000:
            prompt_fd, prompt_file = tempfile.mkstemp(
                prefix='tellonce-retrieve-prompt-',
                suffix='.txt',
            )
            os.close(prompt_fd)
            with open(prompt_file, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(prompt)
            cmd = [
                cli_cmd,
                '-p',
                f'Follow the complete retrieval prompt in '
                f'@{os.path.basename(prompt_file)} exactly. '
                'Return only the requested JSON array.',
            ]
        else:
            cmd = [cli_cmd, '-p', prompt]
        # Only pass --model when set; some runtimes (Copilot) reject an explicit
        # Claude model name, so RETRIEVE_MODEL is empty there and the CLI uses
        # its own default model.
        if RETRIEVE_MODEL:
            cmd.extend(['--model', RETRIEVE_MODEL])
        cmd.extend(['--output-format', 'text'])
        # --setting-sources project: excludes user-global hooks so nested
        # session doesn't fire PT hooks and block itself. Claude-only flag;
        # for Copilot we rely on the B5_RETRIEVE_RECURSION_GUARD env var.
        if RETRIEVE_CLI == 'claude':
            cmd.extend(['--setting-sources', 'project'])
        return (cmd, None, prompt_file, prompt_on_stdin)
    if RETRIEVE_CLI == 'codex':
        # codex exec reads prompt from stdin, writes last assistant text to a
        # file via --output-last-message. --ephemeral + --ignore-user-config +
        # --ignore-rules + read-only sandbox keep this nested call cheap and
        # side-effect-free.
        out_fd, out_path = tempfile.mkstemp(prefix='pt_retrieve_', suffix='.txt')
        os.close(out_fd)
        return (
            ['codex', 'exec', '--ephemeral', '--ignore-user-config',
             '--ignore-rules', '--skip-git-repo-check',
             '--sandbox', 'read-only',
             '-m', RETRIEVE_MODEL,
             '-c', 'model_reasoning_effort="low"',
             '--output-last-message', out_path],
            out_path,
             None,
             True,
        )
    return ([], None, None, False)


def _retrieve_via_cli(user_prompt, fps_dict, memory_idx):
    """CLI backend: small-model semantic retrieval via either
    `claude -p` (CC, default) or `codex exec` (codex), selected by
    B5_RETRIEVE_CLI. Returns hit list shaped like keyword backend.

    Recursion guard: when we spawn the CLI we set
    B5_RETRIEVE_RECURSION_GUARD=1 in the child env so the nested session's
    UPS hook short-circuits, avoiding an infinite loop.
    """
    if not user_prompt:
        return None
    if RETRIEVE_RECURSION_GUARD:
        # Nested CLI session — don't re-fire retrieval; signal backend
        # unavailability so the outer caller can use its local fallback.
        return None
    rules_for_prompt = _build_rules_for_prompt(fps_dict, memory_idx)
    if not rules_for_prompt:
        return []
    prompt_text = user_prompt[:RETRIEVE_HAIKU_PROMPT_BUDGET]
    prompt = _build_llm_prompt_text(prompt_text, rules_for_prompt)

    debug = path_config.pt_env('RETRIEVE_DEBUG') == '1'
    debug_record = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'backend': 'cli',
        'cli': RETRIEVE_CLI,
        'model': RETRIEVE_MODEL,
        'rules_count': len(rules_for_prompt),
        'prompt_truncated_len': len(prompt_text),
    }

    cmd, out_path, prompt_file, prompt_on_stdin = _build_cli_invocation(prompt)
    if not cmd:
        debug_record['err'] = f'unknown B5_RETRIEVE_CLI={RETRIEVE_CLI!r}'
        _write_debug(debug_record) if debug else None
        return None

    # Child CLI sessions must NOT re-fire any PT hook AND must
    # NOT inherit the outer Claude Code session's SSE-mode env vars. If
    # CLAUDECODE / CLAUDE_CODE_SSE_PORT etc. leak through, the inner
    # claude detects "I'm inside a Claude Code session" and routes its
    # response over the SSE channel instead of stdout — retrieve gets
    # back len=1 ('\n') and falls back to keyword.
    child_env = dict(os.environ)
    child_env['PT_CHILD_SESSION'] = '1'
    # Recursion guard for our own hook scripts (CC + Codex check this).
    child_env['B5_RETRIEVE_RECURSION_GUARD'] = '1'
    # Disable all PT hooks in the inner session so its Stop hook chain
    # (check-observation-log / deterministic-block / verify-compliance /
    # shadow-judge / memory-upsert) doesn't interfere with the child result and
    # turn the result into terminal_reason=stop_hook_prevented.
    child_env['B5_DETERMINISTIC_DISABLED'] = '1'
    child_env['B5_SHADOW_DISABLED'] = '1'
    child_env['B5_INJECT_DISABLED'] = '1'
    # Strip outer session markers so inner CLI runs as a fresh top-level
    # session and writes its result to stdout.
    for k in (
        'CLAUDECODE',
        'CLAUDE_CODE_SSE_PORT',
        'CLAUDE_CODE_ENTRYPOINT',
        'CLAUDE_CODE_EXECPATH',
        'COPILOT_SESSION_ID',
        'AI_AGENT',
    ):
        child_env.pop(k, None)

    # Run inner CLI from temp dir so no project settings get loaded.
    inner_cwd = tempfile.gettempdir()
    out = ''
    try:
        t0 = time.time()
        if prompt_on_stdin:
            proc = subprocess.run(
                cmd, input=prompt,
                capture_output=True, text=True, encoding='utf-8',
                timeout=RETRIEVE_TIMEOUT_S, env=child_env,
                cwd=inner_cwd,
            )
        else:
            # copilot/claude -p has prompt as argv positional. Close inner
            # stdin (DEVNULL) so it doesn't wait for input.
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding='utf-8',
                timeout=RETRIEVE_TIMEOUT_S, env=child_env,
                cwd=inner_cwd,
            )
        debug_record['latency_ms'] = round((time.time() - t0) * 1000, 1)
        debug_record['rc'] = proc.returncode
        if proc.returncode != 0:
            debug_record['err'] = f'rc={proc.returncode} stderr={(proc.stderr or "")[:200]}'
            _write_debug(debug_record) if debug else None
            return None
        # claude: stdout = response. codex: read out_path file.
        if out_path and os.path.isfile(out_path):
            try:
                with open(out_path, encoding='utf-8', errors='replace') as _f:
                    out = _f.read()
            except OSError:
                out = proc.stdout or ''
        else:
            out = proc.stdout or ''
        debug_record['stdout_len'] = len(out)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        debug_record['err'] = f'{type(e).__name__}: {str(e)[:200]}'
        _write_debug(debug_record) if debug else None
        return None
    except Exception as e:
        debug_record['err'] = f'{type(e).__name__}: {str(e)[:200]}'
        _write_debug(debug_record) if debug else None
        return None
    finally:
        for cleanup_path in (out_path, prompt_file):
            if not cleanup_path:
                continue
            try:
                os.unlink(cleanup_path)
            except OSError:
                pass

    m = re.search(r'\[[^\[\]]*\]', out, re.DOTALL)
    if not m:
        debug_record['err'] = f'no JSON array in stdout (len={len(out)})'
        debug_record['stdout_head'] = out[:200]
        _write_debug(debug_record) if debug else None
        return None
    try:
        ids = json.loads(m.group())
    except (json.JSONDecodeError, ValueError) as e:
        debug_record['err'] = f'json decode: {e}'
        _write_debug(debug_record) if debug else None
        return None
    if not isinstance(ids, list):
        debug_record['err'] = f'not a list: {type(ids).__name__}'
        _write_debug(debug_record) if debug else None
        return None
    debug_record['ids'] = ids
    _write_debug(debug_record) if debug else None

    rules_by_id = {r['id']: r for r in rules_for_prompt}
    hits = []
    for rid in ids:
        if not isinstance(rid, str):
            continue
        r = rules_by_id.get(rid)
        if not r:
            continue
        hits.append({
            'id': r['id'],
            'trigger': f'{RETRIEVE_CLI}-semantic',
            'priority': r['priority'],
            'desc': r['desc'],
            'action': r['action'],
        })
    if ids and not hits:
        return None
    return hits


def _build_rules_for_prompt(fps_dict, memory_idx):
    """Shared helper: collect rule descriptions for the LLM prompt across
    cli/api backends.

    Desc-fallback: rules that exist only in memory dir (not in
    fingerprints.yaml) used to render with empty desc in additionalContext,
    making the user-facing block read like
        - **[fmt-pref-001]** (normal)
            • applies_when: ...
    Now we fall back to the .md frontmatter `description:` so users see
    a meaningful one-liner even for memory-only rules.
    """
    rules_for_prompt = []
    seen_ids = set()
    for atomic_id, rule in (fps_dict or {}).items():
        if not isinstance(rule, dict) or atomic_id in seen_ids:
            continue
        seen_ids.add(atomic_id)
        desc = (rule.get('desc') or '')
        if not desc:
            desc = read_rule_description(atomic_id)
        desc = desc[:140]
        applies_when, condition = memory_idx.get(atomic_id, ('', ''))
        effective_when = applies_when or condition
        metadata = read_rule_metadata(atomic_id)
        rules_for_prompt.append({
            'id': atomic_id,
            'desc': desc,
            'applies_when': effective_when[:200],
            'scope': metadata['scope'][:120],
            'scope_anchor': metadata.get('scope_anchor', '')[:120],
            'does_not_apply_when': metadata['does_not_apply_when'][:800],
            'priority': rule.get('priority', 'normal'),
            'action': (rule.get('action') or '')[:140],
        })
    for atomic_id, (applies_when, condition) in memory_idx.items():
        if atomic_id in seen_ids:
            continue
        seen_ids.add(atomic_id)
        metadata = read_rule_metadata(atomic_id)
        effective_when = applies_when or condition
        rules_for_prompt.append({
            'id': atomic_id,
            'desc': read_rule_description(atomic_id)[:140],
            'applies_when': effective_when[:200],
            'scope': metadata['scope'][:120],
            'scope_anchor': metadata.get('scope_anchor', '')[:120],
            'does_not_apply_when': metadata['does_not_apply_when'][:800],
            'priority': 'normal',
            'action': '',
        })
    return rules_for_prompt[:RETRIEVE_HAIKU_RULE_LIMIT]


def _build_llm_prompt_text(user_prompt, rules_for_prompt):
    rules_lines = [
        f"- {r['id']}: {r['desc']}"
        + (f" | applies_when: {r['applies_when']}" if r['applies_when'] else '')
        + (f" | scope: {r['scope']}" if r.get('scope') else '')
        + (f" | anchor: {r['scope_anchor']}" if r.get('scope_anchor') else '')
        + (
            f" | does_not_apply_when: {r['does_not_apply_when']}"
            if r.get('does_not_apply_when')
            and r['does_not_apply_when'].lower() not in {'(none)', 'none'}
            else ''
        )
        for r in rules_for_prompt
    ]
    rules_text = '\n'.join(rules_lines)
    prompt_text = user_prompt[:RETRIEVE_HAIKU_PROMPT_BUDGET]
    return (
        "You select which preference rules apply to a user message.\n\n"
        "User message:\n\"\"\"\n" + prompt_text + "\n\"\"\"\n\n"
        "Available rules (one per line with applicability metadata):\n"
        + rules_text + "\n\n"
        "Evaluate every rule independently. Select a rule only when explicit "
        "evidence in the message satisfies its applicability and no exception "
        "matches. Topic similarity or possible future usefulness is not enough. "
        "Continue scanning after a match and return the minimum sufficient set.\n\n"
        "Return ONLY a JSON array of atomic_id strings for rules that apply, no prose.\n"
        "Example: [\"fmt-pref-001\", \"tool-pref-002\"]\n"
        "If no rule applies, return [].\n"
    )


def _resolve_api_endpoint() -> tuple[str, dict, str]:
    """API backend: pick OpenAI-compatible (url, headers, provider).
    Returns ('', {}, '') if config incomplete (caller falls back to keyword)."""
    provider = RETRIEVE_API_PROVIDER
    if provider == 'deepinfra':
        base = path_config.pt_env('RETRIEVE_API_BASE_URL', 'https://api.deepinfra.com/v1/openai')
        api_key = (
            os.environ.get('DEEPINFRA_API_KEY')
            or path_config.pt_env('RETRIEVE_API_KEY')
            or ''
        )
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
    elif provider == 'openrouter':
        base = path_config.pt_env('RETRIEVE_API_BASE_URL', 'https://openrouter.ai/api/v1')
        api_key = (
            os.environ.get('OPENROUTER_API_KEY')
            or path_config.pt_env('RETRIEVE_API_KEY')
            or ''
        )
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            # OpenRouter prefers identifying headers (rate-limit hygiene).
            'HTTP-Referer': 'https://github.com/YujunZhou/tellonce',
            'X-Title': 'tellonce',
        }
    else:
        # Custom OpenAI-compatible: user MUST provide both env vars.
        base = path_config.pt_env('RETRIEVE_API_BASE_URL', '').rstrip('/')
        api_key = path_config.pt_env('RETRIEVE_API_KEY', '')
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
    if not base or not api_key:
        return ('', {}, provider)
    url = base.rstrip('/') + '/chat/completions'
    return (url, headers, provider)


def _retrieve_via_api(user_prompt, fps_dict, memory_idx):
    """Direct OpenAI-compatible HTTP call (DeepInfra /
    OpenRouter / etc) for retrieve. No CLI cold-start, no nested-hook
    issue — fastest path, but uses an API key (not subscription).

    Returns a hit list, including [] for a successful no-match. Returns None on
    failure so the caller can distinguish failure from semantic rejection.
    """
    if not user_prompt or RETRIEVE_RECURSION_GUARD:
        return []
    rules_for_prompt = _build_rules_for_prompt(fps_dict, memory_idx)
    if not rules_for_prompt:
        return []
    prompt = _build_llm_prompt_text(user_prompt, rules_for_prompt)

    url, headers, provider = _resolve_api_endpoint()
    debug = path_config.pt_env('RETRIEVE_DEBUG') == '1'
    debug_record = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'backend': 'api',
        'provider': provider,
        'model': RETRIEVE_MODEL,
        'rules_count': len(rules_for_prompt),
        'prompt_truncated_len': len(user_prompt[:RETRIEVE_HAIKU_PROMPT_BUDGET]),
    }
    if not url:
        debug_record['err'] = 'api endpoint config incomplete (base_url or api_key missing)'
        _write_debug(debug_record) if debug else None
        return None

    body = json.dumps({
        'model': RETRIEVE_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.0,
        'max_tokens': 256,
    }).encode('utf-8')

    import urllib.request as _urlreq
    import urllib.error as _urlerr
    out = ''
    try:
        t0 = time.time()
        req = _urlreq.Request(url, data=body, headers=headers, method='POST')
        with _urlreq.urlopen(req, timeout=RETRIEVE_TIMEOUT_S) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        debug_record['latency_ms'] = round((time.time() - t0) * 1000, 1)
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            debug_record['err'] = f'response not JSON: {e}; head={raw[:200]}'
            _write_debug(debug_record) if debug else None
            return None
        choices = obj.get('choices') or []
        if not choices:
            err_field = obj.get('error') or obj.get('message') or {}
            debug_record['err'] = f'no choices; error={str(err_field)[:200]}'
            _write_debug(debug_record) if debug else None
            return None
        out = (choices[0].get('message') or {}).get('content') or ''
        debug_record['stdout_len'] = len(out)
    except _urlerr.HTTPError as e:
        body_excerpt = ''
        try:
            body_excerpt = e.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            pass
        debug_record['err'] = f'HTTP {e.code}: {body_excerpt}'
        _write_debug(debug_record) if debug else None
        return None
    except Exception as e:
        debug_record['err'] = f'{type(e).__name__}: {str(e)[:200]}'
        _write_debug(debug_record) if debug else None
        return None

    m = re.search(r'\[[^\[\]]*\]', out, re.DOTALL)
    if not m:
        debug_record['err'] = f'no JSON array in response (len={len(out)})'
        debug_record['stdout_head'] = out[:200]
        _write_debug(debug_record) if debug else None
        return None
    try:
        ids = json.loads(m.group())
    except (json.JSONDecodeError, ValueError) as e:
        debug_record['err'] = f'json decode: {e}'
        _write_debug(debug_record) if debug else None
        return None
    if not isinstance(ids, list):
        return None
    debug_record['ids'] = ids
    _write_debug(debug_record) if debug else None

    rules_by_id = {r['id']: r for r in rules_for_prompt}
    hits = []
    for rid in ids:
        if not isinstance(rid, str):
            continue
        r = rules_by_id.get(rid)
        if not r:
            continue
        hits.append({
            'id': r['id'],
            'trigger': f'api-{provider}-semantic',
            'priority': r['priority'],
            'desc': r['desc'],
            'action': r['action'],
        })
    if ids and not hits:
        return None
    return hits


def _retrieve_via_keyword(user_prompt, fps_dict):
    """Keyword + regex matching (v1 behavior)."""
    prompt_scan = user_prompt[:PROMPT_TRUNCATE].lower()
    hits = []
    for atomic_id, rule in (fps_dict or {}).items():
        if not isinstance(rule, dict):
            continue
        fired_by = None
        for key in ('triggers', 'triggers_force_en', 'triggers_force_zh'):
            for trig in rule.get(key, []) or []:
                if trig and trig.lower() in prompt_scan:
                    fired_by = trig
                    break
            if fired_by:
                break
        if not fired_by:
            pat = rule.get('triggers_pattern')
            if pat:
                try:
                    if re.search(pat, user_prompt[:PROMPT_TRUNCATE]):
                        fired_by = f'pattern:{pat[:40]}'
                except re.error:
                    pass
        if fired_by:
            hits.append({
                'id': atomic_id,
                'trigger': fired_by,
                'priority': rule.get('priority', 'normal'),
                'desc': rule.get('desc', ''),
                'action': rule.get('action', ''),
            })
    return hits


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        sys.exit(0)
    prompt_scan = prompt[:PROMPT_TRUNCATE].lower()
    presentation_key = transcript_adapter.get_presentation_key(data)

    # Progressive backend: inject the rule index every turn (capped + rotating
    # under PROGRESSIVE_MAX) and let the main model judge applicability. No
    # prompt filtering, no LLM, no PyYAML.
    if RETRIEVE_BACKEND == 'progressive':
        _emit_progressive('UserPromptSubmit', presentation_key)
        return
    try:
        import yaml  # noqa: F401  (kept so the guard below still exits cleanly if missing)
    except ImportError:
        sys.exit(0)
    clarification = _render_pending_clarifications(presentation_key)

    fps = _load_fingerprints()

    # Backend dispatch. cli/api fall back to keyword only on backend failure;
    # a valid semantic [] must remain a no-match or exclusions are bypassed.
    if RETRIEVE_BACKEND == 'api':
        memory_idx = _build_index()
        hits = _retrieve_via_api(prompt, fps, memory_idx)
        if hits is None:
            hits = _retrieve_via_keyword(prompt, fps)
    elif RETRIEVE_BACKEND == 'cli':
        memory_idx = _build_index()
        hits = _retrieve_via_cli(prompt, fps, memory_idx)
        if hits is None:
            hits = _retrieve_via_keyword(prompt, fps)
    else:
        hits = _retrieve_via_keyword(prompt, fps)

    if not hits:
        _emit_context('UserPromptSubmit', [clarification])
        sys.exit(0)

    priority_order = {'critical': 0, 'high': 1, 'normal': 2}
    hits.sort(key=lambda h: priority_order.get(h['priority'], 3))

    lines = ['### Fingerprint retrieval — memory rules auto-matched for this turn:']
    lines.append('(For each rule, verify its applies_when/condition against the current context before applying. If the rule does not apply, note why and skip.)')
    lines.append('')
    for h in hits[:MAX_SHOW]:
        applies_when, condition = read_rule_applicability(h['id'])
        metadata = read_rule_metadata(h['id'])
        # Desc-fallback: keyword-backend hits may have empty desc
        # if the rule is memory-only (not in fingerprints.yaml). Fall back
        # to the .md frontmatter `description:` so the rendered block has
        # a meaningful one-liner.
        desc = h.get('desc') or read_rule_description(h['id'])
        lines.append(f"- **[{h['id']}]** ({h['priority']}) {desc}")
        if h['action']:
            lines.append(f"    • action: {h['action']}")
        if applies_when:
            lines.append(f"    • applies_when: {applies_when[:200]}")
        if condition:
            lines.append(f"    • condition: {condition[:120]}")
        if metadata['scope']:
            lines.append(f"    • scope: {metadata['scope'][:120]}")
        if metadata.get('scope_anchor'):
            lines.append(f"    • scope_anchor: {metadata['scope_anchor'][:120]}")
        exception = metadata['does_not_apply_when']
        if exception and exception.lower() not in {'(none)', 'none', '[]', '[ ]'}:
            lines.append(f"    • does_not_apply_when: {exception[:800]}")
        lines.append(f"    • triggered by: {h['trigger']}")
    lines.append('')
    lines.append(
        '(Memory retrieval + applicability gate. Apply only when scope and '
        'applicability match and no does_not_apply_when exception holds.)'
    )

    _emit_context('UserPromptSubmit', ['\n'.join(lines), clarification])


def session_start_summary(data=None):
    """SessionStart mode: inject saved rules without prompt matching.

    At session start there is no user prompt, so keyword/CLI/API matching can't
    fire. The default 'progressive' backend injects the rule index (one line
    each; capped + rotating under PROGRESSIVE_MAX, truncation disclosed).
    Legacy backends fall back to scanning fingerprints.yaml for critical/high
    rules only.
    """
    presentation_key = transcript_adapter.get_presentation_key(data or {})
    # Progressive backend: inject the full rule index once at session start.
    if RETRIEVE_BACKEND == 'progressive':
        _emit_progressive('SessionStart', presentation_key)
        return
    try:
        import yaml
    except ImportError:
        sys.exit(0)
    clarification = _render_pending_clarifications(presentation_key)

    fps = _load_fingerprints()
    memory_idx = _build_index()

    # Collect all rules that have critical or high priority
    priority_order = {'critical': 0, 'high': 1, 'normal': 2}
    candidates = []
    seen_ids = set()

    # From fingerprints.yaml
    for atomic_id, rule in fps.items():
        if not isinstance(rule, dict):
            continue
        pri = rule.get('priority', 'normal')
        if pri in ('critical', 'high'):
            candidates.append({
                'id': atomic_id,
                'priority': pri,
                'desc': rule.get('desc', ''),
                'action': rule.get('action', ''),
            })
            seen_ids.add(atomic_id)

    # Memory-dir-only rules (not in fingerprints.yaml) lack a priority field
    # in their .md frontmatter, so we cannot reliably classify them as
    # critical/high. Skip them at session start; they'll be matched per-prompt
    # via keyword/CLI/API retrieval when available.

    if not candidates:
        _emit_context('SessionStart', [clarification])
        sys.exit(0)

    candidates.sort(key=lambda h: priority_order.get(h['priority'], 3))

    lines = ['### Session-start memory rules summary (critical/high fingerprint rules):']
    lines.append('(Verify applies_when/condition against context before applying. Skip inapplicable rules.)')
    lines.append('')
    for h in candidates[:MAX_SHOW]:
        applies_when, condition = read_rule_applicability(h['id'])
        metadata = read_rule_metadata(h['id'])
        desc = h.get('desc') or read_rule_description(h['id'])
        lines.append(f"- **[{h['id']}]** ({h['priority']}) {desc}")
        if h['action']:
            lines.append(f"    • action: {h['action']}")
        if applies_when:
            lines.append(f"    • applies_when: {applies_when[:200]}")
        if condition:
            lines.append(f"    • condition: {condition[:120]}")
        if metadata['scope']:
            lines.append(f"    • scope: {metadata['scope'][:120]}")
        if metadata.get('scope_anchor'):
            lines.append(f"    • scope_anchor: {metadata['scope_anchor'][:120]}")
        exception = metadata['does_not_apply_when']
        if exception and exception.lower() not in {'(none)', 'none', '[]', '[ ]'}:
            lines.append(f"    • does_not_apply_when: {exception[:800]}")
    lines.append('')
    lines.append('(Session-start memory retrieval. Full prompt-based matching fires per-turn in Claude; Copilot injects at session start only.)')

    _emit_context('SessionStart', ['\n'.join(lines), clarification])


if __name__ == '__main__':
    try:
        if '--session-start' in sys.argv:
            try:
                session_data = json.load(sys.stdin)
            except Exception:
                session_data = {}
            session_start_summary(session_data)
        else:
            main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
