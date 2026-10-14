"""The correction lifecycle prompt carries the three scope boundaries validated in the
2026-09-29/30 TRACE diagnosis (task restatement, object-separable SPLIT, actionable-by-assistant),
placed after the generalization check and before the final consistency audit."""
import importlib
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / 'lib'
sys.path.insert(0, str(LIB))
memory_judge = importlib.import_module('memory_judge')
learning_policy = importlib.import_module('learning_policy')

HEADINGS = ('Task-restatement boundary:', 'Object-separable obligations:', 'Actionable-by-the-assistant boundary:',
            'Verification and unmet-requirement boundary:')
AUDIT = 'Final consistency audit before returning the existing JSON schema:'


def _prompt(policy):
    return memory_judge.build_prompt('以后所有 Markdown 正文用中文。', [], 'User: 写一份说明。', policy=policy)


def test_corrections_prompt_has_guidance_once_in_order():
    prompt = _prompt(learning_policy.CORRECTIONS)
    positions = [prompt.index(h) for h in HEADINGS]
    assert all(prompt.count(h) == 1 for h in HEADINGS)
    assert positions == sorted(positions)
    assert prompt.index('Additional generalization check') < positions[0] < prompt.index(AUDIT)
    assert '{scope_guidance}' not in prompt


def test_verification_boundary_names_the_withholding_failure():
    """2026-09-30 MemoryArena pilot: 'do not present unverified items as satisfying' had been learned as
    'keep the inherited entry', which withheld requested changes."""
    prompt = _prompt(learning_policy.CORRECTIONS)
    section = prompt.split('Verification and unmet-requirement boundary:', 1)[1].split('Final consistency audit', 1)[0]
    assert 'does NOT support any standing permission to stop short of a requested' in section
    assert 'Never record "state that it remains unresolved"' in section and '"keep the previous entry"' in section
    assert 'deliver its best' in section and 'verifiable candidate for every requested field' in section


def test_general_policy_prompt_unchanged_by_guidance():
    prompt = _prompt(learning_policy.GENERAL)
    assert not any(h in prompt for h in HEADINGS)
    assert AUDIT not in prompt


def test_three_platform_copies_identical():
    root = Path(__file__).resolve().parents[1]
    reference = (root / 'lib/memory_judge.py').read_bytes()
    for copy in ('codex/shared_lib/memory_judge.py', 'copilot/lib/memory_judge.py'):
        assert (root / copy).read_bytes() == reference, copy

def test_applies_when_must_be_request_decidable():
    """2026-10-01 habit pilot: cards whose applies_when named a process moment ("before starting queries") were
    never selected at the user boundary (3/26), so the learned habit never reached the solver."""
    prompt = _prompt(learning_policy.CORRECTIONS)
    section = prompt.split('Actionable-by-the-assistant boundary:', 1)[1].split('Verification and unmet-requirement boundary:', 1)[0]
    assert "decidable from the user's request alone" in section and 'never a moment inside' in section
    # v5 (2026-10-01): a rule about the assistant's own opening/explanations is not an output kind either; the
    # replayed Chinese-explanations card kept "when writing explanations after the opening" and was selected 2/40.
    assert 'opening,\n  explanations, progress notes' in section and 'takes the request type as its\n  applies_when' in section
    # v6: the v5 replay wrote the Chinese user's cards in English after the English examples were added.
    assert "language of the user's own correction" in section
    # v7: the 2026-10-01 pilot-2 card asked for looked-up values inside the pre-query opening; the compiler refused it.
    assert 'can only be known\n  after a lookup' in section and 'two ordered steps' in section


def test_evidence_judgment_boundary_records_no_rule():
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    block = guidance.split('Evidence-judgment boundary:', 1)[1].split('Verification and unmet-requirement boundary:', 1)[0]
    assert 'records NO standing rule' in block
    assert 'do not list X as supported' in block and 'option B should not have been selected' in block
    assert 'NOOP' in block and 'directly checkable omission' in block
    assert guidance.index('Evidence-judgment boundary:') < guidance.index('Verification and unmet-requirement boundary:')


def test_requirement_conformance_rule_is_stated_on_the_delivered_choice():
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    block = guidance.split('Verification and unmet-requirement boundary:', 1)[1]
    assert 'requirement on the DELIVERED choice' in block
    assert 'verified at the search step' in block and 'never at the delivery' in block
    evidence = guidance.split('Evidence-judgment boundary:', 1)[1].split('Verification and unmet-requirement boundary:', 1)[0]
    assert 'delivered output meeting a condition the request itself states' in evidence


