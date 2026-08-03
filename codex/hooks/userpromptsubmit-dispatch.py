#!/usr/bin/env python3
"""Run Codex UserPromptSubmit handlers in a deterministic sequence."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
from contextlib import redirect_stdout


def _run_main(module, payload: str) -> dict:
    original_stdin = sys.stdin
    output = io.StringIO()
    try:
        sys.stdin = io.StringIO(payload)
        with redirect_stdout(output):
            try:
                module.main()
            except SystemExit:
                pass
    finally:
        sys.stdin = original_stdin
    text = output.getvalue().strip()
    if not text:
        return {}
    value = json.loads(text)
    return value if isinstance(value, dict) else {}


def _merge_context(outputs: list[dict]) -> dict:
    parts = []
    for output in outputs:
        specific = output.get("hookSpecificOutput")
        if not isinstance(specific, dict):
            continue
        context = str(specific.get("additionalContext") or "").strip()
        if context:
            parts.append(context)
    if not parts:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(parts),
        }
    }


def main() -> int:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw_payload = stream.read()
    payload = (
        raw_payload.decode("utf-8", errors="replace")
        if isinstance(raw_payload, bytes)
        else raw_payload
    )
    try:
        data = json.loads(payload)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0

    cwd = str(data.get("cwd") or "").strip()
    if cwd and Path(cwd).is_dir():
        if (Path(cwd) / ".codex" / "tellonce.disabled").is_file():
            return 0
        os.environ["PT_PROJECT_ROOT"] = cwd
        os.environ["B5_PROJECT_ROOT"] = cwd

    shared_lib = Path(__file__).resolve().parent.parent / "shared_lib"
    sys.path.insert(0, str(shared_lib))

    outputs = []
    try:
        import memory_upsert_hook

        memory_upsert_hook.enqueue_from_hook(data, "prompt")
    except Exception:
        pass
    try:
        import retrieve_inject

        outputs.append(_run_main(retrieve_inject, payload))
    except Exception:
        pass
    try:
        import shadow_alert_inject

        outputs.append(_run_main(shadow_alert_inject, payload))
    except Exception:
        pass

    merged = _merge_context(outputs)
    if merged:
        sys.stdout.write(json.dumps(merged, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(0)
