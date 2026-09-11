"""Regression tests for the v1.5.1 integration-audit fixes.

Covers: mixed-plan split commit (audit finding 1), clarification-turn evidence
grounding and parked-plan replay (finding 2), judge-side archived-target
checks (finding 3), retry double-charging (finding 5), and the
scope=unclear/scope_anchor hole (finding 6).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import memory_judge
import memory_upsert
from memory_store import MemoryStore


def _record(rule_text: str, **overrides):
    value = {
        "name": "fix-audit-rule",
        "description": rule_text,
        "type": "preference",
        "domain": "workflow",
        "scope": "global",
        "scope_anchor": "",
        "condition": "",
        "confidence": "high",
        "rule_text": rule_text,
        "applies_when": "",
        "does_not_apply_when": "(none)",
        "body": rule_text,
    }
    value.update(overrides)
    return value


def _mutation(operation: str, rule_text: str = "", **kwargs):
    return {
        "operation": operation,
        "target_ids": kwargs.pop("target_ids", []),
        "record": _record(rule_text, **kwargs.pop("record_overrides", {}))
        if rule_text
        else {},
        "evidence_spans": kwargs.pop("evidence_spans", []),
        "applicability_evidence": kwargs.pop("applicability_evidence", ""),
        "children": [],
        "reason": "test",
    }


class MixedPlanSplitCases(unittest.TestCase):
    def test_failed_legacy_replay_stays_pending_and_can_retry(self):
        source = 'Always run the linter before pushing. Also handle the other thing.'
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn('legacy-retry', source)
            store.mark_needs_user('legacy-retry', {'judged_generation': 0, 'mutations': [
                _mutation('NEW', 'Always run the linter before pushing.',
                          evidence_spans=['Always run the linter before pushing.']),
                _mutation('NEEDS_USER'),
            ]})
            with mock.patch.object(store, 'commit_plan', side_effect=OSError('interrupted')):
                resolved, replay = memory_upsert._resolve_and_replay(store, ['legacy-retry'], 'answer')
            self.assertEqual(resolved, [])
            self.assertEqual(replay['legacy-retry']['status'], 'replay_failed')
            self.assertEqual(store.get_turn('legacy-retry')['status'], 'needs_user')
            resolved, replay = memory_upsert._resolve_and_replay(store, ['legacy-retry'], 'answer')
            self.assertEqual(resolved, ['legacy-retry'])
            self.assertEqual(store.get_turn('legacy-retry')['status'], 'clarified')
            self.assertEqual(len(store.snapshot()[1]), 1)
            memory_upsert._resolve_and_replay(store, ['legacy-retry'], 'answer')
            self.assertEqual(len(store.snapshot()[1]), 1)

    def test_partial_commit_remains_parked_when_projection_fails(self):
        source = 'Always run the linter before pushing. Also handle the other thing.'
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn('mixed-crash', source)
            def judge(_source, _rules, _context):
                return {'mutations': [
                    _mutation('NEW', 'Always run the linter before pushing.',
                              evidence_spans=['Always run the linter before pushing.']),
                    _mutation('NEEDS_USER'),
                ]}
            with mock.patch.object(MemoryStore, 'project', side_effect=OSError('disk full')), \
                 mock.patch.object(MemoryStore, 'mark_needs_user', side_effect=AssertionError('must be atomic')):
                result = memory_upsert.resolve_turn('mixed-crash', memory_dir=td, judge_func=judge)
            self.assertEqual(result['status'], 'needs_user')
            self.assertEqual(store.get_turn('mixed-crash')['status'], 'needs_user')
            self.assertEqual(len(store.snapshot()[1]), 1)
            with mock.patch.object(memory_judge, 'judge_plan', side_effect=AssertionError('duplicate learning')):
                restored = memory_upsert.resolve_turn('mixed-crash', memory_dir=td)
            self.assertEqual(restored['status'], 'needs_user')
            self.assertIn('committed', restored)

    def test_mixed_plan_commits_clear_mutation_and_parks_only_the_ambiguous_one(self):
        source = "Always run the linter before pushing. Also handle the other thing."
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn("mixed-turn", source)

            def judge(_source, _rules, _context):
                return {
                    "mutations": [
                        _mutation(
                            "NEW",
                            "Always run the linter before pushing.",
                            evidence_spans=[
                                "Always run the linter before pushing."
                            ],
                        ),
                        _mutation("NEEDS_USER"),
                    ],
                    "resolved_turn_keys": [],
                    "reason": "one clear preference, one ambiguous clause",
                }

            result = memory_upsert.resolve_turn(
                "mixed-turn", memory_dir=td, judge_func=judge
            )
            # The turn parks for clarification…
            self.assertEqual(result["status"], "needs_user")
            # …but the clear mutation was committed, not discarded.
            self.assertIn("committed", result)
            _generation, active = store.snapshot()
            self.assertEqual(len(active), 1)
            self.assertIn("linter", active[0]["rule_text"])
            self.assertTrue(store.turn_has_transaction("mixed-turn"))
            # Resolving the clarification later does not lose anything either.
            resolved = store.mark_clarifications_resolved(
                ["mixed-turn"], resolved_by="answer-turn"
            )
            self.assertEqual(resolved, ["mixed-turn"])
            _generation, active = store.snapshot()
            self.assertEqual(len(active), 1)

    def test_pure_needs_user_plan_still_parks_the_whole_turn(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn("pure-needs", "Do the thing when it is done.")

            def judge(_source, _rules, _context):
                return {
                    "mutations": [_mutation("NEEDS_USER")],
                    "resolved_turn_keys": [],
                    "reason": "ambiguous",
                }

            result = memory_upsert.resolve_turn(
                "pure-needs", memory_dir=td, judge_func=judge
            )
            self.assertEqual(result["status"], "needs_user")
            self.assertNotIn("committed", result)
            self.assertFalse(store.turn_has_transaction("pure-needs"))


class ClarificationEvidenceCases(unittest.TestCase):
    def test_answer_turn_may_ground_evidence_in_the_clarified_turns_text(self):
        original = "Use tabs for indentation in this codebase, not spaces."
        answer = "Globally."
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn("ask-turn", original)
            parked = {
                "mutations": [_mutation("NEEDS_USER")],
                "resolved_turn_keys": [],
                "reason": "scope unclear",
            }
            store.mark_needs_user("ask-turn", parked)
            store.ensure_turn(
                "answer-turn",
                answer,
                context_text="",
                clarification_candidates=["ask-turn"],
            )

            def judge(_source, _rules, _context, extra_evidence_sources=None):
                self.assertIn("ask-turn", extra_evidence_sources or {})
                return memory_judge.validate_plan(
                    {
                        "mutations": [
                            _mutation(
                                "NEW",
                                "Use tabs for indentation, not spaces.",
                                evidence_spans=[
                                    "Use tabs for indentation in this codebase, not spaces."
                                ],
                            )
                        ],
                        "resolved_turn_keys": ["ask-turn"],
                        "reason": "answer resolves the clarification",
                    },
                    _source,
                    _rules,
                    strict_evidence=True,
                    extra_evidence_sources=extra_evidence_sources,
                )

            result = memory_upsert.resolve_turn(
                "answer-turn", memory_dir=td, judge_func=judge
            )
            self.assertIn(result["status"], {"committed", "projected"})
            self.assertEqual(result.get("resolved_turn_keys"), ["ask-turn"])
            _generation, active = store.snapshot()
            self.assertEqual(len(active), 1)

    def test_evidence_from_an_unresolved_candidate_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn("other-ask", "Prefer rebase over merge commits.")
            store.mark_needs_user(
                "other-ask",
                {"mutations": [_mutation("NEEDS_USER")], "resolved_turn_keys": []},
            )
            store.ensure_turn("current", "Unrelated new turn.")
            with self.assertRaises(memory_judge.MemoryJudgeError):
                memory_judge.validate_plan(
                    {
                        "mutations": [
                            _mutation(
                                "NEW",
                                "Prefer rebase.",
                                evidence_spans=[
                                    "Prefer rebase over merge commits."
                                ],
                            )
                        ],
                        # NOT resolving other-ask -> its text stays context-only.
                        "resolved_turn_keys": [],
                        "reason": "",
                    },
                    "Unrelated new turn.",
                    [],
                    strict_evidence=True,
                    extra_evidence_sources={
                        "other-ask": "Prefer rebase over merge commits."
                    },
                )


class ParkedPlanReplayCases(unittest.TestCase):
    def test_unknown_or_stale_parked_generation_is_not_replayed(self):
        for judged in (None, 12):
            with self.subTest(judged=judged), tempfile.TemporaryDirectory() as td:
                store = MemoryStore(td)
                store.initialize()
                source = 'Always pin dependency versions. Also that unclear thing.'
                store.ensure_turn('parked', source)
                store.mark_needs_user('parked', {'judged_generation': judged, 'mutations': [
                    _mutation('NEW', 'Always pin dependency versions.',
                              evidence_spans=['Always pin dependency versions.']),
                    _mutation('NEEDS_USER'),
                ]})
                resolved, replays = memory_upsert._resolve_and_replay(store, ['parked'], 'answer')
                self.assertEqual(resolved, [])
                self.assertEqual(replays['parked']['status'], 'replay_needs_review')
                self.assertEqual(store.get_turn('parked')['status'], 'needs_user')
                self.assertEqual(store.snapshot()[1], [])

    def test_legacy_mixed_parked_plan_is_replayed_when_clarification_resolves(self):
        source = "Always pin dependency versions. Also that other unclear thing."
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn("legacy-turn", source)
            # Simulate the pre-fix flow: the WHOLE mixed plan was parked and
            # nothing was committed.
            store.mark_needs_user(
                "legacy-turn",
                {
                    "mutations": [
                        _mutation(
                            "NEW",
                            "Always pin dependency versions.",
                            evidence_spans=["Always pin dependency versions."],
                        ),
                        _mutation("NEEDS_USER"),
                    ],
                    "resolved_turn_keys": [],
                    "reason": "legacy park",
                    "judged_generation": 0,
                },
            )
            self.assertFalse(store.turn_has_transaction("legacy-turn"))
            resolved, replays = memory_upsert._resolve_and_replay(
                store, ["legacy-turn"], resolved_by="answer-turn"
            )
            self.assertEqual(resolved, ["legacy-turn"])
            self.assertIn("legacy-turn", replays)
            self.assertIn(
                replays["legacy-turn"].get("status"), {"committed", "projected"}
            )
            _generation, active = store.snapshot()
            self.assertEqual(len(active), 1)
            self.assertIn("pin dependency versions", active[0]["rule_text"])


class ArchivedTargetJudgeCases(unittest.TestCase):
    def _rules(self, status: str):
        return [
            {
                "atomic_id": "wf-pref-001",
                "status": status,
                "rule_text": "Existing rule.",
                "description": "Existing rule.",
            }
        ]

    def test_update_targeting_archived_rule_fails_at_the_judge(self):
        with self.assertRaisesRegex(
            memory_judge.MemoryJudgeError, "only RESTORE may target"
        ):
            memory_judge.validate_plan(
                {
                    "mutations": [
                        _mutation(
                            "UPDATE",
                            "New text.",
                            target_ids=["wf-pref-001"],
                            evidence_spans=["New text."],
                        )
                    ],
                    "resolved_turn_keys": [],
                },
                "New text.",
                self._rules("archived"),
                strict_evidence=True,
            )

    def test_restore_targeting_active_rule_fails_at_the_judge(self):
        with self.assertRaisesRegex(
            memory_judge.MemoryJudgeError, "not archived"
        ):
            memory_judge.validate_plan(
                {
                    "mutations": [
                        _mutation(
                            "RESTORE",
                            target_ids=["wf-pref-001"],
                            evidence_spans=["Restore wf-pref-001"],
                        )
                    ],
                    "resolved_turn_keys": [],
                },
                "Restore wf-pref-001",
                self._rules("active"),
                strict_evidence=True,
            )

    def test_restore_targeting_archived_rule_passes_the_judge(self):
        validated = memory_judge.validate_plan(
            {
                "mutations": [
                    _mutation(
                        "RESTORE",
                        target_ids=["wf-pref-001"],
                        evidence_spans=["Restore wf-pref-001"],
                    )
                ],
                "resolved_turn_keys": [],
            },
            "Restore wf-pref-001",
            self._rules("archived"),
            strict_evidence=True,
        )
        self.assertEqual(validated["mutations"][0]["operation"], "RESTORE")


class ScopeUnclearAnchorCases(unittest.TestCase):
    def test_judge_rejects_unclear_scope_with_anchor(self):
        with self.assertRaisesRegex(
            memory_judge.MemoryJudgeError, "empty for unclear"
        ):
            memory_judge.validate_plan(
                {
                    "mutations": [
                        _mutation(
                            "NEW",
                            "Some rule.",
                            evidence_spans=["Some rule."],
                            record_overrides={
                                "scope": "unclear",
                                "scope_anchor": "mystery-project",
                            },
                        )
                    ],
                    "resolved_turn_keys": [],
                },
                "Some rule.",
                [],
                strict_evidence=True,
            )

    def test_store_rejects_unclear_scope_with_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn("scope-turn", "Some rule.")
            generation, _active = store.snapshot()
            from memory_store import InvalidPlanError

            with self.assertRaisesRegex(InvalidPlanError, "unclear scope"):
                store.commit_plan(
                    "scope-turn",
                    "Some rule.",
                    {
                        "mutations": [
                            _mutation(
                                "NEW",
                                "Some rule.",
                                evidence_spans=["Some rule."],
                                record_overrides={
                                    "scope": "unclear",
                                    "scope_anchor": "mystery-project",
                                },
                            )
                        ],
                        "resolved_turn_keys": [],
                    },
                    generation,
                )


class RetryAccountingCases(unittest.TestCase):
    def test_drain_charges_one_attempt_per_real_failure(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn("acct-turn", "持久偏好")

            calls = {"n": 0}

            def judge(_source, _rules, _context):
                calls["n"] += 1
                raise RuntimeError("judge always fails")

            # resolve_turn catches the judge failure internally and charges via
            # its claim; simulate the drain-level double-charge path by calling
            # mark_turn_error again without a lease, as drain does on escaped
            # exceptions — with charge_attempt=False it must not add a second
            # increment.
            memory_upsert.resolve_turn("acct-turn", memory_dir=td, judge_func=judge)
            turn = store.get_turn("acct-turn")
            self.assertEqual(turn["attempt_count"], 1)
            store.mark_turn_error(
                "acct-turn",
                "escaped exception",
                max_attempts=5,
                charge_attempt=False,
            )
            turn = store.get_turn("acct-turn")
            self.assertEqual(turn["attempt_count"], 1)
            self.assertEqual(turn["status"], "pending")

    def test_no_lease_error_does_not_stomp_a_live_lease(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            store.initialize()
            store.ensure_turn("lease-turn", "持久偏好")
            owner = store.claim_turn("lease-turn")
            self.assertTrue(owner)
            # Another worker reporting an error without the lease must not
            # flip the actively-leased turn back to pending.
            store.mark_turn_error("lease-turn", "outsider", charge_attempt=False)
            turn = store.get_turn("lease-turn")
            self.assertEqual(turn["status"], "resolving")


if __name__ == "__main__":
    unittest.main()