def test_response_parts_are_separate_objects():
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    block = guidance.split('Object-separable obligations:', 1)[1].split('Actionable-by-the-assistant boundary:', 1)[0]
    assert 'Different parts of one response are different objects' in block
    assert 'Keep one policy per part' in block and 'SPLIT instead' in block


def test_stated_format_boundary_forbids_vacuous_rules():
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    block = guidance.split('Stated-format boundary', 1)[1].split('Object-separable obligations:', 1)[0]
    assert 'Record them verbatim in rule_text' in block
    assert 'when the request specifies X, follow the' in block and 'vacuous' in block
    assert 'UPDATE' in block and 'instead of NOOP' in block


def test_anchor_example_and_part_exemption_guidance():
    """2026-10-03 ClawArena round 10 (v26c): the 排除 card's applies_when restated its own obligation and was selected at 1/122
    boundaries; the opening card lost "in the first reply, before any lookup" and could only be checked where no repair reaches;
    the language card's does_not_apply_when named a response part and the compiler parked it as pending."""
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    fmt = guidance.split('Stated-format boundary', 1)[1].split('Object-separable obligations:', 1)[0]
    assert 'position or timing anchor' in fmt and 'Keep the anchor verbatim in' in fmt
    assert 'is an' in fmt and 'instance, not the rule' in fmt
    act = guidance.split('Actionable-by-the-assistant boundary:', 1)[1]
    assert 'must never restate the obligation' in act and 'multiple-choice question' in act
    assert 'does_not_apply_when names a situation' in act and 'never a part of the response' in act


def test_shared_anchor_does_not_merge_independent_obligations():
    """2026-10-03 round 12 (v28): MemoryArena folded the three opening lines, the C-numbered condition list, the per-condition
    reference and the post-lookup values into one card by eight UPDATEs (ClawArena: ten UPDATEs onto fmt-pref-001); the merged
    check failed 131/217 deliveries, 142 repairs chased a different clause each round, and H2/H3 violations rose from 5/9% to 29/32%."""
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    block = guidance.split('Object-separable obligations:', 1)[1].split('Actionable-by-the-assistant boundary:', 1)[0]
    assert 'A shared position anchor does not make obligations one policy' in block
    assert 'each stays its own policy and each repeats the anchor verbatim' in block
    assert 'never an UPDATE that appends a clause' in block


def test_remainder_parts_are_named_by_their_boundary():
    """2026-10-03 round 19 (v35c): the SPLIT child for "keep the rest in English" was written as "Keep the rest of the final answer in
    English." with does_not_apply_when "none"; the compiler parked it as pending ("the rest" has no referent) and the habit went
    unchecked for the round, while round 18 had written "开头几行之后的回答内容使用英语" and compiled it."""
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    block = guidance.split('Object-separable obligations:', 1)[1].split('Actionable-by-the-assistant boundary:', 1)[0]
    assert 'A part defined by what it is not' in block and 'is named by its' in block
    assert 'never a bare "rest" or "remainder"' in block and 'excluded part belongs in rule_text' in block
    assert 'cannot be read without' in block and 'compiles as unresolved and is never checked' in block


def test_labelled_lines_carry_content_in_the_card_and_in_the_check():
    """2026-10-03 MemoryArena round 20 (v38m): the opening card read "恰好写出「目标:」「方法:」「拿不准:」三行"; the solver opened
    with the three bare labels and nothing after them, the delivery check passed 138 of 167 times, and the judge failed H1 in
    24 of 48 units (whole tasks at 6/6, 7/7) because each line must carry its content."""
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    fmt = guidance.split('Stated-format boundary', 1)[1].split('Object-separable obligations:', 1)[0]
    assert 'A labelled line is its label plus what follows it' in fmt
    assert 'every label is followed by its content' in fmt and 'satisfied by three empty lines' in fmt
    import execution_support as es, inspect
    text = inspect.getsource(es._check_instructions) + inspect.getsource(es.check_rule)
    assert 'requirement to write the label AND what' in text and 'followed by nothing, or by a placeholder' in text


