#!/usr/bin/env python3
"""Fast hook adapter that enqueues a turn without opening SQLite."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import hashlib
import math

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

import memory_upsert
import path_config
import pt_platform
import transcript_adapter


def _event_key(data: dict, mode: str, reference=None) -> str:
    session = transcript_adapter.get_session_id(data)
    if not isinstance(session, str) or not session.strip():
        return ''
    for key in ("turn_id", "turnId", "event_id", "eventId", "prompt_id", "promptId", "message_id"):
        value = data.get(key)
        if ((isinstance(value, str) and value.strip())
                or (type(value) is int and value >= 0)):
            return f"{mode}-{value}"
    timestamp = data.get('timestamp')
    valid_timestamp = ((isinstance(timestamp, str) and bool(timestamp.strip()))
                       or (type(timestamp) in {int, float} and math.isfinite(timestamp)))
    if reference is None and not valid_timestamp:
        return ''
    identity = {'session': session,
                'timestamp': timestamp if valid_timestamp else None, 'reference': reference,
                'prompt': data.get('prompt') or data.get('user_prompt') or ''}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return f'{mode}-{digest}'


def _load_stdin() -> dict:
    try:
        value = json.load(sys.stdin)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _log_hook_error(exc: Exception) -> None:
    try:
        state_dir = Path(path_config.get_state_dir())
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / "memory_upsert_hook_errors.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                f"{type(exc).__name__}: {str(exc)[:500]}\n"
            )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        pass


def enqueue_from_hook(data: dict, mode: str) -> dict:
    disabled_by_guard = any(
        os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
        for name in (
            "B5_RETRIEVE_RECURSION_GUARD",
            "PT_MEMORY_UPSERT_DISABLED",
        )
    )
    if pt_platform.is_child_session() or disabled_by_guard or not memory_upsert.hooks_enabled():
        return {"status": "disabled"}
    if mode == "stop" and (data.get("stop_hook_active") or data.get("stopHookActive")):
        return {"status": "reentry_skipped"}
    if mode != "prompt":
        return {"status": "prompt_only"}
    cwd = transcript_adapter.get_cwd(data)
    if cwd:
        os.environ["PT_PROJECT_ROOT"] = cwd
        os.environ["B5_PROJECT_ROOT"] = cwd
        path_config.get_project_root.cache_clear()
        path_config.get_memory_dir.cache_clear()
    session_id = transcript_adapter.get_session_id(data) or "unknown-session"
    source_text = data.get("prompt") or data.get("user_prompt") or ""
    if not isinstance(source_text, str):
        return {'status': 'invalid_prompt', 'blocking': False}
    if not transcript_adapter.is_trusted_user_entry(data, source_text):
        return {"status": "empty"}
    context_parts = []
    if cwd:
        context_parts.insert(0, f"Current project root: {cwd}")
    context = "\n\n".join(context_parts)
    if not source_text.strip():
        return {"status": "empty"}
    reference = transcript_adapter.capture_context_reference(data)
    event_key = _event_key(data, mode, reference)
    if not event_key:
        _log_hook_error(ValueError('automatic learning skipped: no stable prompt event identity'))
        return {'status': 'missing_event_identity', 'blocking': False}
    turn_key = f"{pt_platform.CLI_COMMAND}-{session_id}-{event_key}"
    return memory_upsert.enqueue(
        source_text=source_text,
        turn_key=turn_key,
        context=context,
        memory_dir=path_config.get_memory_dir(),
        spawn_worker=True,
        detect_signal=True,
        context_reference=reference,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prompt", "stop"))
    args = parser.parse_args(argv)
    try:
        enqueue_from_hook(_load_stdin(), args.mode)
    except Exception as exc:
        _log_hook_error(exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
