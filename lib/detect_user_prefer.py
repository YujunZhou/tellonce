#!/usr/bin/env python3
"""Classify user's last message as urgent ('u') or clarity ('c') prefer.

Used by deterministic_block hook short-circuit (per wf-pref-320 + user
v23 day-1 refinement): only skip the regex word-check when user prefers
urgent response. Default urgent on any failure (cheaper degradation).

CLI: python3 detect_user_prefer.py <transcript_path>
Stdout: single char 'u' or 'c'.

Latency: ~300-500ms via Haiku 4.5 + max_tokens=2 + no thinking. Acceptable
because called only when detected=False AND only for deterministic_block
(every day ~5 trips per dashboard 6d data).
"""
import json
import os
import sys


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


def classify(user_msg: str) -> str:
    """Return 'u' (urgent) or 'c' (clarity). Default 'u' on any error."""
    if not user_msg.strip():
        return 'u'
    try:
        import anthropic
    except ImportError:
        return 'u'
    try:
        client = anthropic.Anthropic()
    except Exception:
        return 'u'
    prompt = (
        f"User's last message:\n{user_msg}\n\n"
        "Does the user prefer URGENT (fast, terse, action-oriented) "
        "or CLARITY (detailed, explained, careful) response right now? "
        "Reply ONE letter only: u or c."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2,
            messages=[{"role": "user", "content": prompt}],
        )
        out = resp.content[0].text.strip().lower()
        return 'c' if out.startswith('c') else 'u'
    except Exception:
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