def test_trigger_is_no_wider_than_the_corrected_situation_and_the_request_outranks_the_rule():
    """2026-10-04 MemoryArena round 21 (v40m): a correction about one shared meal became "安排与他人同行的餐食时，使用对方实际安排中的
    餐厅"; the checker applied it to every meal of a traveller with companions ("the meal preferences do not explicitly replace
    the requirement to use Jeffrey's restaurants"), 63 of 182 checks failed, five gold-matching dinners were replaced and the
    injected card made first drafts copy companions' restaurants (native 44/86 vs 62/86 the round before; round 19 had written
    "当请求要求与他人共进某餐时" and passed 135 of 140)."""
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    act = guidance.split('Actionable-by-the-assistant boundary:', 1)[1]
    assert 'The trigger is never wider than the corrected situation' in act
    assert 'Generalise the' in act and 'keep the trigger' in act
    import execution_support as es, inspect
    text = inspect.getsource(es._check_instructions) + inspect.getsource(es.check_rule)
    assert 'The current request outranks a learned rule' in text
    assert 'where the request states its own conditions for an item' in text and "trigger for that item" in text


def test_task_stated_requirements_are_not_user_rules():
    """2026-10-04 user decision after ClawArena round 23: the scenario's own "methods and evidence first" request, repeated
    by the corrector, was stored as a global format card and contradicted the profile's 结论/依据 habit. A requirement the
    task states is not a preference of the user and is never recorded."""
    from memory_judge import LIFECYCLE_SCOPE_GUIDANCE as guidance
    fmt = guidance.split('Stated-format boundary', 1)[1].split('Object-separable obligations:', 1)[0]
    assert 'belongs to that task, not to the user' in fmt
    assert 'records nothing about it' in fmt and 'record the habit only' in fmt



def test_update_declares_what_it_carries_over_and_drops_nothing_unretracted():
    """2026-10-04 MemoryArena round 24 (v44m): the stored card "开始查询前，写完开场的三行后，逐条列出条件，并按 C1、C2 等形式编号" was
    UPDATEd by the correction "请把这些需要分别核对的条件拆开编号" into "列出需要分别核对的条件时，将它们拆开，并按 C1、C2 等形式逐条编号":
    the obligation to list the conditions and its timing anchor were gone, the habit failed 38 of 41 judged test units.
    A refinement keeps the stored rule; an UPDATE declares the stored requirements and may drop one only with the user's words."""
    prompt = _prompt(learning_policy.CORRECTIONS)
    assert 'refines that obligation; it does not' in prompt and 'a requirement the user did not retract is never dropped' in prompt
    assert '"carried_over"' in prompt
    check = memory_judge._validate_carried_over
    stored = {'fmt-pref-002': {'rule_text': '开始查询前，逐条列出条件并编号', 'status': 'active'}}
    turn = '请把这些需要分别核对的条件拆开编号，后面也按编号说明。以后不用写拿不准那一行了。'
    args = (['fmt-pref-002'], turn, 'mutations[0]', stored, True)
    import pytest
    with pytest.raises(memory_judge.MemoryJudgeError, match='carried_over is required on UPDATE'):
        check({}, 'UPDATE', *args)
    with pytest.raises(memory_judge.MemoryJudgeError, match='existing_requirements'):
        check({'carried_over': {'existing_requirements': [], 'dropped': []}}, 'UPDATE', *args)
    with pytest.raises(memory_judge.MemoryJudgeError, match='exact user quotation retracting it'):
        check({'carried_over': {'existing_requirements': ['查询前列出条件'], 'dropped': [
            {'requirement': '查询前列出条件', 'user_quote': '不用再列条件了'}]}}, 'UPDATE', *args)
    kept = check({'carried_over': {'existing_requirements': ['开始查询前', '逐条列出条件', '按 C1、C2 编号'], 'dropped': []}}, 'UPDATE', *args)
    assert kept == {'existing_requirements': ['开始查询前', '逐条列出条件', '按 C1、C2 编号'], 'dropped': []}
    dropped = check({'carried_over': {'existing_requirements': ['写拿不准一行'], 'dropped': [
        {'requirement': '写拿不准一行', 'user_quote': '以后不用写拿不准那一行了'}]}}, 'UPDATE', *args)
    assert dropped['dropped'][0]['user_quote'] == '以后不用写拿不准那一行了'
    assert check({}, 'NEW', [], turn, 'mutations[0]', stored, True) is None
    # a target the judge cannot see, or a non-strict caller, keeps the old shape
    assert check({}, 'UPDATE', ['unknown-id'], turn, 'mutations[0]', stored, True) is None
    assert check({}, 'UPDATE', ['fmt-pref-002'], turn, 'mutations[0]', stored, False) is None


