#!/usr/bin/env python3
"""Helper for install.ps1 — avoids PowerShell string escaping issues with Python oneliners."""
import os
import sys

_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib')
sys.path.insert(0, _LIB)
import path_config  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("Usage: _install_helper.py <command>", file=sys.stderr)
        print("Commands: ensure-dirs, get-memory-dir, get-state-dir", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    path_config._clear_cache()

    if cmd == 'ensure-dirs':
        path_config.ensure_dirs()
    elif cmd == 'get-memory-dir':
        print(path_config.get_memory_dir())
    elif cmd == 'get-state-dir':
        print(path_config.get_state_dir())
    elif cmd == 'get-obs-log-dir':
        print(path_config.get_obs_log_dir())
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
