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
  recent_context(lines)     -> compact context excluding the latest user turn
"""
import json
import os
import stat
import hashlib
from collections import deque

_MAX_LINES = 2000
_SYNTHETIC_USER_PREFIXES = (
    '<system-reminder>',
    '<system_reminder>',
    '<task-notification>',
    '<task_notification>',
    '<local-command-',
    '<local_command_',
)


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


def get_presentation_key(data):
    """Stable key shared by retrieve and enqueue hooks for one conversation."""
    session_id = get_session_id(data)
    if session_id:
        return f"session:{session_id}"
    transcript_path = get_transcript_path(data)
    if transcript_path:
        return f"transcript:{os.path.abspath(str(transcript_path))}"
    return ''


def capture_context_reference(data):
    """Capture a boundary and fingerprint at most 64 KiB, without parsing text.

    Never search directories or follow a reference to another conversation.
    Missing identity/path simply leaves the worker with the user prompt alone.
    """
    session = get_session_id(data)
    path = get_transcript_path(data)
    if not isinstance(session, str) or not session.strip() or not isinstance(path, str) or not path:
        return None
    path = os.path.abspath(path)
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None
            start = max(0, info.st_size - 65535)
            stream.seek(max(0, start - 1))
            preceding = stream.read(1) if start else b''
            raw = stream.read(info.st_size - start)
            after = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None
        return {'schema': 1, 'path': path, 'session_id': session,
                'device': info.st_dev, 'inode': info.st_ino, 'start': start,
                'end': info.st_size, 'mtime_ns': info.st_mtime_ns,
                'line_aligned': start == 0 or preceding == b'\n',
                'sha256': hashlib.sha256(raw).hexdigest()}
    except OSError:
        return None


def read_context_reference(reference, *, before=None, max_bytes=65536):
    """Read one bounded page backwards inside the captured conversation only.

    `next_before` allows a background resolver to request older context without
    adding future messages. Transcript files are expected to be append-only;
    replacement, truncation and in-place rewriting of the captured window are
    rejected. Older pages stay inside the fingerprinted window. This constant
    size byte fingerprint does not parse/scan assistant responses for signals.
    No path discovered inside transcript content is ever opened.
    """
    unavailable = {'status': 'unavailable', 'context': '', 'truncated': False,
                   'next_before': None}
    if not isinstance(reference, dict) or reference.get('schema') != 1:
        return unavailable
    try:
        path, session = reference['path'], reference['session_id']
        end = reference['end']
        lower = reference['start']
        if (not isinstance(path, str) or not os.path.isabs(path)
                or not isinstance(session, str) or not session
                or type(end) is not int or end < 0
                or type(lower) is not int or not 0 <= lower <= end
                or end - lower > 65536
                or type(max_bytes) is not int or not 1 <= max_bytes <= 1048576):
            return unavailable
        stop = end if before is None else before
        if type(stop) is not int or not lower <= stop <= end:
            return unavailable
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode)
                    or (info.st_dev, info.st_ino) != (reference['device'], reference['inode'])
                    or info.st_size < end
                    or (info.st_size == end and info.st_mtime_ns != reference['mtime_ns'])):
                return unavailable
            stream.seek(lower)
            window = stream.read(end - lower)
            if hashlib.sha256(window).hexdigest() != reference['sha256']:
                return unavailable
            start = max(lower, stop - max_bytes)
            raw = window[start-lower:stop-lower]
        # Move to the next complete line; return that boundary so an older
        # page can recover the preceding message without duplicating this one.
        initial_start = start
        aligned = reference.get('line_aligned', False) if start == lower else window[start-lower-1:start-lower] == b'\n'
        if start and not aligned:
            first_newline = raw.find(b'\n')
            if first_newline < 0:
                return {'status': 'truncated', 'context': '', 'truncated': True,
                        'next_before': start if start > lower else None}
            start += first_newline + 1
            raw = raw[first_newline + 1:]
        entries = list(_iter_entries(raw.decode('utf-8', errors='replace').splitlines()))
        rendered = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            item_session = get_session_id(item)
            if item.get('type') == 'session_meta' and isinstance(item.get('payload'), dict):
                item_session = item['payload'].get('id') or item_session
            if item_session and item_session != session:
                return unavailable
            role = _role(item)
            text = _entry_text(item).strip()
            if role and text and (role != 'user' or is_trusted_user_entry(item, text)):
                rendered.append(f'{role.title()}: {text}')
        next_before = start if start < stop else initial_start
        return {'status': 'ok', 'context': '\n'.join(rendered),
                'truncated': start > 0,
                'next_before': next_before if initial_start > lower and next_before < stop else None}
    except (OSError, KeyError, TypeError, ValueError):
        return unavailable


def _role(o):
    """Normalize entry role to 'user' | 'assistant' | None across schemas."""
    t = o.get('type')
    if t in ('user', 'assistant'):
        return t
    if t == 'user.message':
        return 'user'
    if t == 'assistant.message':
        return 'assistant'
    if t == 'response_item':
        payload = o.get('payload')
        if isinstance(payload, dict) and payload.get('type') == 'message':
            role = payload.get('role')
            return role if role in {'user', 'assistant'} else None
    return None


def _text_from_list(items):
    parts = []
    for it in items:
        if isinstance(it, dict) and it.get('type') in {'text', 'input_text', 'output_text'}:
            parts.append(it.get('text', ''))
    return '\n'.join(p for p in parts if p)


def _entry_text(o):
    """Natural-language text of an entry, both schemas (data.content or
    message.content or top-level content; str or list-of-text-blocks)."""
    for container_key in ('data', 'message', 'payload'):
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


def is_trusted_user_entry(entry, text=None):
    """Return whether a user entry is genuine user-authored authorization."""
    if not isinstance(entry, dict):
        return False
    for container in (entry, entry.get('data'), entry.get('message'), entry.get('payload')):
        if isinstance(container, dict):
            meta = container.get('isMeta')
            if meta is None:
                meta = container.get('is_meta')
            if meta is True or str(meta).strip().lower() in {'1', 'true', 'yes', 'on'}:
                return False
    candidate = str(text if text is not None else _entry_text(entry)).lstrip().lower()
    return bool(candidate) and not candidate.startswith(_SYNTHETIC_USER_PREFIXES)


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
            if txt.strip() and is_trusted_user_entry(o, txt):
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


def recent_context(lines, max_messages=6, max_chars=6000):
    """Return nearby conversation as untrusted disambiguation context.

    The latest user message is excluded because memory_upsert passes it
    separately as the only trusted authorization source. Earlier messages and
    the current assistant response may resolve project, referent, or phase
    ambiguity but cannot create a durable rule on their own.
    """
    entries = []
    for item in _iter_entries(lines or []):
        role = _role(item)
        text = _entry_text(item).strip()
        if role and text and (role != 'user' or is_trusted_user_entry(item, text)):
            entries.append((role, text))
    latest_user_index = -1
    for index, (role, _text) in enumerate(entries):
        if role == 'user':
            latest_user_index = index
    if latest_user_index < 0:
        return ''
    nearby = (
        entries[max(0, latest_user_index - max_messages):latest_user_index]
        + entries[latest_user_index + 1:]
    )
    rendered = []
    for role, text in nearby[-max_messages:]:
        remaining = max_chars - sum(len(item) + 1 for item in rendered)
        if remaining <= 0:
            break
        label = 'User' if role == 'user' else 'Assistant'
        rendered.append(f'{label}: {text[:remaining]}')
    return '\n'.join(rendered)
