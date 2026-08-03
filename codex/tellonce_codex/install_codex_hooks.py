#!/usr/bin/env python3
"""Manage tellonce entries in ~/.codex/hooks.json.

Codex hook event names + JSON in/out protocol mirror Claude Code's: stdin
JSON with prompt / tool_name / tool_input / tool_response; stdout JSON with
hookSpecificOutput.additionalContext etc.; exit 2 = block with reason on stderr.

Usage:
  python3 -m tellonce_codex.install_codex_hooks --add
  python3 -m tellonce_codex.install_codex_hooks --remove
  python3 -m tellonce_codex.install_codex_hooks --verify

Sentinel: every PT-managed entry tagged `_pt_managed: true` so cleanup is exact
and we don't trample user's own hook entries.

Hook layout (mirrors CC's UserPromptSubmit chain + adds PostToolUse to fill the
gap that codex doesn't have a Stop hook):

  UserPromptSubmit:
   - userpromptsubmit-memory-upsert.sh      (fast inbox enqueue; detached worker)
   - userpromptsubmit-retrieve-inject.sh    (retrieve memory rules, inject
      additionalContext)
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

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


PT_HOOKS_DEFAULT_DIR = Path.home() / ".codex" / "skills" / "tellonce" / "hooks"

# event_name -> ordered list of hook script basenames (run in order).
# Outer timeouts = each script's inner _pt_timeout + ~10s headroom for
# bash+python startup — keep in sync with codex/hooks/hooks.json (the plugin
# registration path); outer <= inner lets the platform kill the hook just
# before the inner subprocess finishes, silently dropping its JSON output.
PT_HOOKS = {
    "UserPromptSubmit": [
        ("userpromptsubmit-memory-upsert.sh", 5),
        ("userpromptsubmit-retrieve-inject.sh", 40),
        ("userpromptsubmit-shadow-alert-inject.sh", 25),
    ],
    "PostToolUse": [
        ("posttooluse-deterministic-block.sh", 25),
    ],
    "SessionStart": [
        ("sessionstart-init.sh", 20),
    ],
}
LEGACY_HOOK_BASENAMES = {"userpromptsubmit-pending-inject.sh"}


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
    # Timestamp + pid: install.sh runs --remove then --add back-to-back, and
    # with second-granularity names the --add backup (post-remove, stripped)
    # overwrote the --remove backup — destroying the true pre-install rollback
    # anchor. Matches shared_lib/_install_merge_settings.py's fix.
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.v3_pre_pt_{ts}-{os.getpid()}.json"
    # Same-process remove+add within one second would still collide — bump
    # a suffix rather than overwrite (mirrors _install_merge_settings.py).
    while Path(backup).exists():
        backup = backup[: -len(".json")] + "b.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    Path(backup).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    # GC: every install/uninstall writes one backup; without pruning they
    # accumulate forever. Keep the 5 most recent (matches CC install.sh).
    try:
        siblings = sorted(
            p.parent.glob(p.name + ".v3_pre_pt_*.json"),
            key=lambda b: b.stat().st_mtime,
            reverse=True,
        )
        for old in siblings[5:]:
            old.unlink()
    except Exception:
        pass
    return backup


def _save_hooks_json(path: str, data: dict) -> None:
    import uuid as _uuid
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # pid + uuid8 suffix avoids tmp-file race when concurrent installers run
    # (mirror codex/tellonce_codex/ledger.py).
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}.{_uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(p)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _normalize_command(cmd: str) -> str:
    return str(cmd or "").strip().strip("'\"").replace("\\", "/")


def _command_basename(cmd: str) -> str:
    return _normalize_command(cmd).rsplit("/", 1)[-1]


def _is_pt_command(cmd: str) -> bool:
    """Identify any registration string we previously wrote so cleanup is safe.

    PT hook commands always live under */tellonce/hooks/ AND have one
    of our known basenames. We use path-based identification (rather than a
    sentinel field on the entry) so hooks.json stays strictly schema-compliant.

    CX-B5 fix: normalize separators before matching. Hook commands written on
    Windows use backslashes (`...\\tellonce\\hooks\\foo.sh`), so the
    old forward-slash-only `/hooks/` substring and `rsplit("/")` basename
    extraction never matched and cleanup/verify silently missed PT entries.
    """
    norm = _normalize_command(cmd)
    if "tellonce" not in norm:
        return False
    if "/hooks/" not in norm:
        return False
    basename = _command_basename(norm)
    known = {
        basename
        for lst in PT_HOOKS.values()
        for basename, _ in lst
    } | LEGACY_HOOK_BASENAMES
    return basename in known


def cmd_add(hooks_path: str, hooks_dir: str) -> int:
    hooks_dir_p = Path(hooks_dir).expanduser().resolve()
    if not hooks_dir_p.is_dir():
        print(f"⚠ hooks_dir does not exist: {hooks_dir_p}", file=sys.stderr)
        # don't fail — install.sh may run --add before --copy in some flows
    backup = _versioned_backup(hooks_path)
    if backup:
        print(f"  versioned backup: {backup}")

    data = _load_hooks_json(hooks_path)
    data.setdefault("hooks", {})
    for event, chain in data["hooks"].items():
        cleaned_chain = []
        for entry in chain:
            hooks = []
            for hook in entry.get("hooks", []) or []:
                basename = _command_basename(
                    _normalize_command(hook.get("command", ""))
                )
                if basename in LEGACY_HOOK_BASENAMES:
                    continue
                hooks.append(hook)
            if hooks:
                entry["hooks"] = hooks
                cleaned_chain.append(entry)
            elif not entry.get("hooks"):
                cleaned_chain.append(entry)
        data["hooks"][event] = cleaned_chain

    added = 0
    skipped = 0
    for event, hook_list in PT_HOOKS.items():
        chain = data["hooks"].setdefault(event, [])
        existing_basenames = set()
        cleaned_chain = []
        for entry in chain:
            user_hooks = []
            for hook in entry.get("hooks", []) or []:
                command = hook.get("command", "")
                if _is_pt_command(command):
                    existing_basenames.add(_command_basename(command))
                else:
                    user_hooks.append(hook)
            if user_hooks:
                preserved = dict(entry)
                preserved.pop("_pt_managed", None)
                preserved["hooks"] = user_hooks
                cleaned_chain.append(preserved)
            elif not entry.get("hooks"):
                cleaned_chain.append(entry)

        managed_hooks = []
        for basename, timeout in hook_list:
            # shlex.quote: codex shlex-splits the command string and execs it
            # directly — a HOME containing a space would split a bare path
            # into two tokens and every hook would exit 127 silently. For
            # space-free paths quote() is a no-op, so existing installs see
            # byte-identical commands (no re-trust churn).
            import shlex
            cmd = shlex.quote(str(hooks_dir_p / basename))
            if basename in existing_basenames:
                skipped += 1
            else:
                added += 1
            managed_hooks.append({
                "type": "command",
                "command": cmd,
                "timeout": timeout,
            })
        # Keep all Tellonce hooks in one schema-clean entry and rebuild it in
        # PT_HOOKS order on every upgrade. The memory-upsert hook must run
        # before retrieval rotates the clarification presentation sidecar.
        cleaned_chain.insert(0, {"matcher": "", "hooks": managed_hooks})
        data["hooks"][event] = cleaned_chain
    _save_hooks_json(hooks_path, data)
    print(f"  added {added}, skipped {skipped} (already registered)")
    return 0


def cmd_remove(hooks_path: str, hooks_dir: str | None = None) -> int:
    """Remove all PT hooks. Identifies them by path (any command under
    */tellonce/hooks/<known-basename>). Drops emptied entries
    cleanly."""
    backup = _versioned_backup(hooks_path)
    if backup:
        print(f"  versioned backup: {backup}")
    data = _load_hooks_json(hooks_path)
    hooks_block = data.get("hooks") or {}
    removed = 0
    for event, chain in list(hooks_block.items()):
        new_chain = []
        for entry in chain:
            sub = []
            for h in entry.get("hooks", []) or []:
                cmd = h.get("command", "")
                if _is_pt_command(cmd):
                    removed += 1
                    continue
                sub.append(h)
            # Skip backwards-compat: legacy entries had a `_pt_managed: true`
            # field. Strip them whole if all hooks were PT.
            if entry.get("_pt_managed") and not sub:
                continue
            if sub:
                new_entry = {k: v for k, v in entry.items() if k != "_pt_managed"}
                new_entry["hooks"] = sub
                new_chain.append(new_entry)
            elif not entry.get("hooks"):
                # entry started empty — preserve it (user's empty placeholder)
                new_chain.append(entry)
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
    """Verify (event,
    command) pair, not just command-path presence. Otherwise a hook
    misregistered to the wrong event (e.g. PostToolUse hook under
    UserPromptSubmit) would print a green check and rc=0, masking a
    broken install. Now we distinguish:
      ✓                — registered to the correct event
      ✗ (missing)      — not registered at all
      ⚠ (wrong event)  — registered, but to a different event than expected
    Returns 1 if any hook is missing OR registered to the wrong event.
    """
    hooks_dir_p = Path(hooks_dir).expanduser().resolve()
    expected_pairs = {
        (event, str(hooks_dir_p / basename)): basename
        for event, lst in PT_HOOKS.items()
        for basename, _ in lst
    }
    # Map command -> set of events it's registered under.
    cmd_to_events: dict[str, set[str]] = {}
    for event, chain in (data := _load_hooks_json(hooks_path)).get("hooks", {}).items():
        for entry in chain:
            for h in entry.get("hooks", []) or []:
                cmd = h.get("command", "")
                if not cmd:
                    continue
                cmd_to_events.setdefault(_normalize_command(cmd), set()).add(event)
    print(f"  Codex tellonce hook registration status:")
    print(f"    hooks.json: {hooks_path}")
    print(f"    hooks dir:  {hooks_dir_p}")
    bad = 0
    for (expected_event, cmd), basename in expected_pairs.items():
        events_seen = cmd_to_events.get(_normalize_command(cmd), set())
        if expected_event in events_seen:
            print(f"    ✓ {basename} → {expected_event}")
        elif events_seen:
            wrong = ", ".join(sorted(events_seen))
            print(f"    ⚠ {basename} registered to {wrong}, expected {expected_event}")
            bad += 1
        else:
            print(f"    ✗ {basename} → {expected_event} (missing)")
            bad += 1
    return 0 if bad == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Merge tellonce hooks into ~/.codex/hooks.json")
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
