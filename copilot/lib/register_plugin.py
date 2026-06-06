#!/usr/bin/env python3
"""register_plugin — register/unregister preference-tracker in Copilot's plugin list.

WHY THIS EXISTS: Copilot only loads a plugin's hooks if the plugin is listed in
~/.copilot/config.json `installedPlugins` (each entry points at a cache_path).
The canonical way to get there is `copilot plugin install <owner/repo>`, which
Copilot manages itself. But a *side-loaded* install (files copied into
~/.copilot/installed-plugins without going through the plugin manager) is NOT
registered, so the hooks silently never fire. This helper closes that gap for
side-load installs. It is idempotent and backs up config.json first.

config.json is JSONC (a couple of leading `//` header lines) and is marked
"managed automatically" by Copilot, so we touch it minimally: preserve the
leading comment block verbatim, parse the JSON body, upsert one array entry,
and write back. Prefer `copilot plugin install` when you can; this is the
side-load fallback.

Usage:
    python <plugin>/lib/register_plugin.py            # register (idempotent)
    python <plugin>/lib/register_plugin.py --status   # print registered? (no write)
    python <plugin>/lib/register_plugin.py --unregister
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

PLUGIN_NAME = 'preference-tracker'
CONFIG_PATH = os.path.expanduser('~/.copilot/config.json')

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(_LIB_DIR)  # <plugin> = parent of lib/


def _split_header(text):
    """Return (header_lines, body_text). Header = consecutive leading // comment
    lines (and blank lines) before the JSON starts."""
    lines = text.splitlines(keepends=True)
    header = []
    i = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith('//') or s == '':
            header.append(ln)
        else:
            break
    body = ''.join(lines[i:]) if i < len(lines) else ''
    return ''.join(header), body


def _load():
    if not os.path.exists(CONFIG_PATH):
        return '', {}
    with open(CONFIG_PATH, encoding='utf-8-sig') as f:
        raw = f.read()
    header, body = _split_header(raw)
    try:
        data = json.loads(body) if body.strip() else {}
    except Exception as e:
        raise RuntimeError(f'config.json body is not valid JSON ({e}); refusing to edit')
    return header, data


def _save(header, data):
    body = json.dumps(data, indent=2, ensure_ascii=False)
    # header already ends with a newline (it's whole lines kept verbatim); if it
    # somehow doesn't, add one so the JSON body starts on its own line.
    if not header:
        out = body + '\n'
    elif header.endswith('\n'):
        out = header + body + '\n'
    else:
        out = header + '\n' + body + '\n'
    # Timestamped backup so a re-run after a corruption can't overwrite the only
    # good copy (kept local; *.bak is gitignored).
    try:
        if os.path.exists(CONFIG_PATH):
            ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            bak = f'{CONFIG_PATH}.bak-pt-register-{ts}'
            with open(CONFIG_PATH, encoding='utf-8-sig') as f, open(bak, 'w', encoding='utf-8') as b:
                b.write(f.read())
    except Exception:
        pass
    d = os.path.dirname(CONFIG_PATH) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.pt-cfg-', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(out)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _is_registered(data):
    return any(isinstance(e, dict) and e.get('name') == PLUGIN_NAME
              for e in (data.get('installedPlugins') or []))


def register():
    header, data = _load()
    if _is_registered(data):
        print(f'[OK] {PLUGIN_NAME} already registered in {CONFIG_PATH}')
        return 0
    existing = data.get('installedPlugins')
    if existing is not None and not isinstance(existing, list):
        print(f'[WARN] installedPlugins in {CONFIG_PATH} is not a list (got '
              f'{type(existing).__name__}); Copilot config schema may have changed. '
              f'Refusing to edit — register via `copilot plugin install` instead.')
        return 1
    plugins = data.setdefault('installedPlugins', [])
    norm = PLUGIN_ROOT.replace('\\', '/').lower()
    if 'installed-plugins' not in norm:
        print(f'[WARN] cache_path is not under ~/.copilot/installed-plugins '
              f'({PLUGIN_ROOT}). Copilot loads plugins from installed-plugins; '
              f'run this from the INSTALLED plugin copy, not the source repo.')
    plugins.append({
        'name': PLUGIN_NAME,
        'marketplace': PLUGIN_NAME,
        'version': '1.0.0',
        'installed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        'enabled': True,
        'cache_path': PLUGIN_ROOT,
    })
    _save(header, data)
    print(f'[OK] Registered {PLUGIN_NAME} (cache_path={PLUGIN_ROOT}). '
          f'Restart Copilot to load hooks. (config backed up alongside it)')
    return 0


def unregister():
    header, data = _load()
    plugins = data.get('installedPlugins') or []
    new = [e for e in plugins if not (isinstance(e, dict) and e.get('name') == PLUGIN_NAME)]
    if len(new) == len(plugins):
        print(f'[OK] {PLUGIN_NAME} was not registered')
        return 0
    data['installedPlugins'] = new
    _save(header, data)
    print(f'[OK] Unregistered {PLUGIN_NAME} from {CONFIG_PATH}')
    return 0


def status():
    try:
        _header, data = _load()
    except Exception as e:
        print(f'[FAIL] {e}')
        return 1
    if _is_registered(data):
        print(f'[OK] {PLUGIN_NAME} IS registered in {CONFIG_PATH}')
        return 0
    print(f'[WARN] {PLUGIN_NAME} is NOT registered — Copilot will not load its hooks. '
          f'Run: python "{os.path.join(_LIB_DIR, "register_plugin.py")}"  (or install via '
          f'`copilot plugin install`).')
    return 0


def main():
    for _s in (sys.stdout, sys.stderr):
        try:
            if _s is not None and hasattr(_s, 'reconfigure'):
                _s.reconfigure(encoding='utf-8')
        except Exception:
            pass
    args = sys.argv[1:]
    if '--unregister' in args:
        return unregister()
    if '--status' in args:
        return status()
    return register()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        sys.stderr.write(f'register_plugin error: {type(e).__name__}: {e}\n')
        sys.exit(1)