def test_new_rule_names_its_nearest_stored_rule_and_never_repeats_its_obligation():
    """2026-10-05 ClawArena (gpt-6-luna, self): eight cards, among them "选择题…第一段开头先写「目标:」「依据文件:」「拿不准:」三行" and
    "撰写公开说明时，在第一段最前面先写「目标:」「依据文件:」「拿不准:」三行", and two "三行之后用英文" cards — the same obligation stored
    again under another trigger; every duplicate is one more check per reply (1,978 checks, 14.8 M prompt tokens on 120 units).
    A NEW names the nearest active rule; the same obligation under another trigger is an UPDATE that widens applies_when."""
    prompt = _prompt(learning_policy.CORRECTIONS)
    assert 'Same obligation, further situation' in prompt and '"nearest_existing"' in prompt
    assert 'which is their union' in prompt
    check = memory_judge._require_new_rule_comparison
    active = {'fmt-pref-001': {'atomic_id': 'fmt-pref-001', 'status': 'active'}, 'old-1': {'atomic_id': 'old-1', 'status': 'archived'}}
    import pytest
    new = {'operation': 'NEW', 'record': {'rule_text': 'x'}}
    check([new], {})                                           # an empty library needs no comparison
    check([new], {'old-1': active['old-1']})                   # archived rules do not count
    with pytest.raises(memory_judge.MemoryJudgeError, match='nearest_existing is required on NEW'):
        check([new], active)
    with pytest.raises(memory_judge.MemoryJudgeError, match='nearest_existing is required on NEW'):
        check([{**new, 'nearest_existing': {'atomic_id': 'old-1', 'same_obligation': False, 'difference': 'd'}}], active)
    with pytest.raises(memory_judge.MemoryJudgeError, match='return an UPDATE of that rule'):
        check([{**new, 'nearest_existing': {'atomic_id': 'fmt-pref-001', 'same_obligation': True, 'difference': ''}}], active)
    with pytest.raises(memory_judge.MemoryJudgeError, match='difference must say'):
        check([{**new, 'nearest_existing': {'atomic_id': 'fmt-pref-001', 'same_obligation': False, 'difference': ' '}}], active)
    ok = {**new, 'nearest_existing': {'atomic_id': 'fmt-pref-001', 'same_obligation': False, 'difference': '排除行，与开场三行无关'}}
    check([ok, {'operation': 'UPDATE'}, {'operation': 'SPLIT', 'children': [ok, {'operation': 'NOOP'}]}], active)
    with pytest.raises(memory_judge.MemoryJudgeError, match=r'mutation 0\.children\[1\]'):
        check([{'operation': 'SPLIT', 'children': [ok, new]}], active)


def test_update_never_empties_the_stored_applicability_boundary():
    """2026-10-05 ClawArena round 27 (v50c): merged rules came out with an empty applies_when and the selector picked the
    opening-lines rule in 70 of 122 replies (117 of 120 the round before); the same-obligation widening must name both
    situations, and intensifiers like 严格只用 need the user's words."""
    import pytest
    prompt = _prompt(learning_policy.CORRECTIONS)
    assert 'it is never emptied' in prompt and 'the same act on the same part of the response' in prompt
    assert 'Do not add an intensifier the user did not say' in prompt
    stored = {'r1': {'atomic_id': 'r1', 'status': 'active', 'applies_when': '回答选择题时', 'condition': '', 'does_not_apply_when': '(none)'}}
    record = {'rule_text': 'x', 'applies_when': '', 'condition': '', 'does_not_apply_when': '(none)'}
    turn = '写公开说明时也要先写三行。'
    with pytest.raises(memory_judge.MemoryJudgeError, match='Widen applies_when'):
        memory_judge._validate_applicability_evidence({'applicability_evidence': '写公开说明时也要先写三行'}, 'UPDATE', ['r1'], record,
                                                       turn, 'mutations[0]', stored, ['写公开说明时也要先写三行'], True)
    widened = {**record, 'applies_when': '回答选择题或写公开说明时'}
    assert memory_judge._validate_applicability_evidence({'applicability_evidence': '写公开说明时也要先写三行'}, 'UPDATE', ['r1'], widened,
                                                          turn, 'mutations[0]', stored, ['写公开说明时也要先写三行'], True)
