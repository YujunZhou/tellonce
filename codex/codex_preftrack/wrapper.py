from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import time

from .ledger import append_event, event_id
from .mode import load_mode, write_mode
from .verify import verify_output


@dataclass(frozen=True)
class WrappedRun:
    run_id: str
    exit_code: int


def run_wrapped(state_root: Path, cmd: list[str], timeout_s: int = 120) -> WrappedRun:
    run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + event_id("wrapper").split("-")[-1]
    run_dir = state_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"run_id": run_id, "cmd": cmd, "mode": "wrapper"}
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    append_event(state_root, {"event_type": "wrapper_run_started", "session_id": "codex-current", "payload": meta})
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_s, check=False)
        stdout = proc.stdout
        stderr = proc.stderr
        rc = proc.returncode
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        stdout = getattr(exc, "stdout", "") or ""
        stderr = str(exc)
        rc = 3
    (run_dir / "original_stdout.txt").write_text(stdout, encoding="utf-8")
    (run_dir / "original_stderr.txt").write_text(stderr, encoding="utf-8")
    verdict = verify_output(stdout)
    (run_dir / "verdicts.jsonl").write_text(json.dumps(verdict.__dict__) + "\n", encoding="utf-8")
    append_event(
        state_root,
        {
            "event_type": "wrapper_run_completed",
            "session_id": "codex-current",
            "payload": {"run_id": run_id, "exit_code": rc, "verdict": verdict.verdict},
        },
    )
    current_mode = load_mode(state_root)
    write_mode(state_root, mode="wrapper" if current_mode.mode == "audit_only" else current_mode.mode, wrapper_seen=True)
    return WrappedRun(run_id=run_id, exit_code=rc)
