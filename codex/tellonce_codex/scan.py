from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from .ledger import append_event, event_id, sanitize


@dataclass(frozen=True)
class ScanResult:
    signal_type: str
    event_id: str


def classify(message: str) -> str:
    """Heuristic classifier — kept simple on purpose.

    HX-6 fix: removed the single-character friction trigger that appeared
    as a substring inside many unrelated common words, which made friction
    over-fire on entirely unrelated messages. Replaced with disambiguating
    multi-character forms.
    """
    lowered = message.lower()
    # Heuristic seed tokens, language-neutral. We ship both English and Chinese
    # forms so the classifier fires for English-only and Chinese-speaking users
    # alike (the maintainer is a Chinese speaker; the public release targets
    # English users too). These are deliberately small, illustrative seed sets —
    # users extend behavior via their own fingerprints, not by editing this list.
    preference_zh = ["以后", "以后都", "必须", "不要", "我希望", "我想"]
    preference_en = ["from now on", "always", "never", "please don't", "i prefer", "i want", "make sure"]
    if any(token in message for token in preference_zh) or any(token in lowered for token in preference_en):
        return "preference"
    pitfall_en = ["again", "don't repeat", "frustrat"]
    if any(token in lowered for token in pitfall_en) or "又" in message:
        return "pitfall"
    friction_zh = ["很卡", "好卡", "太卡", "卡顿", "麻烦", "太慢", "血压"]
    friction_en = ["slow", "laggy", "annoying", "tedious", "keeps happening", "every time"]
    if any(token in message for token in friction_zh) or any(token in lowered for token in friction_en):
        return "friction"
    return "none"


def scan_message(state_root: Path, message: str, session_id: str = "codex-current", turn_id: int = 0) -> ScanResult:
    signal = classify(message)
    excerpt = sanitize(message[:200])
    eid = event_id(session_id)
    append_event(
        state_root,
        {
            "event_id": eid,
            "event_type": "scan_recorded",
            "session_id": session_id,
            "turn_id": turn_id,
            "payload": {
                "trigger": {
                    "excerpt_redacted": excerpt,
                    "content_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
                },
                "detection": {"signal_type": signal, "detected": signal != "none"},
            },
        },
    )
    return ScanResult(signal_type=signal, event_id=eid)
