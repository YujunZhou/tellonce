"""Secret redaction for CC-side persisted logs (codex review H2 fix, 2026-05-01).

Mirrors codex/codex_preftrack/ledger.py SECRET_PATTERNS so compliance log /
shadow log / shadow alert markdown don't persist user-pasted API keys, SSH
keys, DB URIs etc. in plaintext.

Two public entry points:
  - redact(value)  — redact a string in place
  - sanitize(obj)  — recursively redact strings inside dict/list/tuple

Why mirror not import: lib/ is the CC distribution; codex/ is a separate
publishable that may diverge. Duplication is intentional and documented.
"""
import re

SECRET_PATTERNS = [
    # SSH / GCP private-key PEM block (multi-line; must run before line-local rules)
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
            re.MULTILINE,
        ),
        "[REDACTED_PRIVATE_KEY_BLOCK]",
    ),
    # OpenAI / Anthropic / project keys
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}"), "[REDACTED_API_KEY]"),
    # GitHub: classic + fine-grained + OAuth flow tokens
    (re.compile(r"\bghp_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_PAT]"),
    (re.compile(r"\bgh[ousr]_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    # Slack
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    # Stripe
    (re.compile(r"\b(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}"), "[REDACTED_STRIPE_KEY]"),
    # Google API key (39-char total: AIza + 35 chars). Lookahead instead of \b
    # so it works when followed by another \w char.
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-])"), "[REDACTED_GOOGLE_API_KEY]"),
    # HuggingFace
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}"), "[REDACTED_HF_TOKEN]"),
    # AWS Access Key ID
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY_ID]"),
    # JWT
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+"), "[REDACTED_JWT]"),
    # Bearer / Authorization headers
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-+=/]{20,}", re.I), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\bAuthorization:\s*[A-Za-z]+\s+[A-Za-z0-9._\-+=/]{20,}", re.I), "Authorization: [REDACTED]"),
    # DB URIs with embedded credentials
    (
        re.compile(r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqp|amqps)://[^\s:@]+:[^\s@]*@[^\s\"'`<>]+", re.I),
        "[REDACTED_DB_URI]",
    ),
    # Common assignment forms
    (
        re.compile(
            r"\b(?:password|passwd|pwd|secret|api[_\-]?key|access[_\-]?key|auth[_\-]?token|client[_\-]?secret|private[_\-]?key)\s*[:=]\s*[^\s,;'\"`)]+",
            re.IGNORECASE,
        ),
        "[REDACTED_SECRET_ASSIGNMENT]",
    ),
]


def redact(value):
    """Redact a single string. Non-string passes through unchanged."""
    if not isinstance(value, str):
        return value
    out = value
    for pattern, replacement in SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def sanitize(value):
    """Recursively redact strings inside dict / list / tuple. Other types pass through."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, tuple):
        return tuple(sanitize(v) for v in value)
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()}
    return value
