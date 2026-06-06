#!/usr/bin/env python3
"""doctor — read-only self-check for the preference-tracker Copilot plugin.

Cross-platform (pure Python, no third-party deps). Prints a PASS/WARN/FAIL
report so a public user can diagnose an install without reading the code.

Usage:
    python <plugin>/lib/doctor.py
    python <plugin>/lib/doctor.py --quiet   # only the final summary line

Never modifies anything. Exit code: 0 if no FAIL, 1 if any FAIL.
"""
import json
import os
import shutil
import sys

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _LIB_DIR)

for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None and hasattr(_s, 'reconfigure'):
            _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

_RESULTS = []


def _record(level, name, detail=''):
    _RESULTS.append((level, name, detail))


def _emit(quiet):
    icon = {'PASS': '[OK]  ', 'WARN': '[WARN]', 'FAIL': '[FAIL]'}
    if not quiet:
        for level, name, detail in _RESULTS:
            line = f"{icon.get(level, '      ')} {name}"
            if detail:
                line += f" — {detail}"
            print(line)
        print('-' * 60)
    n_fail = sum(1 for r in _RESULTS if r[0] == 'FAIL')
    n_warn = sum(1 for r in _RESULTS if r[0] == 'WARN')
    n_pass = sum(1 for r in _RESULTS if r[0] == 'PASS')
    print(f"doctor: {n_pass} PASS, {n_warn} WARN, {n_fail} FAIL")
    return 1 if n_fail else 0


def check_python():
    v = sys.version_info
    if v >= (3, 7):
        _record('PASS', 'Python version', f'{v.major}.{v.minor}.{v.micro}')
    else:
        _record('FAIL', 'Python version', f'{v.major}.{v.minor} (<3.7)')


def check_imports():
    mods = [
        'path_config', 'transcript_adapter', 'deterministic_block',
        'verify_compliance', 'verify_retry_shadow', 'session_start_inject',
        'retrieve_inject', 'pt_mode', 'redaction', 'pending_queue_manager',
    ]
    bad = []
    for m in mods:
        try:
            __import__(m)
        except Exception as e:
            bad.append(f'{m}: {type(e).__name__}: {e}')
    if not bad:
        _record('PASS', 'lib modules import', f'{len(mods)} modules')
    else:
        _record('FAIL', 'lib modules import', '; '.join(bad))


def check_paths():
    try:
        import path_config
        pr = path_config.get_project_root()
        sd = path_config.get_state_dir()
        md = path_config.get_memory_dir()
        _record('PASS', 'path_config resolves', f'project_root={pr}')
        _record('PASS', 'state dir', sd)
        _record('PASS', 'memory dir', md)
        return md
    except Exception as e:
        _record('FAIL', 'path_config resolves', f'{type(e).__name__}: {e}')
        return None


def check_mode():
    try:
        import path_config
        enforce = path_config.enforcement_enabled()
        shadow = path_config.shadow_enabled()
        mode = 'full' if (enforce and shadow) else ('enforce' if enforce else 'observe')
        detail = f'mode={mode} (enforce={enforce}, shadow={shadow})'
        if mode == 'observe':
            _record('PASS', 'run mode', detail + ' — safe default')
        else:
            _record('WARN', 'run mode', detail + ' — hard enforcement / LLM judge active')
    except Exception as e:
        _record('FAIL', 'run mode', f'{type(e).__name__}: {e}')


def check_config():
    try:
        import path_config
        p = path_config.CONFIG_PATH
        if not os.path.exists(p):
            _record('WARN', 'config file', f'not found at {p} (defaults apply)')
            return
        with open(p, encoding='utf-8-sig') as f:
            json.load(f)
        bom = ''
        with open(p, 'rb') as f:
            if f.read(3) == b'\xef\xbb\xbf':
                bom = ' (has UTF-8 BOM — tolerated, but readers prefer no BOM)'
        _record('PASS', 'config readable', p + bom)
    except Exception as e:
        _record('FAIL', 'config readable', f'{type(e).__name__}: {e}')


