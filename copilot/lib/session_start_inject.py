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

# Windows pipes default to cp1252; we print Chinese additionalContext, which
# would raise UnicodeEncodeError and silently drop injection. Force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None and hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8')
    except Exception:
        pass

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
            encoding='utf-8',
            timeout=timeout,
            env=env,
        )
    except Exception:
        return ''

    if result.returncode != 0:
        return ''
    return result.stdout.strip()


def main() -> None:
    # Child-session guard: if this SessionStart fired inside a nested `copilot -p`
    # subprocess that the skill itself spawned (e.g. the shadow judge), do nothing
    # — don't re-inject memory into a throwaway child session.
    if os.environ.get('PT_CHILD_SESSION', '').strip().lower() in ('1', 'true', 'yes', 'on') \
            or os.environ.get('B5_INJECT_DISABLED', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        sys.exit(0)

    raw_input = '' if sys.stdin.isatty() else sys.stdin.read()
    payload = {}
    if raw_input.strip():
        try:
            parsed = json.loads(raw_input)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}

    # Accept either key Copilot/Claude may send (mirrors transcript_adapter.get_cwd).
    cwd = payload.get('cwd') or payload.get('workingDirectory')
    if cwd:
        os.environ['B5_PROJECT_ROOT'] = str(cwd)

    parts: list[str] = []
    forwarded_input = raw_input if raw_input.strip() else '{}'

    # SessionStart mode: use --session-start to inject top critical/high rules
    # without prompt matching. This replaces the v1 no-op behavior.
    if os.environ.get('B5_RETRIEVE_RECURSION_GUARD') != '1':
        ctx = _extract_context(_run_entry(
            'retrieve_inject.py',
            args=['--session-start'],
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
        # Copilot CLI injects the TOP-LEVEL `additionalContext` field and SILENTLY
        # IGNORES `hookSpecificOutput.additionalContext`. Verified live 2026-06-05
        # via `copilot -p`: the default hookSpecificOutput shape → child reports
        # NO-INJECTION-FOUND; top-level → child quotes the full injection. The CLI
        # bundle confirms it (the SessionStart output mapper reads `c.additionalContext`).
        # So default to top-level. Copilot's parser is lenient (no
        # additionalProperties:false), so the "extra key rejects the whole object"
        # fear did not materialize. Set PT_SESSIONSTART_HOOKSPECIFIC=1 to emit the
        # Claude-style hookSpecificOutput envelope instead (e.g. when reusing the
        # same module on Claude Code, which reads that shape).
        if os.environ.get('PT_SESSIONSTART_HOOKSPECIFIC', '').strip().lower() in ('1', 'true', 'yes', 'on'):
            print(json.dumps({
                'hookSpecificOutput': {
                    'hookEventName': 'SessionStart',
                    'additionalContext': combined,
                }
            }, ensure_ascii=False))
        else:
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
