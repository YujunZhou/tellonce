#!/usr/bin/env python3
"""Manage preference-tracker entries in ~/.codex/hooks.json.

Codex hook event names + JSON in/out protocol mirror Claude Code's: stdin
JSON with prompt / tool_name / tool_input / tool_response; stdout JSON with
hookSpecificOutput.additionalContext etc.; exit 2 = block with reason on stderr.

Usage:
  python3 -m codex_preftrack.install_codex_hooks --add
  python3 -m codex_preftrack.install_codex_hooks --remove
  python3 -m codex_preftrack.install_codex_hooks --verify

Sentinel: every PT-managed entry tagged `_pt_managed: true` so cleanup is exact
and we don't trample user's own hook entries.

Hook layout (mirrors CC's UserPromptSubmit chain + adds PostToolUse to fill the
gap that codex doesn't have a Stop hook):

  UserPromptSubmit:
    - userpromptsubmit-retrieve-inject.sh    (retrieve memory rules, inject
      additionalContext)
    - userpromptsubmit-pending-inject.sh     (cross-session pending memory
      reminders)
    - userpromptsubmit-shadow-alert-inject.sh (last-turn shadow violation
      alerts -> next turn fix)

  PostToolUse:
    - posttooluse-deterministic-block.sh     (regex/fingerprint scan tool
      output text; advisory by default, blocking when mode=='blocking')

  SessionStart:
    - sessionstart-init.sh                   (lazy init project state + mode
      file when codex enters a fresh project)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


PT_HOOKS_DEFAULT_DIR = Path.home() / ".codex" / "skills" / "preference-tracker" / "hooks"

# event_name -> ordered list of hook script basenames (run in order)
PT_HOOKS = {
    "UserPromptSubmit": [
        ("userpromptsubmit-retrieve-inject.sh", 30),
        ("userpromptsubmit-pending-inject.sh", 10),
        ("userpromptsubmit-shadow-alert-inject.sh", 10),
    ],
    "PostToolUse": [
        ("posttooluse-deterministic-block.sh", 15),
    ],
    "SessionStart": [
        ("sessionstart-init.sh", 10),
    ],
}


def _load_hooks_json(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        with p.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _versioned_backup(path: str) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.v3_pre_pt_{ts}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    Path(backup).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def _save_hooks_json(path: str, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


def _is_pt_command(cmd: str) -> bool:
    """Identify any registration string we previously wrote so cleanup is safe."""
    return "preference-tracker" in cmd and (cmd.endswith(".sh") or "/hooks/" in cmd)


def cmd_add(hooks_path: str, hooks_dir: str) -> int:
    hooks_dir_p = Path(hooks_dir).expanduser().resolve()
    if not hooks_dir_p.is_dir():
        print(f"⚠ hooks_dir 不存在: {hooks_dir_p}", file=sys.stderr)
        # don't fail — install.sh may run --add before --copy in some flows
    backup = _versioned_backup(hooks_path)
    if backup:
        print(f"  versioned backup: {backup}")

    data = _load_hooks_json(hooks_path)
    data.setdefault("hooks", {})

    added = 0
    skipped = 0
    for event, hook_list in PT_HOOKS.items():
        chain = data["hooks"].setdefault(event, [])
        # Find / create our entry by sentinel marker so we don't merge into the
        # user's own entries (they may have a matcher that scopes their hooks
        # differently; we want PT hooks to fire unconditionally for the event).
        pt_entry = None
        for entry in chain:
            if entry.get("_pt_managed"):
                pt_entry = entry
                break
        if pt_entry is None:
            pt_entry = {"_pt_managed": True, "matcher": "", "hooks": []}
            chain.append(pt_entry)
        existing_cmds = {h.get("command", "") for h in pt_entry.get("hooks", [])}
        for basename, timeout in hook_list:
            cmd = str(hooks_dir_p / basename)
            if cmd in existing_cmds:
                skipped += 1
                continue
            pt_entry.setdefault("hooks", []).append({
                "type": "command",
                "command": cmd,
                "timeout": timeout,
            })
            added += 1
    _save_hooks_json(hooks_path, data)
    print(f"  added {added}, skipped {skipped} (already registered)")
    return 0


def cmd_remove(hooks_path: str, hooks_dir: str | None = None) -> int:
    """Remove all PT-managed entries. hooks_dir is optional — we identify PT
    entries by `_pt_managed: true` sentinel OR by command path containing
    'preference-tracker'."""
    backup = _versioned_backup(hooks_path)
    if backup:
        print(f"  versioned backup: {backup}")
    data = _load_hooks_json(hooks_path)
    hooks_block = data.get("hooks") or {}
    removed = 0
    for event, chain in list(hooks_block.items()):
        new_chain = []
        for entry in chain:
            if entry.get("_pt_managed"):
                removed += len(entry.get("hooks", []))
                continue
            # also strip stale PT commands inside non-PT entries
            sub = []
            for h in entry.get("hooks", []) or []:
                cmd = h.get("command", "")
                if _is_pt_command(cmd):
                    removed += 1
                    continue
                sub.append(h)
            if sub:
                new_entry = dict(entry)
                new_entry["hooks"] = sub
                new_chain.append(new_entry)
        if new_chain:
            hooks_block[event] = new_chain
        else:
            hooks_block.pop(event, None)
    if hooks_block:
        data["hooks"] = hooks_block
    else:
        data.pop("hooks", None)
    _save_hooks_json(hooks_path, data)
    print(f"  removed {removed} hooks")
    return 0


def cmd_verify(hooks_path: str, hooks_dir: str) -> int:
    hooks_dir_p = Path(hooks_dir).expanduser().resolve()
    expected = {
        str(hooks_dir_p / basename): event
        for event, lst in PT_HOOKS.items()
        for basename, _ in lst
    }
    data = _load_hooks_json(hooks_path)
    found_cmds: set[str] = set()
    for event, chain in (data.get("hooks") or {}).items():
        for entry in chain:
            for h in entry.get("hooks", []) or []:
                cmd = h.get("command", "")
                if cmd in expected:
                    found_cmds.add(cmd)
    missing = [c for c in expected if c not in found_cmds]
    print(f"  Codex preference-tracker hooks 注册情况:")
    print(f"    hooks.json: {hooks_path}")
    print(f"    hooks dir:  {hooks_dir_p}")
    for cmd, event in expected.items():
        status = "✓" if cmd in found_cmds else "✗"
        print(f"    {status} {os.path.basename(cmd)} → {event}")
    return 0 if not missing else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Merge preference-tracker hooks into ~/.codex/hooks.json")
    ap.add_argument("--hooks-json", default=str(Path.home() / ".codex" / "hooks.json"))
    ap.add_argument("--hooks-dir", default=str(PT_HOOKS_DEFAULT_DIR))
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--add", action="store_true")
    grp.add_argument("--remove", action="store_true")
    grp.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    if args.add:
        return cmd_add(args.hooks_json, args.hooks_dir)
    if args.remove:
        return cmd_remove(args.hooks_json, args.hooks_dir)
    return cmd_verify(args.hooks_json, args.hooks_dir)


if __name__ == "__main__":
    sys.exit(main())
