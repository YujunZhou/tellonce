#!/usr/bin/env python3
"""SessionStart hook entry point — combines retrieve + pending + shadow alert injection.

Copilot CLI port: replaces 3 separate Claude UserPromptSubmit hooks with one
SessionStart hook that injects all context at session start.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

THIS_LIB = Path(__file__).resolve().parent
# All lib modules live alongside this file in copilot/lib/
ROOT_LIB = THIS_LIB
_RUNPY_SHIM = """
import os
import runpy
import sys

script_path = sys.argv[1]
script_args = sys.argv[2:]
copilot_lib = os.environ.get('COPILOT_PT_LIB')
root_lib = os.environ.get('ROOT_PT_LIB')
if copilot_lib:
    sys.path.insert(0, copilot_lib)
if root_lib:
    sys.path.insert(1, root_lib)
sys.argv = [script_path, *script_args]
runpy.run_path(script_path, run_name='__main__')
"""


def _extract_context(text: str) -> str:
    text = (text or '').strip()
    if not text:
        return ''
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(data, dict):
        hook_output = data.get('hookSpecificOutput') or {}
        if isinstance(hook_output, dict):
            return (hook_output.get('additionalContext') or '').strip()
        return (data.get('additionalContext') or '').strip()
    return ''


def _run_entry(script_name: str, args: list[str] | None = None, *, stdin_text: str = '', timeout: int = 5,
               extra_env: dict[str, str] | None = None) -> str:
    script_path = ROOT_LIB / script_name
    if not script_path.exists():
        return ''

    env = os.environ.copy()
    env['COPILOT_PT_LIB'] = str(THIS_LIB)
    env['ROOT_PT_LIB'] = str(ROOT_LIB)
    if extra_env:
        env.update(extra_env)

    try:
        result = subprocess.run(
            [sys.executable, '-c', _RUNPY_SHIM, str(script_path), *(args or [])],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except Exception:
        return ''

    if result.returncode != 0:
        return ''
    return result.stdout.strip()


def main() -> None:
    raw_input = '' if sys.stdin.isatty() else sys.stdin.read()
    payload = {}
    if raw_input.strip():
        try:
            parsed = json.loads(raw_input)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}

    if payload.get('cwd'):
        os.environ['B5_PROJECT_ROOT'] = str(payload['cwd'])

    parts: list[str] = []
    forwarded_input = raw_input if raw_input.strip() else '{}'

    # NOTE: retrieve_inject.py requires data["prompt"] to trigger keyword matching.
    # SessionStart events don't include a prompt, so this call is effectively a
    # no-op in Copilot (known v1 limitation — Claude fires on UserPromptSubmit
    # which includes the prompt). Kept for forward-compatibility if Copilot adds
    # prompt to SessionStart payload, or if B5_RETRIEVE_CLI is set to a mode
    # that doesn't need prompt-based triggering.
    if os.environ.get('B5_RETRIEVE_RECURSION_GUARD') != '1':
        ctx = _extract_context(_run_entry(
            'retrieve_inject.py',
            stdin_text=forwarded_input,
            timeout=10,
            extra_env={'B5_RETRIEVE_RECURSION_GUARD': '1'},
        ))
        if ctx:
            parts.append(ctx)

    pending_text = _run_entry('pending_queue_manager.py', ['inject'], timeout=5)
    if pending_text:
        header = '### Pending memory finalize required (carried over from prior session):'
        parts.append(header + '\n' + pending_text)

    shadow_ctx = _extract_context(_run_entry(
        'shadow_alert_inject.py',
        stdin_text=forwarded_input,
        timeout=5,
    ))
    if shadow_ctx:
        parts.append(shadow_ctx)

    if parts:
        combined = '\n\n---\n\n'.join(parts)
        print(json.dumps({'additionalContext': combined}, ensure_ascii=False))

    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass
    sys.exit(0)
