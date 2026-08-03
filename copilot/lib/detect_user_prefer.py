#!/usr/bin/env python3
"""Optional, experimental heuristic: classify the user's last message as
urgent ('u') or clarity ('c') preferring. OFF by default (PT_PREFER_BACKEND=off).

When enabled, deterministic_block's hook short-circuit can skip the regex
word-check only when the user prefers an urgent response. Defaults to urgent
('u') on any failure (cheaper degradation).

CLI: python3 detect_user_prefer.py <transcript_path>
Stdout: single char 'u' or 'c'.

Default is now NO-API. The previous behaviour
silently called Anthropic SDK on every Stop where last obs entry had
detected=false (the base-rate path), charging the user's API key without
any visible signal. Reviewed env contract:

  PT_PREFER_BACKEND=off (default)   → return 'u' immediately, no API call,
                                      no token spend. Behaviour: short-circuit
                                      gate is fully open on detected=false,
                                      identical to prior behaviour ON FAILURE.
  PT_PREFER_BACKEND=sdk             → Anthropic Python SDK (legacy path).
                                      Charges per call; user opts in.
  PT_PREFER_BACKEND=cli             → Use the platform CLI in print mode (subscription
                                      mode, 0 metered cost).

This means the deterministic_block short-circuit still works (urgent = skip)
but no longer silently bills users. To re-enable adaptive classification
explicitly: `export PT_PREFER_BACKEND=cli` (subscription) or `=sdk` (API).
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pt_platform  # platform-specific values (this variant)


_BACKEND = os.environ.get('PT_PREFER_BACKEND', 'off').strip().lower()
_PREFER_MODEL = os.environ.get('PT_PREFER_MODEL', 'claude-haiku-4-5')


def _read_last_user_msg(transcript_path: str, max_chars: int = 200) -> str:
    """Tail transcript for the latest user message text. Returns truncated."""
    try:
        with open(transcript_path, errors='ignore') as f:
            lines = list(f)
    except Exception:
        return ''
    for line in reversed(lines):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get('type') != 'user':
            continue
        content = o.get('message', {}).get('content', '')
        text = ''
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get('type') == 'text':
                    text = c.get('text', '')
                    break
        # Skip empty + system reminders + tool result wrappers
        if not text or text.startswith('<system-reminder>') or text.startswith('<task-notification>'):
            continue
        return text[:max_chars]
    return ''


_CLASSIFY_PROMPT = (
    "User's last message:\n{user_msg}\n\n"
    "Does the user prefer URGENT (fast, terse, action-oriented) "
    "or CLARITY (detailed, explained, careful) response right now? "
    "Reply ONE letter only: u or c."
)


def _classify_via_sdk(user_msg: str) -> str:
    """Anthropic Python SDK call (charges API credit). Opt-in via
    PT_PREFER_BACKEND=sdk."""
    try:
        import anthropic
    except ImportError:
        return 'u'
    try:
        client = anthropic.Anthropic()
    except Exception:
        return 'u'
    try:
        resp = client.messages.create(
            model=_PREFER_MODEL,
            max_tokens=2,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(user_msg=user_msg)}],
        )
        out = resp.content[0].text.strip().lower()
        return 'c' if out.startswith('c') else 'u'
    except Exception:
        return 'u'


def _classify_via_cli(user_msg: str) -> str:
    """Platform CLI (-p) subprocess (subscription mode, 0 metered cost). Opt-in via
    PT_PREFER_BACKEND=cli. Falls back to 'u' on any error."""
    output_path = None
    try:
        prompt = _CLASSIFY_PROMPT.format(user_msg=user_msg)
        cli = pt_platform.CLI_COMMAND
        _cli_model = os.environ.get('PT_PREFER_MODEL', pt_platform.PREFER_MODEL_DEFAULT).strip()
        prompt_input = None
        if cli == 'codex':
            output_fd, output_path = tempfile.mkstemp(
                prefix='tellonce-prefer-',
                suffix='.txt',
            )
            os.close(output_fd)
            _cmd = [
                'codex', 'exec', '--ephemeral', '--ignore-user-config',
                '--ignore-rules', '--skip-git-repo-check',
                '--sandbox', 'read-only',
                '--output-last-message', output_path,
            ]
            if _cli_model:
                _cmd += ['-m', _cli_model]
            prompt_input = prompt
        elif cli == 'claude':
            _cmd = ['claude', '-p']
            if _cli_model:
                _cmd += ['--model', _cli_model]
            _cmd += ['--output-format', 'text', '--setting-sources', 'project']
            prompt_input = prompt
        else:
            _cmd = [cli, '-p', prompt]
            if _cli_model:
                _cmd += ['--model', _cli_model]
            _cmd += ['--output-format', 'text']
        child_env = dict(os.environ)
        child_env["PT_CHILD_SESSION"] = "1"
        child_env["B5_RETRIEVE_RECURSION_GUARD"] = "1"
        child_env["B5_DETERMINISTIC_DISABLED"] = "1"
        child_env["B5_SHADOW_DISABLED"] = "1"
        child_env["B5_INJECT_DISABLED"] = "1"
        proc = subprocess.run(
            _cmd,
            input=prompt_input,
            capture_output=True, text=True, encoding='utf-8', timeout=15,
            env=child_env,
            cwd=tempfile.gettempdir(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        if output_path:
            try:
                os.unlink(output_path)
            except OSError:
                pass
        return 'u'
    except Exception:
        if output_path:
            try:
                os.unlink(output_path)
            except OSError:
                pass
        return 'u'
    try:
        if proc.returncode != 0:
            return 'u'
        if output_path and os.path.isfile(output_path):
            out = Path(output_path).read_text(encoding='utf-8', errors='replace')
        else:
            out = proc.stdout or ''
        return 'c' if out.strip().lower().startswith('c') else 'u'
    finally:
        if output_path:
            try:
                os.unlink(output_path)
            except OSError:
                pass


def classify(user_msg: str) -> str:
    """Return 'u' (urgent) or 'c' (clarity). Default 'u' on any error or when
    PT_PREFER_BACKEND is unset / off (no API call, no subscription cost)."""
    if not user_msg.strip():
        return 'u'
    backend = _BACKEND
    if backend == 'sdk':
        return _classify_via_sdk(user_msg)
    if backend == 'cli':
        return _classify_via_cli(user_msg)
    # backend == 'off' or unrecognized → no remote call, default to urgent
    return 'u'


def main() -> int:
    if len(sys.argv) < 2:
        print('u')
        return 0
    transcript = sys.argv[1]
    user_msg = _read_last_user_msg(transcript)
    print(classify(user_msg))
    return 0


if __name__ == '__main__':
    sys.exit(main())
