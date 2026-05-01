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

    HX-6 fix: removed single-character "卡" trigger. "卡" appears in
    "卡尔", "信用卡", "打卡", "卡夫卡", etc. and made friction over-fire
    on entirely unrelated messages. Replaced with disambiguating
    multi-character forms.
    """
    lowered = message.lower()
    if any(token in message for token in ["以后", "以后都", "必须", "不要", "我希望", "我想"]):
        return "preference"
    if any(token in lowered for token in ["again", "don't repeat", "frustrat"]) or "又" in message:
        return "pitfall"
    friction_tokens = ["很卡", "好卡", "太卡", "卡顿", "麻烦", "太慢", "血压"]
    if any(token in message for token in friction_tokens):
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
