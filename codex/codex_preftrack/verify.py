from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Verdict:
    verdict: str
    violations: list[dict]


# CX-15 fix: provide a base whitelist + user-extension contract instead of
# always running with an empty set (which made every Chinese reply containing
# any English token noise \u2014 e.g. `def`, `http`, `json`, `API`, file paths).
_BASE_WHITELIST: frozenset[str] = frozenset({
    # programming-ubiquitous tokens (case-insensitive)
    "api", "json", "yaml", "toml", "xml", "html", "css", "js", "ts",
    "url", "uri", "http", "https", "ftp", "ssh", "tcp", "udp",
    "cli", "gui", "ide", "sdk", "ci", "cd", "qa", "ux",
    "def", "var", "let", "const", "return", "import", "export",
    "true", "false", "null", "none",
    # AI/ML
    "ai", "ml", "llm", "gpt", "claude", "anthropic", "openai", "codex",
    # cloud / infra commonly referenced
    "aws", "gcp", "azure", "k8s", "docker", "git", "github", "gitlab",
    # python-style
    "pip", "npm", "yarn", "uv",
    # filesystem terms that are language-of-art in mixed-zh prose
    "path", "dir", "file", "pwd", "cwd", "env",
    # token symbols too short to filter individually but caught by the {2,} rule
})


def load_default_whitelist() -> set[str]:
    """Build the runtime whitelist: base set \u222a user-extension file (one token per line).

    User-extension file lookup order:
      1. CODEX_PT_WHITELIST env var (path to a file)
      2. <state_root>/whitelist.txt (resolved via paths.load_registration \u2014
         but verify.py doesn't know the project root, so this is loaded by
         wrapper.py at call time, not here)

    Comments (#-prefixed lines) and blank lines are ignored.
    """
    out: set[str] = {w.lower() for w in _BASE_WHITELIST}
    extra_path = os.environ.get("CODEX_PT_WHITELIST", "").strip()
    if extra_path:
        try:
            with open(extra_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    out.add(line.lower())
        except OSError:
            pass
    return out


def merge_state_whitelist(base: set[str], state_root: Path) -> set[str]:
    """Add user-edited whitelist tokens from <state_root>/whitelist.txt.
    Called by wrapper.py once it knows the state root.
    """
    user_path = state_root / "whitelist.txt"
    if not user_path.is_file():
        return base
    out = set(base)
    try:
        with open(user_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                out.add(line.lower())
    except OSError:
        pass
    return out


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
    if whitelist is None:
        whitelist = load_default_whitelist()
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
