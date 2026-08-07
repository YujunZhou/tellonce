from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import memory_judge


FIXTURE_PATH = Path(__file__).with_name("memory_judge_semantic_fixtures.json")
PROMPT_HASH_SOURCE = "__TELLONCE_SEMANTIC_FIXTURE_SOURCE__"
PROMPT_HASH_CONTEXT = "__TELLONCE_SEMANTIC_FIXTURE_CONTEXT__"


def prompt_sha256() -> str:
    prompt = memory_judge.build_prompt(
        PROMPT_HASH_SOURCE,
        [],
        PROMPT_HASH_CONTEXT,
    )
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class MemoryJudgeSemanticFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_prompt_hash_is_frozen(self):
        self.assertEqual(self.manifest["prompt_sha256"], prompt_sha256())

    @unittest.skipUnless(
        os.environ.get("PT_RUN_SEMANTIC_FIXTURES") == "1",
        "set PT_RUN_SEMANTIC_FIXTURES=1 for real CLI judge fixtures",
    )
    def test_real_cli_judge_semantics(self):
        old_cli = os.environ.get("PT_MEMORY_UPSERT_CLI")
        old_model = os.environ.get("PT_MEMORY_UPSERT_MODEL")
        old_timeout = os.environ.get("PT_MEMORY_UPSERT_TIMEOUT")
        os.environ["PT_MEMORY_UPSERT_CLI"] = self.manifest["cli"]
        os.environ["PT_MEMORY_UPSERT_MODEL"] = self.manifest["model"]
        os.environ["PT_MEMORY_UPSERT_TIMEOUT"] = str(
            self.manifest["timeout_seconds"]
        )
        failures = []
        try:
            for fixture in self.manifest["fixtures"]:
                with self.subTest(fixture=fixture["id"]):
                    try:
                        plan = memory_judge.judge_plan(
                            fixture["source"],
                            fixture.get("active_rules", []),
                            fixture.get("context", ""),
                        )
                        self._assert_fixture(fixture, plan)
                    except Exception as exc:
                        failures.append(
                            f"{fixture['id']}: {type(exc).__name__}: {exc}"
                        )
        finally:
            if old_cli is None:
                os.environ.pop("PT_MEMORY_UPSERT_CLI", None)
            else:
                os.environ["PT_MEMORY_UPSERT_CLI"] = old_cli
            if old_model is None:
                os.environ.pop("PT_MEMORY_UPSERT_MODEL", None)
            else:
                os.environ["PT_MEMORY_UPSERT_MODEL"] = old_model
            if old_timeout is None:
                os.environ.pop("PT_MEMORY_UPSERT_TIMEOUT", None)
            else:
                os.environ["PT_MEMORY_UPSERT_TIMEOUT"] = old_timeout
        if failures:
            self.fail("\n".join(failures))

    def _assert_fixture(self, fixture: dict, plan: dict) -> None:
        mutations = plan["mutations"]
        operations = [item["operation"] for item in mutations]
        self.assertEqual(operations, fixture["expected_operations"])
        if "expected_mutation_count" in fixture:
            self.assertEqual(len(mutations), fixture["expected_mutation_count"])
        if "expected_resolved_turn_keys" in fixture:
            self.assertEqual(
                plan["resolved_turn_keys"],
                fixture["expected_resolved_turn_keys"],
            )
        if not mutations:
            return
        mutation = mutations[0]
        if "expected_child_count" in fixture:
            self.assertEqual(
                len(mutation["children"]),
                fixture["expected_child_count"],
            )
        record = mutation.get("record") or {}
        if "expected_scope" in fixture:
            self.assertEqual(record.get("scope"), fixture["expected_scope"])
        if "expected_scope_anchor" in fixture:
            self.assertEqual(
                record.get("scope_anchor", ""),
                fixture["expected_scope_anchor"],
            )
        applicability = (
            str(record.get("applies_when", ""))
            or str(record.get("condition", ""))
        )
        if "expected_applicability" in fixture:
            self.assertEqual(
                applicability,
                fixture["expected_applicability"],
            )
        if "expected_applicability_contains" in fixture:
            self.assertIn(
                fixture["expected_applicability_contains"],
                applicability,
            )


if __name__ == "__main__":
    unittest.main()
