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


if __name__ == "__main__":
    unittest.main()