def check_memory(md):
    if not md:
        return
    try:
        if not os.path.isdir(md):
            _record('WARN', 'memory dir', 'does not exist yet (no rules saved)')
            return
        rules = [f for f in os.listdir(md)
                 if f.endswith('.md') and f != 'MEMORY.md' and not f.startswith('_archived')]
        has_index = os.path.exists(os.path.join(md, 'MEMORY.md'))
        detail = f'{len(rules)} rule files; MEMORY.md index {"present" if has_index else "MISSING"}'
        # Scale advisory tied to the known retrieval cap.
        if len(rules) > 40:
            _record('WARN', 'memory size', detail + ' — >40 rules: retrieval cap may drop some (see docs)')
        else:
            _record('PASS', 'memory size', detail)
    except Exception as e:
        _record('WARN', 'memory dir', f'{type(e).__name__}: {e}')


def check_hooks():
    plugin_root = os.path.dirname(_LIB_DIR)
    hooks_dir = os.path.join(plugin_root, 'hooks')
    hooks_json = os.path.join(hooks_dir, 'hooks.json')
    if os.path.exists(hooks_json):
        try:
            with open(hooks_json, encoding='utf-8') as f:
                json.load(f)
            _record('PASS', 'hooks.json', 'present and valid JSON')
        except Exception as e:
            _record('FAIL', 'hooks.json', f'invalid JSON: {e}')
    else:
        _record('WARN', 'hooks.json', f'not found at {hooks_json}')
    expected = [
        'check-observation-log.sh', 'memory-deterministic-block.sh',
        'memory-verify-compliance.sh', 'memory-shadow-judge.sh',
        'memory-pending-promote.sh', 'session-start-inject.sh',
    ]
    missing = [h for h in expected if not os.path.exists(os.path.join(hooks_dir, h))]
    if not missing:
        _record('PASS', 'hook scripts', f'{len(expected)} present')
    else:
        _record('WARN', 'hook scripts', 'missing: ' + ', '.join(missing))


def check_plugin_registration():
    """Copilot only loads a plugin's hooks if the plugin is listed in
    ~/.copilot/config.json `installedPlugins` (each with a cache_path). Merely
    copying files into installed-plugins/ and flipping settings.json
    `enabledPlugins` is NOT enough — the hooks then silently never fire (0 hooks
    loaded, no block, no injection). This check catches that exact failure."""
    cfg = os.path.expanduser('~/.copilot/config.json')
    if not os.path.exists(cfg):
        _record('WARN', 'copilot plugin registration',
                f'{cfg} not found (Claude Code / non-Copilot install?) — skipped')
        return
    try:
        with open(cfg, encoding='utf-8-sig') as f:
            raw = f.read()
        # config.json is JSONC: strip whole-line // comments before parsing.
        cleaned = '\n'.join('' if ln.lstrip().startswith('//') else ln
                            for ln in raw.splitlines())
        data = json.loads(cleaned)
    except Exception as e:
        _record('WARN', 'copilot plugin registration',
                f'could not parse {cfg}: {type(e).__name__}: {e}')
        return
    plugins = data.get('installedPlugins') or []
    plugin_root = os.path.normcase(os.path.normpath(os.path.dirname(_LIB_DIR)))
    found = None
    for p in plugins:
        if not isinstance(p, dict):
            continue
        cp = p.get('cache_path')
        same_path = bool(cp) and os.path.normcase(os.path.normpath(cp)) == plugin_root
        if same_path or p.get('name') == 'preference-tracker':
            found = p
            break
    if found is None:
        _record('FAIL', 'copilot plugin registration',
                'preference-tracker NOT in ~/.copilot/config.json installedPlugins — '
                'Copilot will NOT load its hooks (no block / no injection). Install via '
                '`/plugin`, or add a {name,marketplace,enabled,cache_path} entry.')
    elif found.get('enabled') is False:
        _record('WARN', 'copilot plugin registration',
                'registered but enabled=false — hooks will not load')
    else:
        _record('PASS', 'copilot plugin registration', 'registered in installedPlugins')


def check_tools():
    if shutil.which('jq') is None:
        _record('WARN', 'jq', 'not found — bash hooks degrade gracefully; Windows uses python directly')
    else:
        _record('PASS', 'jq', 'found')
    cli = shutil.which('copilot')
    if cli is None:
        _record('WARN', 'copilot CLI', 'not on PATH — shadow judge (full mode only) cannot run')
    else:
        _record('PASS', 'copilot CLI', 'found')


def main():
    quiet = '--quiet' in sys.argv
    check_python()
    check_imports()
    md = check_paths()
    check_mode()
    check_config()
    check_memory(md)
    check_hooks()
    check_plugin_registration()
    check_tools()
    return _emit(quiet)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        sys.stderr.write(f'doctor crashed: {type(e).__name__}: {e}\n')
        sys.exit(1)
