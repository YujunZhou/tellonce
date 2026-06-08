#!/usr/bin/env python3
"""Optional, experimental heuristic: classify the user's last message as
urgent ('u') or clarity ('c') preferring. OFF by default (PT_PREFER_BACKEND=off).

When enabled, deterministic_block's hook short-circuit can skip the regex
word-check only when the user prefers an urgent response. Defaults to urgent
('u') on any failure (cheaper degradation).

CLI: python3 detect_user_prefer.py <transcript_path>
Stdout: single char 'u' or 'c'.

C6 fix (2026-05-01 review): default is now NO-API. The previous behaviour
silently called Anthropic SDK on every Stop where last obs entry had
detected=false (the base-rate path), charging the user's API key without
any visible signal. Reviewed env contract:

  PT_PREFER_BACKEND=off (default)   → return 'u' immediately, no API call,
                                      no token spend. Behaviour: short-circuit
                                      gate is fully open on detected=false,
                                      identical to prior behaviour ON FAILURE.
  PT_PREFER_BACKEND=sdk             → Anthropic Python SDK (legacy path).
                                      Charges per call; user opts in.
  PT_PREFER_BACKEND=cli             → Use `claude -p` subprocess (subscription
                                      mode, 0 metered cost).

This means the deterministic_block short-circuit still works (urgent = skip)
but no longer silently bills users. To re-enable adaptive classification
explicitly: `export PT_PREFER_BACKEND=cli` (subscription) or `=sdk` (API).
"""
import json
import os
import subprocess
import sys


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
    """`claude -p` subprocess (subscription mode, 0 metered cost). Opt-in via
    PT_PREFER_BACKEND=cli. Falls back to 'u' on any error."""
    try:
        proc = subprocess.run(
            ['claude', '-p', _CLASSIFY_PROMPT.format(user_msg=user_msg),
             '--model', _PREFER_MODEL, '--output-format', 'text'],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 'u'
    except Exception:
        return 'u'
    if proc.returncode != 0:
        return 'u'
    out = (proc.stdout or '').strip().lower()
    return 'c' if out.startswith('c') else 'u'


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
