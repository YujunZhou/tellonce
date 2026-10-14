"""Explicit learning scope for new experimental libraries and legacy libraries."""
GENERAL = 'general'
CORRECTIONS = 'corrections'
CORRECTION_TYPES = frozenset({'preference', 'friction', 'pitfall'})


def admission_instruction(policy: str) -> str:
    if policy == GENERAL:
        return ('Persist only durable preferences, recurring pitfalls, friction, user facts, '
                'project facts, or reusable references. A one-task instruction is not memory.')
    if policy != CORRECTIONS:
        raise ValueError('unknown learning policy')
    return (
        'Persist only reusable preferences, friction, or pitfalls expressed by the user. '
        'A correction can qualify without an explicit request to remember it. '
        'Apply this distinction BEFORE rejecting a message as task-specific: '
        '(1) a new requested choice or unexplained replacement is ordinary task work; '
        '(2) an explained correction identifies what went wrong in the assistant\'s '
        'process and can support that specific reusable pitfall even after ONE example. '
        'A sentence saying the chosen item fails the requested criteria, omits a '
        'required part, or lacks the stated evidence is not merely a new choice. '
        'Preserve its supported lesson without requiring "always", repetition, '
        'or a separate future-facing sentence. Reject it only if no behavior can '
        'be generalized without adding a requirement not expressed by the user. '
        'Distinguish a one-off choice from a correction that identifies a repeatable error: '
        'a concrete example does not make the underlying pitfall one-off. '
        'For example, "that selection is outside the range I gave; check the range" '
        'can support checking selections against the requested ranges, but not storing '
        'the example selection or treating that range as a standing preference. '
        'Extract only the reusable behavior directly supported by the correction; '
        'do not invent a cause, mandatory tool, extra verification step, or broader ban. '
        'Preserve the kind of claim and corrected relationship while removing incidental '
        'names. In particular, source-attribution fidelity is not a demand that all '
        'deductions, calculations or paraphrases occur verbatim in source text. '
        'Require exact words for direct quotations, and semantic support for paraphrases; '
        'do not apply the quotation standard to every attributed claim. '
        'Verifying a claimed source type is not requiring universally generalizable evidence. '
        'A bare replacement ("use B instead of A"), a new task requirement, or an '
        'explicit one-time exception does not by itself establish a reusable rule. '
        'Do not store task answers, specific factual knowledge, project facts, or reference material. '
        'This applies to the entire record, including body and description: keep '
        'concrete example names, dates, answers and incidental values only in the '
        'source evidence spans, not in the reusable rule record. '
        'Use record.type preference, friction, or pitfall only. '
        'Keep parameters, paths, tool names and thresholds only when they are part '
        'of the actual durable requirement, not an incidental failed example; '
        'generalization must not erase these or invent broader obligations. '
        'For an admitted reusable preference, do not append "if requested" or '
        '"when explicitly asked again" just because this correction is a request. '
        'The future trigger is the supported activity or object, not repetition '
        'of the correction. Preserve a genuinely stated opt-in condition or '
        'one-time limit; do not turn "only when I ask for X" into a default. '
        'If durability is unsupported, save no rule instead of a vacuous promise '
        'to comply only after the same request is repeated. '
        'A new one-task instruction is not memory; an explained error correction '
        'must still be assessed for a reusable pitfall as described above.'
    )


def validate_types(plan: dict, policy: str) -> None:
    admission_instruction(policy)
    if policy == GENERAL:
        return
    def visit(items):
        for item in items:
            if item.get('operation', '').upper() in {'NEW', 'UPDATE', 'SUPERSEDE'}:
                if item.get('record', {}).get('type') not in CORRECTION_TYPES:
                    raise ValueError('experimental memory accepts only preference, friction, or pitfall')
            visit(item.get('children') or [])
    visit(plan.get('mutations') or [])
