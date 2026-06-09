#!/usr/bin/env python3
"""transcript_adapter — read hook stdin + transcript across runtimes.

Copilot CLI and Claude Code deliver Stop-hook data with DIFFERENT shapes.
Without this adapter the Stop hooks silently no-op on Copilot (they look for
Claude field names / schema, find nothing, and pass everything through).

Differences handled:

stdin field names:
  Claude  : transcript_path, session_id, cwd
  Copilot : transcriptPath,  sessionId,  cwd

transcript entry schema (one JSON object per line):
  Claude  : {"type":"user"|"assistant", "message":{"content": <str | [{"type":"text","text":...}]>}}
  Copilot : {"type":"user.message"|"assistant.message", "data":{"content": <str>, "toolRequests":[...]}}

Public API:
  get_session_id(data)      -> str
  get_transcript_path(data) -> str | None
  get_cwd(data)             -> str | None
  read_transcript(data)     -> (response_text, last_user_text, tool_commands, raw_lines)
"""
import json
import os
from collections import deque

_MAX_LINES = 2000


def stdin_get(data, *names, default=None):
    """Return first present, non-None value among camelCase/snake_case names."""
    if isinstance(data, dict):
        for n in names:
            if data.get(n) is not None:
                return data[n]
    return default


def get_session_id(data):
    return stdin_get(data, 'session_id', 'sessionId', default='') or ''


def get_transcript_path(data):
    return stdin_get(data, 'transcript_path', 'transcriptPath')


def get_cwd(data):
    return stdin_get(data, 'cwd', 'workingDirectory')


def _role(o):
    """Normalize entry role to 'user' | 'assistant' | None across schemas."""
    t = o.get('type')
    if t in ('user', 'assistant'):
        return t
    if t == 'user.message':
        return 'user'
    if t == 'assistant.message':
        return 'assistant'
    return None


def _text_from_list(items):
    parts = []
    for it in items:
        if isinstance(it, dict) and it.get('type') == 'text':
            parts.append(it.get('text', ''))
    return '\n'.join(p for p in parts if p)


def _entry_text(o):
    """Natural-language text of an entry, both schemas (data.content or
    message.content or top-level content; str or list-of-text-blocks)."""
    for container_key in ('data', 'message'):
        c = o.get(container_key)
        if isinstance(c, dict) and 'content' in c:
            cc = c['content']
            if isinstance(cc, str):
                return cc
            if isinstance(cc, list):
                return _text_from_list(cc)
    cc = o.get('content')
    if isinstance(cc, str):
        return cc
    if isinstance(cc, list):
        return _text_from_list(cc)
    return ''


def _entry_tool_commands(o):
    """Command/argument strings from an assistant entry's tool requests
    (Copilot data.toolRequests). Used so /tmp-style rules can see shell
    commands the agent ran via tools, not just fenced code blocks."""
    out = []
    data = o.get('data')
    if isinstance(data, dict):
        reqs = data.get('toolRequests') or data.get('tool_requests') or []
        if isinstance(reqs, list):
            for tr in reqs:
                if not isinstance(tr, dict):
                    continue
                for key in ('command', 'input', 'arguments', 'args', 'parameters'):
                    v = tr.get(key)
                    if isinstance(v, str):
                        out.append(v)
                    elif isinstance(v, (dict, list)):
                        try:
                            out.append(json.dumps(v, ensure_ascii=False))
                        except Exception:
                            pass
    return out


def _iter_entries(lines):
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def read_transcript(data):
    """Return (response_text, last_user_text, tool_commands, raw_lines).

    response_text  = last non-empty assistant text of the latest turn.
    last_user_text = most recent user text.
    tool_commands  = command strings from the latest assistant turn's tools.
    raw_lines      = the tail transcript lines actually parsed (last _MAX_LINES).
    Any failure → ('', '', [], []).
    """
    path = get_transcript_path(data)
    if not path or not os.path.exists(path):
        return '', '', [], []
    try:
        # Tail-read only the last _MAX_LINES — a long session transcript can be
        # tens of MB and this runs in every Stop hook across several modules.
        # deque(maxlen=...) streams the file without holding it all in memory.
        with open(path, encoding='utf-8', errors='ignore') as f:
            lines = list(deque(f, maxlen=_MAX_LINES))
    except Exception:
        return '', '', [], []

    entries = list(_iter_entries(lines))

    last_user_idx = -1
    last_user = ''
    for i, o in enumerate(entries):
        if _role(o) == 'user':
            txt = _entry_text(o)
            if txt.strip():
                last_user_idx = i
                last_user = txt

    response = ''
    tool_commands = []
    for o in entries[last_user_idx + 1:]:
        if _role(o) == 'assistant':
            txt = _entry_text(o)
            if txt.strip():
                response = txt
            tool_commands.extend(_entry_tool_commands(o))

    return response, last_user, tool_commands, lines
