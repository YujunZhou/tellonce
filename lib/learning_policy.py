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
        'Do not store task answers, specific factual knowledge, project facts, or reference material. '
        'Use record.type preference, friction, or pitfall only. '
        'Keep parameters, paths, tool names and thresholds that are part of the actual requirement; '
        'generalization must not erase these or invent broader obligations. '
        'A one-task instruction is not memory.'
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
