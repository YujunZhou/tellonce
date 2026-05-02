import contextlib
import io
import json
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_preftrack.cli import main
from codex_preftrack.dashboard import build_dashboard
from codex_preftrack.doctor import run_doctor
from codex_preftrack.index import build_active_index
from codex_preftrack.install import install
from codex_preftrack.ledger import DuplicateEventError, append_event, read_events, repair_tail
from codex_preftrack.memory import canonical_key, parse_memory
from codex_preftrack.migrate import preview_migration
from codex_preftrack.mode import load_mode, write_mode
from codex_preftrack.paths import ProjectRootError, register_project, resolve_project_root
from codex_preftrack.promote import promote_candidate
from codex_preftrack.scan import scan_message
from codex_preftrack.uninstall import uninstall
from codex_preftrack.verify import verify_output
from codex_preftrack.wrapper import run_wrapped


class CodexPreftrackCoreTests(unittest.TestCase):
    def test_cli_requires_command(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rc = main([])
        self.assertEqual(rc, 2)
        self.assertIn("usage:", stderr.getvalue())

    def test_cli_accepts_known_commands(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = main(["--help"])
        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        for command in ["install", "doctor", "uninstall", "scan", "promote", "dashboard", "exec", "migrate"]:
            self.assertIn(command, out)

    def test_resolve_project_root_rejects_home(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"HOME": td}):
                with self.assertRaises(ProjectRootError):
                    resolve_project_root(Path(td))

    def test_register_project_writes_registration_and_mode(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(os.environ, {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"}):
                registration = register_project(project)
            self.assertEqual(registration.project_root, project.resolve())
            self.assertEqual(registration.state_root, project / ".codex" / "preference-tracker")
            self.assertTrue((registration.state_root / "registration.json").is_file())
            self.assertTrue((registration.state_root / "mode.json").is_file())
            self.assertEqual(load_mode(registration.state_root).mode, "audit_only")

    def test_mode_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td) / "state"
            write_mode(state_root, mode="wrapper", wrapper_seen=True)
            mode = load_mode(state_root)
            self.assertEqual(mode.mode, "wrapper")
            self.assertTrue(mode.wrapper_seen)
            self.assertFalse(mode.blocking)

    def test_append_event_redacts_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td) / "state"
            event = {
                "event_id": "20260429T000000Z-abcd1234-deadbeef",
                "event_type": "scan_recorded",
                "session_id": "s1",
                "payload": {"message": "token sk-abcdefghijklmnopqrstuvwxyz0123456789"},
            }
            append_event(state_root, event)
            with self.assertRaises(DuplicateEventError):
                append_event(state_root, event)
            stored = list(read_events(state_root))[0]
            self.assertNotIn("sk-", repr(stored))
            self.assertIn("[REDACTED_API_KEY]", repr(stored))

    def test_repair_tail_quarantines_partial_line(self):
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td) / "state"
            state_root.mkdir()
            (state_root / "events.jsonl").write_text(
                '{"event_id":"ok","event_type":"x","payload":{}}\n{"broken"', encoding="utf-8"
            )
            result = repair_tail(state_root)
            self.assertTrue(result.repaired)
            self.assertEqual(len(list(read_events(state_root))), 1)
            self.assertTrue((state_root / "evidence" / "events_tail_quarantine.txt").is_file())

    def test_scan_records_preference_event_and_dashboard_counts(self):
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td) / "state"
            result = scan_message(state_root, "以后都用中文回答我")
            self.assertEqual(result.signal_type, "preference")
            event = list(read_events(state_root))[0]
            self.assertEqual(event["event_type"], "scan_recorded")
            self.assertEqual(event["payload"]["detection"]["signal_type"], "preference")
            text = build_dashboard(state_root)
            self.assertIn("mode:", text)
            self.assertIn("scan_count: 1", text)

    def test_promote_writes_committed_memory_and_index(self):
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td) / "state"
            candidate = {
                "atomic_id": "wf-pref-001",
                "type": "preference",
                "domain": "workflow",
                "scope": "global",
                "condition": "when user asks for status",
                "rule_text": "MUST be concise",
                "applies_when": "status update",
                "does_not_apply_when": "(none)",
                "confidence": "high",
                "body": "Keep status updates concise.",
            }
            result = promote_candidate(state_root, candidate)
            self.assertTrue(result.created)
            parsed = parse_memory(result.path)
            self.assertEqual(parsed.data["status"], "active")
            self.assertEqual(len(parsed.data["source_event_ids"]), 2)
            self.assertEqual(parsed.data["canonical_key"], canonical_key(parsed.data))
            index = build_active_index(state_root)
            self.assertEqual(index["active"][0]["atomic_id"], "wf-pref-001")

    def test_promote_dry_run_does_not_write_memory(self):
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td) / "state"
            candidate = {
                "atomic_id": "wf-pref-002",
                "type": "preference",
                "domain": "workflow",
                "scope": "global",
                "condition": "always",
                "rule_text": "MUST test first",
                "applies_when": "coding",
                "does_not_apply_when": "(none)",
                "confidence": "high",
            }
            result = promote_candidate(state_root, candidate, dry_run=True)
            self.assertFalse(result.created)
            self.assertFalse((state_root / "memories" / "active").exists())

    def test_promote_commit_failure_active_exists_but_index_skips(self):
        # CX-7 design (publish review 2026-05-01): rename comes BEFORE the
        # commit append, so the ledger only records what's already on disk.
        # If the commit-append fails, the active file IS present on disk
        # (the rename succeeded), but build_active_index will filter it
        # out because there's no matching `promotion_committed` event.
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td) / "state"
            candidate = {
                "atomic_id": "wf-pref-003",
                "type": "preference",
                "domain": "workflow",
                "scope": "global",
                "condition": "always",
                "rule_text": "MUST not leave active before commit",
                "applies_when": "promotion",
                "does_not_apply_when": "(none)",
                "confidence": "high",
            }
            import codex_preftrack.promote as promote_mod
            from codex_preftrack.index import build_active_index

            calls = {"n": 0}
            real_append = promote_mod.append_event

            def flaky_append(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("commit append failed")
                return real_append(*args, **kwargs)

            with patch.object(promote_mod, "append_event", side_effect=flaky_append):
                with self.assertRaises(RuntimeError):
                    promote_candidate(state_root, candidate)
            # Active file is present on disk (rename succeeded before the
            # failed commit append) — this is the CX-7 invariant: ledger
            # entries only reflect what's already durable.
            self.assertTrue((state_root / "memories" / "active" / "wf-pref-003.md").exists())
            # But build_active_index will not surface it because no
            # promotion_committed event exists for this atomic_id.
            index = build_active_index(state_root)
            atomic_ids = {entry["atomic_id"] for entry in index["active"]}
            self.assertNotIn("wf-pref-003", atomic_ids)

    def test_install_doctor_uninstall_keep_data(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(os.environ, {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"}):
                record = install(project)
                report = run_doctor(project)
                result = uninstall(project, keep_data=True)
            self.assertTrue(record.state_root.exists())
            self.assertTrue(report.status_line.startswith("Preference Tracker status:"))
            self.assertEqual(report.sections["state"], "PASS")
            self.assertTrue(result.removed_integration)
            self.assertTrue(record.state_root.exists())

    def test_doctor_private_path_audit_fails(self):
        # Public-release default has no built-in PRIVATE_PATTERNS, so we
        # provide one via the documented env-extension contract and verify
        # the detector fires.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "CODEX_PREFTRACK_ALLOW_TEMP": "1",
                    "CODEX_PT_PRIVATE_PATTERNS": "__test_leaked_token__",
                },
            ):
                # Reload doctor to pick up env-extended patterns.
                import importlib

                from codex_preftrack import doctor as _doctor

                importlib.reload(_doctor)
                install(project)
                bad = project / ".codex" / "preference-tracker" / "managed_runtime.txt"
                bad.write_text(
                    "this file accidentally contains __test_leaked_token__ from a fork",
                    encoding="utf-8",
                )
                report = _doctor.run_doctor(project)
            self.assertEqual(report.sections["private_paths"], "FAIL")

    def test_verify_warns_on_tmp_artifact(self):
        verdict = verify_output("write important output to /tmp/result.json")
        self.assertEqual(verdict.verdict, "warn_log")
        self.assertEqual(verdict.violations[0]["rule_id"], "tool-pit-130")

    def test_verify_detects_chinese_inline_english_with_whitelist(self):
        text = "好的我来修复这个 stub 的问题，然后 merge 进主分支。"
        verdict = verify_output(text, whitelist={"merge"})
        rule_ids = {v["rule_id"] for v in verdict.violations}
        self.assertIn("lang-pit-130", rule_ids)
        self.assertNotIn("merge", repr(verdict.violations))

    def test_wrapper_records_missing_codex_as_degraded(self):
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td) / "state"
            result = run_wrapped(state_root, ["definitely-missing-codex-binary"], timeout_s=1)
            self.assertEqual(result.exit_code, 3)
            self.assertTrue((state_root / "runs" / result.run_id / "run_meta.json").is_file())

    def test_migration_preview_does_not_write_active_memory(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            legacy = base / "legacy.md"
            legacy.write_text("---\natomic_id: wf-pref-001\nrule_text: old\n---\nbody\n", encoding="utf-8")
            report = preview_migration(base / "state", [legacy])
            self.assertIn(report["items"][0]["decision"], {"archive_legacy", "pending_review", "quarantine"})
            self.assertFalse((base / "state" / "memories" / "active").exists())

    def test_cli_migrate_preview_is_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            project = base / "project"
            legacy = base / "legacy.md"
            project.mkdir()
            legacy.write_text("---\natomic_id: wf-pref-001\nrule_text: old\n---\nbody\n", encoding="utf-8")
            rc = main(["migrate", "--project-root", str(project), "--preview", "--source", str(legacy)])
            self.assertEqual(rc, 0)
            self.assertFalse((project / ".codex" / "preference-tracker").exists())

    def test_doctor_ignores_registration_private_paths(self):
        # registration.json is special-cased: even if it contains the
        # configured leak pattern, it's never flagged (it's expected to
        # contain absolute paths by design).
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "CODEX_PREFTRACK_ALLOW_TEMP": "1",
                    "CODEX_PT_PRIVATE_PATTERNS": "__test_token_for_registration_test__",
                },
            ):
                import importlib

                from codex_preftrack import doctor as _doctor

                importlib.reload(_doctor)
                install(project)
                registration = project / ".codex" / "preference-tracker" / "registration.json"
                # Inject the leak token inside a real JSON field so the file
                # stays valid JSON; the audit must still skip registration.json
                # by special-case (its filename, not its content).
                data_obj = json.loads(registration.read_text(encoding="utf-8"))
                data_obj["__decoy_for_test__"] = "__test_token_for_registration_test__"
                registration.write_text(json.dumps(data_obj, indent=2), encoding="utf-8")
                report = _doctor.run_doctor(project)
            self.assertNotEqual(report.sections["private_paths"], "FAIL")

    def test_skill_package_wrappers_exist(self):
        root = Path(__file__).resolve().parents[2]
        package = root / "codex_preftrack_skill" / "preference-tracker"
        for name in ["SKILL.md", "install.sh", "doctor.sh", "uninstall.sh", "dashboard.sh"]:
            self.assertTrue((package / name).is_file(), name)

    def test_skill_install_and_doctor_wrappers_smoke(self):
        root = Path(__file__).resolve().parents[2]
        package = root / "codex_preftrack_skill" / "preference-tracker"
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["CODEX_PREFTRACK_ALLOW_TEMP"] = "1"
            env["PYTHON"] = os.sys.executable
            install_proc = subprocess.run(
                ["bash", str(package / "install.sh"), "--project-root", str(project)],
                cwd=project,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(install_proc.returncode, 0, install_proc.stderr)
            doctor_proc = subprocess.run(
                ["bash", str(package / "doctor.sh"), "--project-root", str(project)],
                cwd=project,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(doctor_proc.returncode, 0, doctor_proc.stderr)
            self.assertIn("Preference Tracker status:", doctor_proc.stdout)

    def test_standalone_skill_folder_install_smoke(self):
        root = Path(__file__).resolve().parents[2]
        source = root / "codex_preftrack_skill" / "preference-tracker"
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            skill = home / ".codex" / "skills" / "preference-tracker"
            home.mkdir()
            project.mkdir()
            skill.parent.mkdir(parents=True)
            shutil.copytree(source, skill)
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["CODEX_PREFTRACK_ALLOW_TEMP"] = "1"
            env["PYTHON"] = os.sys.executable
            proc = subprocess.run(
                ["bash", str(skill / "install.sh"), "--project-root", str(project)],
                cwd=project,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_wrapper_seen_is_reported_by_doctor(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(os.environ, {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"}):
                install(project)
                state = project / ".codex" / "preference-tracker"
                run_wrapped(state, ["definitely-missing-codex-binary"], timeout_s=1)
                report = run_doctor(project)
            self.assertIn(report.sections["wrapper"], {"PASS", "DEGRADED"})


class CodexHookIntegrationTests(unittest.TestCase):
    """Round-7: codex hook integration (install_codex_hooks + PostToolUse adapter)."""

    def test_install_codex_hooks_add_remove_idempotent(self):
        from codex_preftrack import install_codex_hooks as ich
        with tempfile.TemporaryDirectory() as td:
            hooks_json = Path(td) / "hooks.json"
            # _is_pt_command identifies PT hooks by path: must contain
            # "preference-tracker" + "/hooks/" + a known basename. Mirror the
            # real layout (~/.codex/skills/preference-tracker/hooks/) for tests.
            hooks_dir = Path(td) / "preference-tracker" / "hooks"
            hooks_dir.mkdir(parents=True)
            # First add
            ich.cmd_add(str(hooks_json), str(hooks_dir))
            data = json.loads(hooks_json.read_text())
            # 5 PT hooks: 3 UserPromptSubmit + 1 PostToolUse + 1 SessionStart
            def _count_pt(d):
                n = 0
                for entries in d.get("hooks", {}).values():
                    for entry in entries:
                        for h in entry.get("hooks", []) or []:
                            if ich._is_pt_command(h.get("command", "")):
                                n += 1
                return n
            self.assertEqual(_count_pt(data), 5)
            self.assertIn("UserPromptSubmit", data["hooks"])
            self.assertIn("PostToolUse", data["hooks"])
            self.assertIn("SessionStart", data["hooks"])
            # Schema cleanliness: PT entries should NOT contain a sentinel
            # field codex doesn't know about.
            for entries in data["hooks"].values():
                for entry in entries:
                    self.assertNotIn("_pt_managed", entry,
                        "PT entries must be schema-clean (no sentinel field)")
            # Re-add: idempotent (no duplicates)
            ich.cmd_add(str(hooks_json), str(hooks_dir))
            data2 = json.loads(hooks_json.read_text())
            self.assertEqual(_count_pt(data2), 5, "re-add must be idempotent")
            # Remove cleans them all
            ich.cmd_remove(str(hooks_json))
            data3 = json.loads(hooks_json.read_text())
            self.assertNotIn("hooks", data3)

    def test_install_codex_hooks_preserves_user_entries(self):
        """User's own non-PT hook entries must survive --add and --remove."""
        from codex_preftrack import install_codex_hooks as ich
        with tempfile.TemporaryDirectory() as td:
            hooks_json = Path(td) / "hooks.json"
            hooks_dir = Path(td) / "preference-tracker" / "hooks"
            hooks_dir.mkdir(parents=True)
            seed = {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "gws-axi", "timeout": 10}
                            ],
                        }
                    ]
                }
            }
            hooks_json.write_text(json.dumps(seed, indent=2))
            ich.cmd_add(str(hooks_json), str(hooks_dir))
            data = json.loads(hooks_json.read_text())
            ss = data["hooks"]["SessionStart"]
            # Should have 2 entries: user's gws-axi (pure non-PT) + PT-only
            self.assertEqual(len(ss), 2)
            user_entry = next(
                e for e in ss
                if any(not ich._is_pt_command(h.get("command", ""))
                       for h in e.get("hooks", []))
            )
            pt_entry = next(
                e for e in ss
                if e is not user_entry
            )
            self.assertEqual(user_entry["hooks"][0]["command"], "gws-axi")
            self.assertEqual(len(pt_entry["hooks"]), 1)
            self.assertTrue(ich._is_pt_command(pt_entry["hooks"][0]["command"]))
            ich.cmd_remove(str(hooks_json))
            data2 = json.loads(hooks_json.read_text())
            # User's gws-axi should still be there
            self.assertEqual(
                data2["hooks"]["SessionStart"][0]["hooks"][0]["command"], "gws-axi"
            )

    def test_posttooluse_adapter_extract_agent_text(self):
        from codex_preftrack import codex_posttooluse_block as cpb
        # Write tool style
        self.assertEqual(
            cpb._extract_agent_text({"tool_input": {"content": "hello"}}),
            "hello",
        )
        # Edit tool style
        self.assertEqual(
            cpb._extract_agent_text(
                {"tool_input": {"old_string": "a", "new_string": "b"}}
            ),
            "b",
        )
        # Bash command style
        self.assertEqual(
            cpb._extract_agent_text({"tool_input": {"command": "ls -la"}}),
            "ls -la",
        )
        # Empty / missing
        self.assertEqual(cpb._extract_agent_text({}), "")
        self.assertEqual(cpb._extract_agent_text({"tool_input": {}}), "")

    def test_posttooluse_adapter_audit_only_logs_but_doesnt_block(self):
        from codex_preftrack import codex_posttooluse_block as cpb
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(
                os.environ,
                {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"},
            ):
                install(project)
                # default mode is audit_only
                payload = {
                    "tool_name": "Write",
                    "tool_input": {"content": "我们今天要把 stub 占位代码处理完, 然后 merge 进主分支, 整个流程跑一遍, 这一段必须够长."},
                    "cwd": str(project),
                    "session_id": "audit-test",
                }
                with patch("sys.stdin", io.StringIO(json.dumps(payload))):
                    rc = cpb.main()
                self.assertEqual(rc, 0, "audit_only must never block (rc=0)")
                log = (
                    project / ".codex" / "preference-tracker" / "runtime"
                    / "posttooluse_log.jsonl"
                )
                self.assertTrue(log.is_file(), "must write log entry")
                lines = log.read_text(encoding="utf-8").strip().split("\n")
                last = json.loads(lines[-1])
                self.assertEqual(last["mode"], "audit_only")
                self.assertIn("lang-pit-130", last["violations"])

    def test_posttooluse_adapter_blocking_mode_returns_2_on_violation(self):
        from codex_preftrack import codex_posttooluse_block as cpb
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(
                os.environ,
                {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"},
            ):
                install(project)
                # Force blocking mode for the test
                mode_path = project / ".codex" / "preference-tracker" / "mode.json"
                m = json.loads(mode_path.read_text())
                m["mode"] = "blocking"
                mode_path.write_text(json.dumps(m, indent=2))
                # 高中文比例 + 几个英文借词以触发 lang-pit-130
                payload = {
                    "tool_name": "Write",
                    "tool_input": {"content": "我们今天要把这个 stub 占位代码全部处理干净, 然后 merge 进入主分支, 整个测试流程跑过一遍特别重要, 我们要保证中文比例足够高才能触发偏好规则的检查机制."},
                    "cwd": str(project),
                    "session_id": "block-test",
                }
                with patch("sys.stdin", io.StringIO(json.dumps(payload))):
                    rc = cpb.main()
                self.assertEqual(
                    rc, 2,
                    "blocking + violation must return rc=2 to signal block",
                )

    def test_posttooluse_adapter_clean_input_returns_0(self):
        from codex_preftrack import codex_posttooluse_block as cpb
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(
                os.environ,
                {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"},
            ):
                install(project)
                mode_path = project / ".codex" / "preference-tracker" / "mode.json"
                m = json.loads(mode_path.read_text())
                m["mode"] = "blocking"
                mode_path.write_text(json.dumps(m, indent=2))
                # All-English clean input — no rule fires (no chinese-mixed-english)
                payload = {
                    "tool_name": "Edit",
                    "tool_input": {"old_string": "foo", "new_string": "this is a perfectly clean edit with no rule violations whatsoever in english only"},
                    "cwd": str(project),
                    "session_id": "clean-test",
                }
                with patch("sys.stdin", io.StringIO(json.dumps(payload))):
                    rc = cpb.main()
                self.assertEqual(rc, 0)

    def test_secure_mkdir_rejects_non_directory_ancestor(self):
        """Round-7 robustness: when ~/.codex is a 0-byte regular file (a real
        case observed in production), secure_mkdir must give an actionable
        error before mkdir bombs with a generic FileExistsError."""
        from codex_preftrack.ledger import secure_mkdir, NonDirectoryPathError
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "ima_file"
            blocker.write_text("not-a-dir")
            with self.assertRaises(NonDirectoryPathError) as cm:
                secure_mkdir(blocker / "sub" / "dir")
            msg = str(cm.exception)
            self.assertIn("regular file", msg)
            self.assertIn("mv", msg, "error must suggest the fix")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "ima_file"
            target.write_text("blocker")
            with self.assertRaises(NonDirectoryPathError):
                secure_mkdir(target)

    def test_doctor_reports_hooks_status(self):
        """doctor must report hooks=PASS / NOT_INSTALLED / PARTIAL / FAIL."""
        from codex_preftrack import install_codex_hooks as ich
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(
                os.environ,
                {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"},
            ):
                install(project)
                # No hooks registered yet
                report = run_doctor(project)
                self.assertEqual(report.sections["hooks"], "NOT_INSTALLED")
                # Register all 5 hooks
                hooks_json = home / ".codex" / "hooks.json"
                (home / ".codex").mkdir(exist_ok=True)
                hooks_dir = home / ".codex" / "skills" / "preference-tracker" / "hooks"
                hooks_dir.mkdir(parents=True)
                ich.cmd_add(str(hooks_json), str(hooks_dir))
                report = run_doctor(project)
                self.assertEqual(report.sections["hooks"], "PASS")
                # Drop one hook entry to simulate partial state
                data = json.loads(hooks_json.read_text())
                pt_chain = data["hooks"]["UserPromptSubmit"]
                pt_chain[0]["hooks"] = pt_chain[0]["hooks"][:1]  # keep only 1 of 3
                hooks_json.write_text(json.dumps(data, indent=2))
                report = run_doctor(project)
                self.assertEqual(report.sections["hooks"], "PARTIAL")

    def test_uninstall_sh_smoke_purge_hooks_and_skill(self):
        """End-to-end: install then uninstall --purge-hooks --purge-skill cleans."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            repo_root = Path(__file__).resolve().parents[2]
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "CODEX_PREFTRACK_ALLOW_TEMP": "1",
                "PYTHON": os.sys.executable,
            })
            # Install
            ip = subprocess.run(
                ["bash", str(repo_root / "codex" / "install.sh")],
                cwd=project, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(ip.returncode, 0, ip.stderr)
            global_dir = home / ".codex" / "skills" / "preference-tracker"
            hooks_json = home / ".codex" / "hooks.json"
            self.assertTrue(global_dir.is_dir())
            self.assertTrue(hooks_json.is_file())
            # Uninstall --purge-hooks --purge-skill
            up = subprocess.run(
                ["bash", str(repo_root / "codex" / "uninstall.sh"),
                 "--purge-hooks", "--purge-skill"],
                cwd=project, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(up.returncode, 0, up.stderr)
            self.assertFalse(global_dir.is_dir(), "skill dir should be removed")
            data = json.loads(hooks_json.read_text())
            self.assertEqual(data.get("hooks", {}), {},
                "hooks.json should have no PT entries left")

    def test_dashboard_hooks_status_reflects_hooks_json(self):
        """Round-7 fix: dashboard reads ground truth (~/.codex/hooks.json)
        not the stale `mode.hooks` field."""
        from codex_preftrack import install_codex_hooks as ich
        from codex_preftrack.dashboard import build_dashboard
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(
                os.environ,
                {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"},
            ):
                install(project, register_hooks=False)
                state = project / ".codex" / "preference-tracker"
                # No hooks registered yet
                out = build_dashboard(state)
                self.assertIn("hooks: NOT_INSTALLED", out)
                # Now register hooks (simulate global runtime layout)
                hooks_dir = home / ".codex" / "skills" / "preference-tracker" / "hooks"
                hooks_dir.mkdir(parents=True)
                ich.cmd_add(
                    str(home / ".codex" / "hooks.json"),
                    str(hooks_dir),
                )
                out2 = build_dashboard(state)
                self.assertIn("hooks: PASS", out2,
                    "dashboard must reflect hooks.json reality, not stale mode field")

    def test_install_auto_registers_global_hooks_when_layout_present(self):
        """Round-7: codex_preftrack install (programmatic) auto-registers hooks
        if global runtime is present."""
        from codex_preftrack import install_codex_hooks as ich
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            # Pre-create the global runtime layout (skill dir with hooks/)
            hooks_dir = home / ".codex" / "skills" / "preference-tracker" / "hooks"
            hooks_dir.mkdir(parents=True)
            with patch.dict(
                os.environ,
                {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"},
            ):
                rec = install(project)
                self.assertTrue(rec.hooks_registered,
                    "install must auto-register hooks when global runtime is in place")
                hooks_json = home / ".codex" / "hooks.json"
                self.assertTrue(hooks_json.is_file())
                data = json.loads(hooks_json.read_text())
                # 5 PT hooks should be registered
                pt_count = sum(
                    1
                    for entries in data["hooks"].values()
                    for entry in entries
                    for h in entry.get("hooks", [])
                    if ich._is_pt_command(h.get("command", ""))
                )
                self.assertEqual(pt_count, 5)

    def test_install_no_register_hooks_flag(self):
        """register_hooks=False disables the auto-registration path."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            hooks_dir = home / ".codex" / "skills" / "preference-tracker" / "hooks"
            hooks_dir.mkdir(parents=True)
            with patch.dict(
                os.environ,
                {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"},
            ):
                rec = install(project, register_hooks=False)
                self.assertFalse(rec.hooks_registered)
                self.assertFalse((home / ".codex" / "hooks.json").is_file())

    def test_posttooluse_bib_drift_detection(self):
        """Round-7: PostToolUse adapter runs verify_bib_ledger.py when the
        agent writes a .bib file, returns bib-pref-001 violation on drift."""
        from codex_preftrack import codex_posttooluse_block as cpb
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict(
                os.environ,
                {"HOME": str(home), "CODEX_PREFTRACK_ALLOW_TEMP": "1"},
            ):
                install(project, register_hooks=False)
                # Set up a .bib + ledger pair where ledger entry is drifted
                bib = project / "references.bib"
                bib.write_text(
                    "@article{Smith2024_KEY_RENAMED,\n"
                    "  title = {Test},\n"
                    "  author = {Smith, J.},\n"
                    "  doi = {10.1234/x}\n}\n",
                    encoding="utf-8",
                )
                # ledger says original key was Smith2024_test (now renamed -> drift_modified)
                ledger = project / "bib_sources.jsonl"
                ledger.write_text(json.dumps({
                    "ts": "2026-05-01T00:00:00Z",
                    "title_query": "Test",
                    "matched_title": "Test",
                    "match_score": 0.99,
                    "doi": "10.1234/x",
                    "appended_to": str(bib),
                    "bibtex_raw": "@article{Smith2024_test,\n  title = {Test},\n  author = {Smith, J.},\n  doi = {10.1234/x}\n}",
                }) + "\n", encoding="utf-8")
                # Drop a verifier script in scripts/
                scripts_dir = project / "scripts"
                scripts_dir.mkdir()
                (scripts_dir / "verify_bib_ledger.py").write_text(
                    Path(__file__).resolve().parents[2].joinpath(
                        "..", "example-research-project-paper", "scripts", "verify_bib_ledger.py"
                    ).resolve().read_text() if Path("/home/user/zyj/example-research-project-paper/scripts/verify_bib_ledger.py").is_file()
                    else "#!/usr/bin/env python3\nimport sys; sys.exit(1)\n",
                    encoding="utf-8",
                )
                payload = {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(bib), "old_string": "Smith2024_test", "new_string": "Smith2024_KEY_RENAMED"},
                    "cwd": str(project),
                    "session_id": "bib-drift-test",
                }
                vios = cpb._check_bib_drift(payload)
                # If verifier exists in user's paper repo, drift should be caught.
                if (scripts_dir / "verify_bib_ledger.py").read_text().startswith("#!/usr/bin/env python3\nimport sys; sys.exit(1)"):
                    # Stub script always fails — counts as drift signal
                    self.assertEqual(len(vios), 1)
                else:
                    # Real verifier from paper repo: should detect drift_modified
                    self.assertGreaterEqual(len(vios), 1)
                    self.assertEqual(vios[0]["rule_id"], "bib-pref-001")

    def test_posttooluse_bib_drift_fatal_rc2_does_not_synthesize_violation(self):
        """If verify_bib_ledger.py fatally errors (rc=2), don't fake a
        bib-pref-001 violation — log to stderr and skip."""
        from codex_preftrack import codex_posttooluse_block as cpb
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            project.mkdir()
            bib = project / "refs.bib"
            bib.write_text("@article{x}\n", encoding="utf-8")
            ledger = project / "bib_sources.jsonl"
            ledger.write_text(json.dumps({
                "bibtex_raw": "@article{x}",
                "appended_to": str(bib),
                "doi": "1",
                "matched_title": "x",
            }) + "\n", encoding="utf-8")
            scripts = project / "scripts"
            scripts.mkdir()
            # Stub verifier returns rc=2 (fatal verifier error)
            (scripts / "verify_bib_ledger.py").write_text(
                "#!/usr/bin/env python3\nimport sys\nsys.exit(2)\n",
                encoding="utf-8",
            )
            payload = {
                "tool_input": {"file_path": str(bib)},
                "cwd": str(project),
                "session_id": "rc2-test",
            }
            vios = cpb._check_bib_drift(payload)
            self.assertEqual(vios, [],
                "rc=2 verifier error must not synthesize a drift violation")

    def test_posttooluse_bib_no_verifier_no_violation(self):
        """If no verify_bib_ledger.py is in the project, .bib writes don't
        synthesize a violation (we fail open — drift detection is opt-in)."""
        from codex_preftrack import codex_posttooluse_block as cpb
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            project = base / "project"
            project.mkdir()
            bib = project / "references.bib"
            bib.write_text("@article{x, title={x}}\n")
            payload = {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(bib), "old_string": "x", "new_string": "y"},
                "cwd": str(project),
                "session_id": "no-verifier",
            }
            vios = cpb._check_bib_drift(payload)
            self.assertEqual(vios, [])

    def test_install_sh_smoke_global_layout(self):
        """End-to-end: bash install.sh creates global runtime + hooks.json + state."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            project = base / "project"
            home.mkdir()
            project.mkdir()
            repo_root = Path(__file__).resolve().parents[2]
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "CODEX_PREFTRACK_ALLOW_TEMP": "1",
                "PYTHON": os.sys.executable,
            })
            proc = subprocess.run(
                ["bash", str(repo_root / "codex" / "install.sh")],
                cwd=project, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            global_dir = home / ".codex" / "skills" / "preference-tracker"
            self.assertTrue((global_dir / "codex_preftrack").is_dir())
            self.assertTrue((global_dir / "shared_lib").is_dir())
            self.assertTrue((global_dir / "hooks").is_dir())
            self.assertTrue((global_dir / "shared_lib" / "retrieve_inject.py").is_file())
            self.assertTrue((global_dir / "shared_lib" / "deterministic_block.py").is_file())
            self.assertTrue(
                (global_dir / "hooks" / "posttooluse-deterministic-block.sh").is_file()
            )
            hooks_json = home / ".codex" / "hooks.json"
            self.assertTrue(hooks_json.is_file())
            data = json.loads(hooks_json.read_text())
            self.assertIn("UserPromptSubmit", data["hooks"])
            self.assertIn("PostToolUse", data["hooks"])
            self.assertIn("SessionStart", data["hooks"])
            # Per-project state init also ran
            self.assertTrue(
                (project / ".codex" / "preference-tracker" / "registration.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
