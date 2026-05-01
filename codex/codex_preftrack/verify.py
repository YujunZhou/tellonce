from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Verdict:
    verdict: str
    violations: list[dict]


def _chinese_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    chinese = sum(1 for c in chars if "\u4e00" <= c <= "\u9fff")
    return chinese / len(chars)


def _inline_english_tokens(text: str, whitelist: set[str]) -> list[str]:
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9_\-]{2,}\b", text)
    allowed = {w.lower() for w in whitelist}
    return [token for token in tokens if token.lower() not in allowed]


def verify_output(text: str, whitelist: set[str] | None = None) -> Verdict:
    whitelist = whitelist or set()
    violations = []
    if re.search(r"/tmp/[^\s`'\"]+", text):
        violations.append(
            {
                "rule_id": "tool-pit-130",
                "severity": "warn",
                "evidence": ["/tmp path in output"],
                "repair_instruction": "Use a durable project path for important artifacts.",
            }
        )
    inline = _inline_english_tokens(text, whitelist)
    if inline and _chinese_ratio(text) >= 0.35:
        violations.append(
            {
                "rule_id": "lang-pit-130",
                "severity": "warn",
                "evidence": inline[:10],
                "repair_instruction": "Avoid ordinary English filler in Chinese user-facing replies unless whitelisted.",
            }
        )
    return Verdict(verdict="warn_log" if violations else "pass", violations=violations)
