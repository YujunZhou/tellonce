#!/usr/bin/env python3
"""uninstall — remove tellonce state/memory/config for the Copilot plugin.

The plugin code itself is removed by `copilot plugin uninstall tellonce`.
This helper cleans up the per-project state, the saved memory rules, and the
mode keys this skill wrote to ~/.tellonce.config.json.

SAFE BY DEFAULT: with no flags it only PRINTS what it would remove (dry run).
Pass explicit flags to actually delete:

    python <plugin>/lib/uninstall.py                 # dry run (show only)
    python <plugin>/lib/uninstall.py --purge-state   # delete .copilot/tellonce-state/
    python <plugin>/lib/uninstall.py --purge-memory  # delete the memory/ rules
    python <plugin>/lib/uninstall.py --reset-config   # remove enforce/shadow keys (back to observe)
    python <plugin>/lib/uninstall.py --unregister     # remove from Copilot's installedPlugins
    python <plugin>/lib/uninstall.py --all            # integration/state/config; memory kept
    python <plugin>/lib/uninstall.py --purge-memory --confirm-shared-memory

User data (memory rules) is preserved unless you ask for --purge-memory.
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
    # parent of the runtime dir = .../.copilot/tellonce-state
    return os.path.dirname(path_config.get_state_dir())


def _rm_dir(path, dry):
    if not path or not os.path.isdir(path):
        print(f'  (skip) not present: {path}')
        return True
    if dry:
        print(f'  would remove dir: {path}')
        return True
    else:
        try:
            shutil.rmtree(path)
            print(f'  removed dir: {path}')
            return True
        except Exception as e:
            print(f'  ERROR removing {path}: {type(e).__name__}: {e}')
            return False


def _reset_config(dry):
    p = path_config.CONFIG_PATH
    if not os.path.exists(p):
        print(f'  (skip) no config at {p}')
        return True
    try:
        with open(p, encoding='utf-8-sig') as f:
            cfg = json.load(f)
    except Exception as e:
        print(f'  ERROR reading config: {e}')
        return False
    removed = [k for k in ('enforce', 'shadow') if k in cfg]
    if not removed:
        print('  (skip) config has no enforce/shadow keys')
        return True
    if dry:
        print(f'  would remove config keys {removed} from {p} (retrieve_* kept)')
        return True
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
        return True
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        print(f'  ERROR writing config: {e}')
        return False


def main():
    args = set(sys.argv[1:])
    do_all = '--all' in args
    purge_state = do_all or '--purge-state' in args
    purge_memory = '--purge-memory' in args
    confirm_shared_memory = '--confirm-shared-memory' in args
    reset_config = do_all or '--reset-config' in args
    unregister = do_all or '--unregister' in args
    dry = not (purge_state or purge_memory or reset_config or unregister)

    if purge_memory and not confirm_shared_memory:
        print(
            'ERROR: memory is shared by Claude, Copilot, and Codex. '
            'Re-run with --purge-memory --confirm-shared-memory.',
            file=sys.stderr,
        )
        return 2

    print('tellonce uninstall' + (' (DRY RUN — pass flags to act)' if dry else ''))
    print('-' * 60)
    print('To remove the plugin code itself, run:')
    print('  copilot plugin uninstall tellonce')
    print('-' * 60)
    ok = True

    if dry or purge_state:
        print('State:')
        ok = _rm_dir(_state_root(), dry or not purge_state) and ok
    if dry or purge_memory:
        print('Memory rules (your saved preferences):')
        ok = _rm_dir(path_config.get_memory_dir(), dry or not purge_memory) and ok
    if dry or reset_config:
        print('Config mode keys:')
        ok = _reset_config(dry or not reset_config) and ok
    if dry or unregister:
        print('Copilot plugin registration:')
        if dry or not unregister:
            print('  would unregister tellonce from ~/.copilot/config.json')
        else:
            try:
                import subprocess
                completed = subprocess.run(
                    [
                        sys.executable,
                        os.path.join(_LIB_DIR, 'register_plugin.py'),
                        '--unregister',
                    ],
                    check=False,
                )
                if completed.returncode != 0:
                    print(f'  ERROR: unregister exited {completed.returncode}')
                    ok = False
            except Exception as e:
                print(f'  ERROR: {e}')
                ok = False

    print('-' * 60)
    if dry:
        print('Nothing was deleted. Re-run with --purge-state / --reset-config / '
              '--unregister / --all. Shared memory needs --purge-memory '
              '--confirm-shared-memory.')
    else:
        print('Done.' if ok else 'Completed with errors.')
    return 0 if ok else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        sys.stderr.write(f'uninstall crashed: {type(e).__name__}: {e}\n')
        sys.exit(1)
