#!/usr/bin/env python3
"""uninstall — remove preference-tracker state/memory/config for the Copilot plugin.

The plugin code itself is removed by `copilot plugin uninstall preference-tracker`.
This helper cleans up the per-project state, the saved memory rules, and the
mode keys this skill wrote to ~/.preference-tracker.config.json.

SAFE BY DEFAULT: with no flags it only PRINTS what it would remove (dry run).
Pass explicit flags to actually delete:

    python <plugin>/lib/uninstall.py                 # dry run (show only)
    python <plugin>/lib/uninstall.py --purge-state   # delete .copilot/preference-tracker-state/
    python <plugin>/lib/uninstall.py --purge-memory  # delete the memory/ rules
    python <plugin>/lib/uninstall.py --reset-config   # remove enforce/shadow keys (back to observe)
    python <plugin>/lib/uninstall.py --unregister     # remove from Copilot's installedPlugins
    python <plugin>/lib/uninstall.py --all            # all of the above

User data (memory rules) is preserved unless you ask for --purge-memory/--all.
"""
import json
import os
import shutil
import sys

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _LIB_DIR)
import path_config  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None and hasattr(_s, 'reconfigure'):
            _s.reconfigure(encoding='utf-8')
    except Exception:
        pass


def _state_root():
    # parent of the runtime dir = .../.copilot/preference-tracker-state
    return os.path.dirname(path_config.get_state_dir())


def _rm_dir(path, dry):
    if not path or not os.path.isdir(path):
        print(f'  (skip) not present: {path}')
        return
    if dry:
        print(f'  would remove dir: {path}')
    else:
        try:
            shutil.rmtree(path)
            print(f'  removed dir: {path}')
        except Exception as e:
            print(f'  ERROR removing {path}: {type(e).__name__}: {e}')


def _reset_config(dry):
    p = path_config.CONFIG_PATH
    if not os.path.exists(p):
        print(f'  (skip) no config at {p}')
        return
    try:
        with open(p, encoding='utf-8-sig') as f:
            cfg = json.load(f)
    except Exception as e:
        print(f'  ERROR reading config: {e}')
        return
    removed = [k for k in ('enforce', 'shadow') if k in cfg]
    if not removed:
        print('  (skip) config has no enforce/shadow keys')
        return
    if dry:
        print(f'  would remove config keys {removed} from {p} (retrieve_* kept)')
        return
    for k in removed:
        cfg.pop(k, None)
    import tempfile
    d = os.path.dirname(p) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.pt-config-', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, p)
        print(f'  reset config: removed {removed} (back to observe default)')
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        print(f'  ERROR writing config: {e}')


def main():
    args = set(sys.argv[1:])
    do_all = '--all' in args
    purge_state = do_all or '--purge-state' in args
    purge_memory = do_all or '--purge-memory' in args
    reset_config = do_all or '--reset-config' in args
    unregister = do_all or '--unregister' in args
    dry = not (purge_state or purge_memory or reset_config or unregister)

    print('preference-tracker uninstall' + (' (DRY RUN — pass flags to act)' if dry else ''))
    print('-' * 60)
    print('To remove the plugin code itself, run:')
    print('  copilot plugin uninstall preference-tracker')
    print('-' * 60)

    if dry or purge_state:
        print('State:')
        _rm_dir(_state_root(), dry or not purge_state)
    if dry or purge_memory:
        print('Memory rules (your saved preferences):')
        _rm_dir(path_config.get_memory_dir(), dry or not purge_memory)
    if dry or reset_config:
        print('Config mode keys:')
        _reset_config(dry or not reset_config)
    if dry or unregister:
        print('Copilot plugin registration:')
        if dry or not unregister:
            print('  would unregister preference-tracker from ~/.copilot/config.json')
        else:
            try:
                import subprocess
                subprocess.run([sys.executable, os.path.join(_LIB_DIR, 'register_plugin.py'),
                                '--unregister'], check=False)
            except Exception as e:
                print(f'  ERROR: {e}')

    print('-' * 60)
    if dry:
        print('Nothing was deleted. Re-run with --purge-state / --purge-memory / '
              '--reset-config / --unregister / --all to actually remove.')
    else:
        print('Done.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        sys.stderr.write(f'uninstall crashed: {type(e).__name__}: {e}\n')
        sys.exit(1)
